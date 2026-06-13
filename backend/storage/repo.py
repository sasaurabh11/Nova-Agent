from __future__ import annotations

import json
from typing import Any, Optional

from backend.domain.ids import new_id, now_iso
from backend.domain.schemas import (
    DecisionResult,
    ExtractionResult,
    ValidationResult,
)
from backend.storage.db import cursor


# --- customers & rulesets -------------------------------------------------
def upsert_customer(customer_id: str, name: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO customers(id, name) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
            (customer_id, name),
        )


def upsert_ruleset(ruleset_id: str, customer_id: str, version: int, rules: dict) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO rulesets(id, customer_id, version, rules_json, created_at) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET rules_json=excluded.rules_json, "
            "version=excluded.version",
            (ruleset_id, customer_id, version, json.dumps(rules), now_iso()),
        )


def get_ruleset(ruleset_id: str) -> Optional[dict]:
    with cursor(read_only=True) as cur:
        row = cur.execute(
            "SELECT * FROM rulesets WHERE id=?", (ruleset_id,)
        ).fetchone()
    if not row:
        return None
    return {**dict(row), "rules": json.loads(row["rules_json"])}


def active_ruleset_for_customer(customer_id: str) -> Optional[dict]:
    with cursor(read_only=True) as cur:
        row = cur.execute(
            "SELECT * FROM rulesets WHERE customer_id=? ORDER BY version DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
    if not row:
        return None
    return {**dict(row), "rules": json.loads(row["rules_json"])}


# --- shipments & documents ------------------------------------------------
def create_shipment(customer_id: str, source: str = "upload") -> str:
    sid = new_id("shp")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO shipments(id, customer_id, status, source, created_at) "
            "VALUES(?, ?, 'processing', ?, ?)",
            (sid, customer_id, source, now_iso()),
        )
    return sid


def set_shipment_status(shipment_id: str, status: str) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE shipments SET status=? WHERE id=?", (status, shipment_id)
        )


def create_document(
    shipment_id: str, filename: str, mime: str, doc_type: str = "unknown",
    source: str = "upload",
) -> str:
    did = new_id("doc")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO documents(id, shipment_id, doc_type, filename, mime, source, received_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (did, shipment_id, doc_type, filename, mime, source, now_iso()),
        )
    return did


def set_document_type(document_id: str, doc_type: str) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE documents SET doc_type=? WHERE id=?", (doc_type, document_id)
        )


# --- agent outputs --------------------------------------------------------
def save_extraction(ext: ExtractionResult) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO extractions(id, document_id, fields_json, model, latency_ms, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                new_id("ext"), ext.document_id,
                ext.model_dump_json(include={"fields", "warnings", "doc_type"}),
                ext.model, ext.latency_ms, now_iso(),
            ),
        )


def save_validation(val: ValidationResult) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO validations(id, document_id, shipment_id, ruleset_id, "
            "results_json, overall_status, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("val"), val.document_id, val.shipment_id, val.ruleset_id,
                val.model_dump_json(include={"results", "summary"}),
                val.overall_status.value, now_iso(),
            ),
        )


def save_decision(dec: DecisionResult) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO decisions(id, shipment_id, decision, reasoning, "
            "discrepancies_json, draft_json, requires_human, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("dec"), dec.shipment_id, dec.decision.value, dec.reasoning,
                json.dumps([d.model_dump() for d in dec.discrepancies]),
                dec.draft_amendment.model_dump_json() if dec.draft_amendment else None,
                int(dec.requires_human), now_iso(),
            ),
        )


# --- composite read for the UI -------------------------------------------
def get_shipment_full(shipment_id: str) -> Optional[dict]:
    """Everything the UI needs for one shipment, in one call."""
    with cursor(read_only=True) as cur:
        ship = cur.execute(
            "SELECT s.*, c.name AS customer_name FROM shipments s "
            "JOIN customers c ON c.id = s.customer_id WHERE s.id=?",
            (shipment_id,),
        ).fetchone()
        if not ship:
            return None
        docs = cur.execute(
            "SELECT * FROM documents WHERE shipment_id=?", (shipment_id,)
        ).fetchall()
        exts = cur.execute(
            "SELECT e.* FROM extractions e JOIN documents d ON d.id=e.document_id "
            "WHERE d.shipment_id=? ORDER BY e.created_at",
            (shipment_id,),
        ).fetchall()
        vals = cur.execute(
            "SELECT * FROM validations WHERE shipment_id=? ORDER BY created_at",
            (shipment_id,),
        ).fetchall()
        dec = cur.execute(
            "SELECT * FROM decisions WHERE shipment_id=? ORDER BY created_at DESC LIMIT 1",
            (shipment_id,),
        ).fetchone()
        runs = cur.execute(
            "SELECT agent, model, tokens_in, tokens_out, cost_usd, latency_ms, status, error "
            "FROM agent_runs WHERE shipment_id=? ORDER BY created_at",
            (shipment_id,),
        ).fetchall()

    def _ext(r: Any) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d.pop("fields_json"))
        return d

    def _val(r: Any) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d.pop("results_json"))
        return d

    cost = sum(r["cost_usd"] for r in runs)
    tokens = sum(r["tokens_in"] + r["tokens_out"] for r in runs)
    latency = sum(r["latency_ms"] for r in runs)

    decision = None
    if dec:
        decision = dict(dec)
        decision["discrepancies"] = json.loads(decision.pop("discrepancies_json"))
        draft = decision.pop("draft_json")
        decision["draft_amendment"] = json.loads(draft) if draft else None
        decision["requires_human"] = bool(decision["requires_human"])

    return {
        "shipment": dict(ship),
        "documents": [dict(d) for d in docs],
        "extractions": [_ext(r) for r in exts],
        "validations": [_val(r) for r in vals],
        "decision": decision,
        "runs": [dict(r) for r in runs],
        "totals": {"cost_usd": round(cost, 6), "tokens": tokens, "latency_ms": latency},
    }


def list_shipments(limit: int = 50) -> list[dict]:
    with cursor(read_only=True) as cur:
        rows = cur.execute(
            "SELECT s.id, s.status, s.source, s.created_at, c.name AS customer_name "
            "FROM shipments s JOIN customers c ON c.id=s.customer_id "
            "ORDER BY s.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
