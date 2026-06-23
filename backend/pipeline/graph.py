from __future__ import annotations

import sqlite3
from typing import Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.agents.extractor import extract_document
from backend.agents.router import decide
from backend.agents.validator import cross_validate, validate_extraction
from backend.config import get_config
from backend.domain.models import DocumentRef
from backend.domain.schemas import (
    CrossValidationResult,
    ExtractionResult,
    ValidationResult,
)
from backend.llm.client import LLMClient
from backend.llm.tracing import log_event
from backend.pipeline.state import PipelineState, doc_from_dict, doc_to_dict
from backend.storage import repo


# Nodes — each is small, typed, and fail-loud (errors -> state.errors).
def _extractor_node(state: PipelineState) -> dict:
    client = LLMClient()
    extractions: list[dict] = []
    errors = list(state.get("errors", []))
    for d in state["documents"]:
        doc: DocumentRef = doc_from_dict(d)
        try:
            ext = extract_document(doc, client)
            extractions.append(ext.model_dump(mode="json"))
        except Exception as e:  # noqa: BLE001 — fail loud, don't swallow
            msg = f"extractor failed for {doc.filename}: {type(e).__name__}: {e}"
            errors.append(msg)
            log_event("node_error", node="extractor", shipment_id=state["shipment_id"], error=msg)
    return {"extractions": extractions, "errors": errors}


def _validator_node(state: PipelineState) -> dict:
    ruleset = repo.get_ruleset(state["ruleset_id"])
    if ruleset is None:
        return {"errors": state.get("errors", []) + [f"ruleset {state['ruleset_id']} not found"]}
    validations: list[dict] = []
    errors = list(state.get("errors", []))
    for e in state["extractions"]:
        try:
            ext = ExtractionResult(**e)
            val = validate_extraction(ext, ruleset)
            validations.append(val.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001
            msg = f"validator failed: {type(exc).__name__}: {exc}"
            errors.append(msg)
            log_event("node_error", node="validator", shipment_id=state["shipment_id"], error=msg)
    return {"validations": validations, "errors": errors}


def _cross_validator_node(state: PipelineState) -> dict:
    errors = list(state.get("errors", []))
    try:
        extractions = [ExtractionResult(**e) for e in state.get("extractions", [])]
        cv = cross_validate(extractions)
        if extractions:
            repo.save_cross_validation(cv)
        return {"cross_validation": cv.model_dump(mode="json"), "errors": errors}
    except Exception as exc:  # noqa: BLE001
        msg = f"cross-validator failed: {type(exc).__name__}: {exc}"
        errors.append(msg)
        log_event("node_error", node="cross_validator",
                  shipment_id=state["shipment_id"], error=msg)
        return {"cross_validation": None, "errors": errors}


def _router_node(state: PipelineState) -> dict:
    errors = list(state.get("errors", []))
    validations = [ValidationResult(**v) for v in state.get("validations", [])]
    cv_dict = state.get("cross_validation")
    cross = CrossValidationResult(**cv_dict) if cv_dict else None
    if not validations:
        # Nothing to decide on -> fail loud to human review, never silent-approve.
        repo.set_shipment_status(state["shipment_id"], "needs_review")
        errors.append("no validations produced; routed to human review")
        return {"errors": errors, "decision": None}
    try:
        dec = decide(validations, LLMClient(), cross_validation=cross)
        return {"decision": dec.model_dump(mode="json"), "errors": errors}
    except Exception as e:  # noqa: BLE001
        repo.set_shipment_status(state["shipment_id"], "needs_review")
        errors.append(f"router failed: {type(e).__name__}: {e}")
        log_event("node_error", node="router", shipment_id=state["shipment_id"], error=errors[-1])
        return {"errors": errors, "decision": None}


# Graph assembly + checkpointer
def _build_graph(checkpointer: Optional[SqliteSaver]):
    g = StateGraph(PipelineState)
    g.add_node("extractor", _extractor_node)
    g.add_node("validator", _validator_node)
    g.add_node("cross_validator", _cross_validator_node)
    g.add_node("router", _router_node)
    g.add_edge(START, "extractor")
    g.add_edge("extractor", "validator")
    g.add_edge("validator", "cross_validator")
    g.add_edge("cross_validator", "router")
    g.add_edge("router", END)
    return g.compile(checkpointer=checkpointer)


def _checkpointer_conn() -> sqlite3.Connection:
    cfg = get_config()
    # Same DB file as the app; own connection.
    return sqlite3.connect(cfg.db_abspath, check_same_thread=False)


def run_pipeline(
    shipment_id: str,
    documents: list[DocumentRef],
    customer_id: str,
    ruleset_id: Optional[str] = None,
) -> PipelineState:
    """Run extract -> validate -> route for one shipment."""
    if ruleset_id is None:
        rs = repo.active_ruleset_for_customer(customer_id)
        if rs is None:
            raise RuntimeError(f"No ruleset for customer {customer_id}")
        ruleset_id = rs["id"]

    conn = _checkpointer_conn()
    try:
        checkpointer = SqliteSaver(conn)
        app = _build_graph(checkpointer)
        config = {"configurable": {"thread_id": shipment_id}}
        initial: PipelineState = {
            "shipment_id": shipment_id,
            "customer_id": customer_id,
            "ruleset_id": ruleset_id,
            "documents": [doc_to_dict(d) for d in documents],
            "extractions": [],
            "validations": [],
            "cross_validation": None,
            "decision": None,
            "errors": [],
        }
        log_event("pipeline_start", shipment_id=shipment_id, customer_id=customer_id,
                  documents=len(documents))
        final = app.invoke(initial, config)
        log_event("pipeline_done", shipment_id=shipment_id,
                  decision=(final.get("decision") or {}).get("decision"),
                  errors=len(final.get("errors", [])))
        return final
    finally:
        conn.close()


def resume_pipeline(shipment_id: str) -> PipelineState:
    """Resume an interrupted run from its last checkpoint (invoke with no new
    input)."""
    conn = _checkpointer_conn()
    try:
        checkpointer = SqliteSaver(conn)
        app = _build_graph(checkpointer)
        config = {"configurable": {"thread_id": shipment_id}}
        snapshot = app.get_state(config)
        if not snapshot.values:
            raise RuntimeError(f"No checkpoint found for shipment {shipment_id}")
        log_event("pipeline_resume", shipment_id=shipment_id, next=list(snapshot.next))
        final = app.invoke(None, config)  # None -> continue from checkpoint
        log_event("pipeline_done", shipment_id=shipment_id,
                  decision=(final.get("decision") or {}).get("decision"),
                  errors=len(final.get("errors", [])), resumed=True)
        return final
    finally:
        conn.close()
