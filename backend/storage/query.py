from __future__ import annotations

import re

from backend.domain.schemas import QueryAnswer
from backend.llm.client import LLMClient
from backend.storage.db import connect

_ALLOWED_TABLES = {
    "shipments", "documents", "customers", "rulesets",
    "extractions", "validations", "decisions", "agent_runs",
    "json_each",  # used for unpacking validation results
}
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)

SCHEMA_DESC = """
Tables (read-only):
  shipments(id, customer_id, status, source, created_at)
    status in: processing, approved, needs_review, amendment_requested
  customers(id, name)
  documents(id, shipment_id, doc_type, filename, mime, source, received_at)
  validations(id, document_id, shipment_id, ruleset_id, results_json, overall_status, created_at)
    results_json is JSON: {"results":[{"field","found","expected","status","confidence","reason"}], ...}
  decisions(id, shipment_id, decision, reasoning, requires_human, created_at)
    decision in: auto_approve, flag_for_review, request_amendment
  agent_runs(id, shipment_id, agent, model, tokens_in, tokens_out, cost_usd, latency_ms, status, created_at)
Join shipments.customer_id = customers.id for customer names.
Use SQLite date funcs, e.g. created_at >= datetime('now','-7 days') for "this week".
"""


class UnsafeQuery(Exception):
    pass


def _guard(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise UnsafeQuery("multiple statements are not allowed")
    if not re.match(r"(?is)^\s*select\b", s):
        raise UnsafeQuery("only SELECT statements are allowed")
    if _FORBIDDEN.search(s):
        raise UnsafeQuery("statement contains a forbidden keyword")
    refs = re.findall(r"(?is)\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", s)
    for t in refs:
        if t.lower() not in _ALLOWED_TABLES:
            raise UnsafeQuery(f"table '{t}' is not queryable")
    if not re.search(r"(?is)\blimit\b", s):
        s += " LIMIT 100"
    return s


def _summarize(question: str, rows: list[dict]) -> str:
    if not rows:
        return "No matching records were found."
    if len(rows) == 1 and len(rows[0]) == 1:
        (k, v), = rows[0].items()
        return f"{v}"
    return f"Found {len(rows)} matching record(s)."


def answer_question(question: str, client: LLMClient | None = None) -> QueryAnswer:
    client = client or LLMClient()
    try:
        raw_sql = client.nl_to_sql(question, SCHEMA_DESC)
    except Exception as e:  # noqa: BLE001
        return QueryAnswer(question=question, answer="", grounded=False,
                           error=f"could not generate a query: {e}")
    try:
        sql = _guard(raw_sql)
    except UnsafeQuery as e:
        return QueryAnswer(question=question, answer="", sql=raw_sql, grounded=False,
                           error=f"refused unsafe query: {e}")

    conn = connect(read_only=True)
    try:
        cur = conn.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        return QueryAnswer(question=question, answer="", sql=sql, grounded=False,
                           error=f"query execution failed: {e}")
    finally:
        conn.close()

    return QueryAnswer(question=question, answer=_summarize(question, rows),
                       sql=sql, rows=rows, grounded=True)
