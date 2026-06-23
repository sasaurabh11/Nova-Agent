from __future__ import annotations

from backend.config import get_config
from backend.domain.ids import new_id
from backend.domain.schemas import (
    CrossValidationResult,
    DecisionResult,
    DecisionType,
    Discrepancy,
    DraftEmail,
    FieldVerdict,
    OverallStatus,
    Reply,
    ReplyKind,
    ReplyStatus,
    ValidationResult,
)
from backend.llm.tracing import log_event
from backend.storage import repo

_STATUS_BY_DECISION = {
    DecisionType.auto_approve: "approved",
    DecisionType.flag_for_review: "needs_review",
    DecisionType.request_amendment: "amendment_requested",
}
_REPLY_KIND = {
    DecisionType.auto_approve: ReplyKind.approval,
    DecisionType.flag_for_review: ReplyKind.review,
    DecisionType.request_amendment: ReplyKind.amendment,
}
_SIGNOFF = "Best regards,\nNova Cargo Control Group"


def _label(field: str) -> str:
    return field.replace("_", " ").title()


def _is_cross(d: Discrepancy) -> bool:
    return "identical across" in (d.expected or "")


def _discrepancies(validations: list[ValidationResult], auto_threshold: float) -> list[Discrepancy]:
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


def _cross_discrepancies(cross: CrossValidationResult | None) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    if not cross or cross.consistent:
        return out
    for c in cross.conflicts:
        vals = "; ".join(f"{v['doc_type']}='{v['value']}'" for v in c["values"])
        out.append(Discrepancy(field=c["field"], found=vals,
                               expected="must be identical across all documents",
                               status=FieldVerdict.mismatch))
    return out


def _phrase(d: Discrepancy) -> str:
    if _is_cross(d):
        return f"'{_label(d.field)}' differs across documents ({d.found})"
    if d.status == FieldVerdict.mismatch:
        return f"'{_label(d.field)}' (found {d.found!r}, expected {d.expected})"
    return f"'{_label(d.field)}' is uncertain (found {d.found!r})"


def _reasoning(decision: DecisionType, discs: list[Discrepancy], n_docs: int) -> str:
    if decision == DecisionType.auto_approve:
        return (f"All validated fields across {n_docs} document(s) matched the customer's "
                f"rule set with sufficient confidence, so this shipment was auto-approved.")
    joined = "; ".join(_phrase(d) for d in discs) or "one or more fields need attention"
    if decision == DecisionType.request_amendment:
        return f"Auto-approval blocked: {joined}. An amendment has been requested."
    return f"Auto-approval blocked: {joined}. Flagged for human review."


def _amendment_body(shipment_id: str, discs: list[Discrepancy]) -> str:
    items = []
    for i, d in enumerate(discs, 1):
        if _is_cross(d):
            items.append(f"{i}. {_label(d.field)}: the documents disagree ({d.found}). "
                         f"Please make them consistent across all documents.")
        elif d.status == FieldVerdict.mismatch:
            items.append(f"{i}. {_label(d.field)}: the document shows '{d.found}'; "
                         f"this should be {d.expected}.")
        else:
            items.append(f"{i}. {_label(d.field)}: could not be read with confidence "
                         f"(found {d.found!r}) — please confirm and resend a clearer copy.")
    body = "\n".join(items)
    return (f"Dear Supplier,\n\nThank you for the documents for shipment {shipment_id}. "
            f"Before we can approve them, please correct the following:\n\n{body}\n\n"
            f"Once corrected, please reply with the updated document(s).\n\n{_SIGNOFF}")


def _review_body(shipment_id: str, discs: list[Discrepancy]) -> str:
    items = "\n".join(f"{i}. {_label(d.field)} (found {d.found!r})"
                      for i, d in enumerate(discs, 1))
    return (f"Dear Supplier,\n\nWe are reviewing the documents for shipment {shipment_id}. "
            f"We could not confidently verify the following and may follow up:\n\n{items}\n\n"
            f"No action is required yet.\n\n{_SIGNOFF}")


def _approval_body(shipment_id: str) -> str:
    return (f"Dear Supplier,\n\nThank you for the documents for shipment {shipment_id}. "
            f"They have been validated against the customer's requirements and APPROVED. "
            f"No further action is needed.\n\n{_SIGNOFF}")


# --------------------------------------------------------------------------
def decide(
    validations: list[ValidationResult],
    cross_validation: CrossValidationResult | None = None,
) -> DecisionResult:
    cfg = get_config()
    if not validations:
        raise ValueError("decide() requires at least one ValidationResult")
    shipment_id = validations[0].shipment_id
    auto = cfg.auto_approve_threshold

    discs = _discrepancies(validations, auto) + _cross_discrepancies(cross_validation)
    all_rows = [r for v in validations for r in v.results]
    all_confident = all(r.confidence >= auto for r in all_rows)
    statuses = {v.overall_status for v in validations}
    has_cross_conflict = bool(cross_validation and not cross_validation.consistent)

    if OverallStatus.has_mismatch in statuses or has_cross_conflict:
        decision = DecisionType.request_amendment
    elif OverallStatus.has_uncertain in statuses:
        decision = DecisionType.flag_for_review
    elif all_confident:
        decision = DecisionType.auto_approve
    else:
        decision = DecisionType.flag_for_review

    reasoning = _reasoning(decision, discs, len(validations))

    draft = None
    if decision == DecisionType.request_amendment:
        draft = DraftEmail(subject=f"Amendment required — Shipment {shipment_id}",
                           body=_amendment_body(shipment_id, discs))

    result = DecisionResult(
        shipment_id=shipment_id, decision=decision, reasoning=reasoning,
        requires_human=(decision != DecisionType.auto_approve),
        discrepancies=discs, draft_amendment=draft,
    )

    if not repo.decision_exists(shipment_id):
        repo.save_decision(result)
        _save_reply(decision, shipment_id, discs)
    repo.set_shipment_status(shipment_id, _STATUS_BY_DECISION[decision])

    log_event("decision_done", shipment_id=shipment_id, decision=decision.value,
              requires_human=result.requires_human, discrepancies=len(discs),
              cross_conflict=has_cross_conflict)
    return result


def _save_reply(decision: DecisionType, shipment_id: str, discs: list[Discrepancy]) -> None:
    if decision == DecisionType.auto_approve:
        subject = f"Documents approved — Shipment {shipment_id}"
        body = _approval_body(shipment_id)
    elif decision == DecisionType.request_amendment:
        subject = f"Amendment required — Shipment {shipment_id}"
        body = _amendment_body(shipment_id, discs)
    else:
        subject = f"Please confirm — Shipment {shipment_id} under review"
        body = _review_body(shipment_id, discs)

    email = repo.get_email_by_shipment(shipment_id)
    repo.save_reply(Reply(
        id=new_id("rep"), shipment_id=shipment_id,
        email_id=email["id"] if email else None,
        kind=_REPLY_KIND[decision], subject=subject, body=body,
        status=ReplyStatus.draft,
    ))
