from __future__ import annotations

from typing import Optional, TypedDict

from backend.domain.models import DocumentRef


class PipelineState(TypedDict, total=False):
    shipment_id: str
    customer_id: str
    ruleset_id: str
    documents: list[dict]      # serialised DocumentRef (1+; Part 1 passes 1)
    extractions: list[dict]    # serialised ExtractionResult
    validations: list[dict]    # serialised ValidationResult
    decision: Optional[dict]   # serialised DecisionResult
    errors: list[str]


def doc_to_dict(d: DocumentRef) -> dict:
    return {
        "document_id": d.document_id, "shipment_id": d.shipment_id,
        "filename": d.filename, "mime": d.mime, "path": d.path,
        "doc_type": d.doc_type, "source": d.source,
    }


def doc_from_dict(d: dict) -> DocumentRef:
    return DocumentRef(**d)
