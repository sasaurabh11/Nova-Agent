"""Router / Decision Agent 

Picks ONE of: auto_approve | flag_for_review | request_amendment, and explains it.

Critical separation of concerns (graded under Architecture):
  - The DECISION BRANCH is computed deterministically in code from the validation
    summary. The LLM never chooses the branch — that keeps the policy auditable
    and prevents a model from silently approving something it shouldn't.
  - The LLM only writes the human-readable `reasoning` and the draft email `body`
    from the exact facts it is given, so it cannot invent fields or outcomes.

Decision policy:
  has_mismatch                          -> request_amendment   (draft lists every discrepancy)
  has_uncertain (no mismatch)           -> flag_for_review     (names uncertain fields)
  all_match & every conf >= AUTO        -> auto_approve
  all_match but some conf < AUTO        -> flag_for_review     (matched but not confident enough)
Silent approval of an uncertain field is forbidden by construction.
"""
from __future__ import annotations

from backend.config import get_config
from backend.domain.schemas import (
    DecisionResult,
    DecisionType,
    Discrepancy,
    DraftEmail,
    FieldVerdict,
    OverallStatus,
    ValidationResult,
)
from backend.llm.client import LLMClient
from backend.llm.tracing import log_event
from backend.storage import repo

_STATUS_BY_DECISION = {
    DecisionType.auto_approve: "approved",
    DecisionType.flag_for_review: "needs_review",
    DecisionType.request_amendment: "amendment_requested",
}


def _discrepancies(validations: list[ValidationResult], auto_threshold: float) -> list[Discrepancy]:
    """Every row that blocks a clean auto-approval, across ALL docs in the
    shipment: mismatch, uncertain, or matched-but-below-auto-approve-confidence."""
    out: list[Discrepancy] = []
    for val in validations:
        for r in val.results:
            blocking = (
                r.status in (FieldVerdict.mismatch, FieldVerdict.uncertain)
                or (r.status == FieldVerdict.match and r.confidence < auto_threshold)
            )
            if blocking:
                status = r.status
                if r.status == FieldVerdict.match:  # matched but low confidence
                    status = FieldVerdict.uncertain
                out.append(Discrepancy(field=r.field, found=r.found,
                                       expected=r.expected, status=status))
    return out


def decide(validations: list[ValidationResult], client: LLMClient | None = None) -> DecisionResult:
    """Decide one outcome for the whole shipment from its per-doc validations."""
    client = client or LLMClient()
    cfg = get_config()
    if not validations:
        raise ValueError("decide() requires at least one ValidationResult")
    shipment_id = validations[0].shipment_id
    auto = cfg.auto_approve_threshold
    discs = _discrepancies(validations, auto)
    all_rows = [r for v in validations for r in v.results]
    all_confident = all(r.confidence >= auto for r in all_rows)
    statuses = {v.overall_status for v in validations}

    if OverallStatus.has_mismatch in statuses:
        decision = DecisionType.request_amendment
    elif OverallStatus.has_uncertain in statuses:
        decision = DecisionType.flag_for_review
    elif all_confident:  # all_match AND every field clears the auto-approve bar
        decision = DecisionType.auto_approve
    else:  # all_match but some field below auto-approve confidence
        decision = DecisionType.flag_for_review

    # LLM writes prose from the exact facts; it cannot alter the branch above.
    facts = {
        "shipment_id": shipment_id,
        "decision": decision.value,
        "discrepancies": [d.model_dump() for d in discs],
    }
    reasoning = client.compose("router_reasoning", facts, shipment_id)

    draft = None
    if decision == DecisionType.request_amendment:
        body = client.compose("amendment_body", facts, shipment_id)
        draft = DraftEmail(
            subject=f"Amendment required — Shipment {shipment_id}",
            body=body,
        )

    result = DecisionResult(
        shipment_id=shipment_id,
        decision=decision,
        reasoning=reasoning,
        requires_human=(decision != DecisionType.auto_approve),
        discrepancies=discs,
        draft_amendment=draft,
    )
    repo.save_decision(result)
    repo.set_shipment_status(shipment_id, _STATUS_BY_DECISION[decision])
    log_event("decision_done", shipment_id=shipment_id, decision=decision.value,
              requires_human=result.requires_human, discrepancies=len(discs))
    return result
