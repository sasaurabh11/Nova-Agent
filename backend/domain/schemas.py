from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# The 8 fields the Extractor must always attempt (assignment minimum bar).
REQUIRED_FIELDS: tuple[str, ...] = (
    "consignee_name",
    "hs_code",
    "port_of_loading",
    "port_of_discharge",
    "incoterms",
    "description_of_goods",
    "gross_weight",
    "invoice_number",
)

INCOTERMS_2020 = {
    "EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP",
    "FAS", "FOB", "CFR", "CIF",
}

DOC_TYPES = {
    "bill_of_lading",
    "commercial_invoice",
    "packing_list",
    "certificate_of_origin",
    "unknown",
}



class FieldStatus(str, Enum):
    found = "found"
    not_found = "not_found"


class ExtractedField(BaseModel):
    value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    status: FieldStatus = FieldStatus.not_found
    source_snippet: Optional[str] = None
    page: Optional[int] = None


class ExtractionResult(BaseModel):
    document_id: str
    shipment_id: str
    doc_type: str = "unknown"
    fields: dict[str, ExtractedField]
    model: str
    latency_ms: int = 0
    warnings: list[str] = Field(default_factory=list)


class FieldVerdict(str, Enum):
    match = "match"
    mismatch = "mismatch"
    uncertain = "uncertain"


class OverallStatus(str, Enum):
    all_match = "all_match"
    has_mismatch = "has_mismatch"
    has_uncertain = "has_uncertain"


class FieldValidation(BaseModel):
    field: str
    found: Optional[str] = None
    expected: str
    status: FieldVerdict
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str


class ValidationSummary(BaseModel):
    match: int = 0
    mismatch: int = 0
    uncertain: int = 0


class ValidationResult(BaseModel):
    document_id: str
    shipment_id: str
    ruleset_id: str
    results: list[FieldValidation]
    summary: ValidationSummary
    overall_status: OverallStatus


class CrossValidationResult(BaseModel):
    """Part 2 seam: consignee/HS-code consistency ACROSS docs in a shipment."""
    shipment_id: str
    consistent: bool
    conflicts: list[dict] = Field(default_factory=list)


class DecisionType(str, Enum):
    auto_approve = "auto_approve"
    flag_for_review = "flag_for_review"
    request_amendment = "request_amendment"


class Discrepancy(BaseModel):
    field: str
    found: Optional[str] = None
    expected: str
    status: FieldVerdict


class DraftEmail(BaseModel):
    subject: str
    body: str


class DecisionResult(BaseModel):
    shipment_id: str
    decision: DecisionType
    reasoning: str
    requires_human: bool
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    draft_amendment: Optional[DraftEmail] = None


class QueryAnswer(BaseModel):
    question: str
    answer: str
    sql: Optional[str] = None
    rows: list[dict] = Field(default_factory=list)
    grounded: bool = True
    error: Optional[str] = None


class CrossConflict(BaseModel):
    """One field that disagrees across a shipment's documents."""
    field: str
    values: list[dict]   # [{"doc_type": "...", "filename": "...", "value": "..."}]


class ReplyKind(str, Enum):
    approval = "approval"
    amendment = "amendment"
    review = "review"


class ReplyStatus(str, Enum):
    draft = "draft"
    sent = "sent"


class Reply(BaseModel):
    """The CG-facing draft reply to the supplier. Editable; CG clicks send.
    The agent NEVER sends on its own."""
    id: str
    shipment_id: str
    email_id: Optional[str] = None
    kind: ReplyKind
    subject: str
    body: str
    status: ReplyStatus = ReplyStatus.draft
    sent_at: Optional[str] = None


class EmailStatus(str, Enum):
    received = "received"      # in the inbox, not yet processed
    processing = "processing"  # the pipeline is running
    verified = "verified"      # processed; a draft reply is ready for CG
    replied = "replied"        # CG sent the reply


class Email(BaseModel):
    """A simulated inbound SU email (the trigger)."""
    id: str
    customer_id: str
    sender: str
    subject: str
    status: EmailStatus
    shipment_id: Optional[str] = None
    received_at: str
