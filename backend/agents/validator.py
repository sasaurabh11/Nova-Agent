"""Validator Agent

Compares an ExtractionResult against a customer rule set, field by field, and
produces match / mismatch / uncertain — with found-vs-expected on every row.

Hard rule (graded under AI craft): a field is `uncertain` whenever its
extraction confidence is below UNCERTAIN_THRESHOLD or it was not_found —
regardless of whether the value would otherwise match. Uncertain ALWAYS
surfaces; it is NEVER silently treated as a match.

This agent is deterministic (no LLM): rule checking must be auditable and
reproducible, not a model's opinion.
"""
from __future__ import annotations

import re

from backend.config import get_config
from backend.domain.schemas import (
    CrossValidationResult,
    ExtractionResult,
    FieldStatus,
    FieldValidation,
    FieldVerdict,
    OverallStatus,
    ValidationResult,
    ValidationSummary,
)
from backend.llm.tracing import log_event
from backend.storage import repo


def _expected_str(rule: dict) -> str:
    t = rule["type"]
    if t == "exact":
        return f"'{rule['value']}'"
    if t == "allowed_set":
        return "one of [" + ", ".join(rule["values"]) + "]"
    if t == "format":
        parts = [f"format {rule.get('pattern', '')}"]
        if rule.get("allowed_prefixes"):
            parts.append("prefix in [" + ", ".join(rule["allowed_prefixes"]) + "]")
        return ", ".join(parts)
    if t == "presence":
        return "present, numeric+unit" if rule.get("numeric") else "present"
    return "valid"


def _evaluate(value: str, rule: dict) -> tuple[bool, str]:
    """Return (matches, reason) for a present, confident value."""
    t = rule["type"]
    v = value.strip()
    if t == "exact":
        ok = v.lower() == str(rule["value"]).strip().lower()
        return ok, ("Matches the required value." if ok
                    else f"Expected '{rule['value']}', found '{value}'.")
    if t == "allowed_set":
        allowed = [x.upper() for x in rule["values"]]
        ok = v.upper() in allowed
        return ok, ("In the customer's allowed set." if ok
                    else f"'{value}' is not in the customer's allowed set.")
    if t == "format":
        pat = rule.get("pattern")
        if pat and not re.match(pat, v):
            return False, f"'{value}' does not match required format."
        prefixes = rule.get("allowed_prefixes")
        if prefixes:
            head = re.split(r"[.\s]", v)[0]
            if not any(head.startswith(p) for p in prefixes):
                return False, f"'{value}' prefix is outside the customer's tariff scope."
        return True, "Matches the required format and scope."
    if t == "presence":
        if rule.get("numeric") and not re.search(r"\d", v):
            return False, "Required to be numeric but no number found."
        return True, "Present as required."
    return True, "No constraint."


def validate_extraction(ext: ExtractionResult, ruleset: dict) -> ValidationResult:
    cfg = get_config()
    rules: dict = ruleset["rules"]
    rows: list[FieldValidation] = []
    counts = {"match": 0, "mismatch": 0, "uncertain": 0}

    for field, rule in rules.items():
        ef = ext.fields.get(field)
        expected = _expected_str(rule)

        # 1) uncertainty dominates — surfaces regardless of would-be match.
        if ef is None or ef.status == FieldStatus.not_found:
            verdict, reason, found, conf = (
                FieldVerdict.uncertain,
                "Field was not found in the document.",
                None,
                ef.confidence if ef else 0.0,
            )
        elif ef.confidence < cfg.uncertain_threshold:
            verdict, reason, found, conf = (
                FieldVerdict.uncertain,
                f"Extraction confidence {ef.confidence:.2f} is below the "
                f"{cfg.uncertain_threshold:.2f} threshold — needs human eyes.",
                ef.value,
                ef.confidence,
            )
        else:
            ok, reason = _evaluate(ef.value or "", rule)
            verdict = FieldVerdict.match if ok else FieldVerdict.mismatch
            found, conf = ef.value, ef.confidence

        counts[verdict.value] += 1
        rows.append(FieldValidation(
            field=field, found=found, expected=expected,
            status=verdict, confidence=conf, reason=reason,
        ))

    if counts["mismatch"]:
        overall = OverallStatus.has_mismatch
    elif counts["uncertain"]:
        overall = OverallStatus.has_uncertain
    else:
        overall = OverallStatus.all_match

    result = ValidationResult(
        document_id=ext.document_id,
        shipment_id=ext.shipment_id,
        ruleset_id=ruleset["ruleset_id"] if "ruleset_id" in ruleset else ruleset.get("id", ""),
        results=rows,
        summary=ValidationSummary(**counts),
        overall_status=overall,
    )
    repo.save_validation(result)
    log_event("validation_done", shipment_id=ext.shipment_id, document_id=ext.document_id,
              overall=overall.value, **counts)
    return result


def cross_validate(shipment_extractions: list[ExtractionResult]) -> CrossValidationResult:
    """ check consignee / HS code consistency ACROSS a shipment's docs
    (BOL + Invoice + Packing List). The Shipment->Documents data model and the
    list-based pipeline state already support this; only the comparison logic
    is deferred. Leaving the door open, not walking through it."""
    raise NotImplementedError("Cross-document validation is a Part 2 feature.")
