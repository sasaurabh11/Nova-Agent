"""Extractor Agent 

Vision LLM -> structured fields with per-field confidence + source snippet.
Anti-hallucination is enforced in THREE layers:
  1. system prompt: extract only what's visible; not_found over guessing.
  2. schema validation: output must match ExtractionResult or we repair/fail loud.
  3. deterministic format checks: a value that fails its format (HS code, Incoterms,
     gross weight) gets its confidence capped and a warning attached — the model
     cannot "confidently" emit a malformed field.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from backend.domain.models import DocumentRef
from backend.domain.schemas import (
    INCOTERMS_2020,
    REQUIRED_FIELDS,
    ExtractedField,
    ExtractionResult,
    FieldStatus,
)
from backend.llm.client import LLMClient
from backend.llm.tracing import log_event
from backend.storage import repo

_HS_RE = re.compile(r"^\d{4,6}(\.\d{2,4})?$")
_WEIGHT_RE = re.compile(r"\d+(\.\d+)?\s*(kg|kgs|lb|lbs|t|mt|tons?)\b", re.IGNORECASE)
_FORMAT_CAP = 0.40  # WHY: a malformed value can't be high-confidence, no matter the model.


def _apply_format_checks(name: str, f: ExtractedField) -> list[str]:
    """Cap confidence + emit a warning when a found value fails its format rule."""
    if f.status != FieldStatus.found or f.value is None:
        return []
    warnings: list[str] = []
    val = str(f.value).strip()
    bad = None
    if name == "hs_code" and not _HS_RE.match(val):
        bad = "HS code is not in NNNN[.NN] format"
    elif name == "incoterms" and val.upper() not in INCOTERMS_2020:
        bad = f"'{val}' is not a recognised Incoterm code"
    elif name == "gross_weight" and not _WEIGHT_RE.search(val):
        bad = "gross weight is not a number followed by a unit"
    if bad:
        warnings.append(f"{name}: {bad} (confidence capped)")
        f.confidence = min(f.confidence, _FORMAT_CAP)
    return warnings


def _coerce(raw: dict, doc: DocumentRef) -> ExtractionResult:
    """Validate raw model output into ExtractionResult; repair common gaps."""
    fields_in = raw.get("fields", {}) or {}
    fields: dict[str, ExtractedField] = {}
    for name in REQUIRED_FIELDS:
        item = fields_in.get(name)
        if not isinstance(item, dict):
            # Model omitted the field entirely -> treat as not_found (never invent).
            fields[name] = ExtractedField(status=FieldStatus.not_found, confidence=0.0)
            continue
        try:
            fields[name] = ExtractedField(**item)
        except ValidationError:
            fields[name] = ExtractedField(status=FieldStatus.not_found, confidence=0.0)
    return ExtractionResult(
        document_id=doc.document_id,
        shipment_id=doc.shipment_id,
        doc_type=raw.get("doc_type", "unknown"),
        fields=fields,
        model="",  # filled by caller (knows the actual model id)
        warnings=list(raw.get("warnings", []) or []),
    )


def extract_document(doc: DocumentRef, client: LLMClient | None = None) -> ExtractionResult:
    client = client or LLMClient()
    raw = client.extract(doc.read_bytes(), doc.mime, doc.shipment_id)
    result = _coerce(raw, doc)
    result.model = client.cfg.model

    for name, f in result.fields.items():
        result.warnings.extend(_apply_format_checks(name, f))

    # Persist classified doc_type + the extraction itself.
    if result.doc_type and result.doc_type != "unknown":
        repo.set_document_type(doc.document_id, result.doc_type)
    repo.save_extraction(result)

    found = sum(1 for f in result.fields.values() if f.status == FieldStatus.found)
    log_event("extraction_done", shipment_id=doc.shipment_id, document_id=doc.document_id,
              doc_type=result.doc_type, found=found, total=len(REQUIRED_FIELDS),
              warnings=len(result.warnings))
    return result
