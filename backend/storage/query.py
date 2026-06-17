"""Behaviour D — natural-language query over stored output, GROUNDED + SAFE.

Chain: NL question -> LLM -> SQL -> validate -> execute -> answer + rows.

Validation is engine-grade, not regex:
  1. sqlglot AST parse — must be exactly ONE `SELECT`, contain no
     INSERT/UPDATE/DELETE/DDL nodes, and reference only whitelisted tables;
     a LIMIT is injected if missing.
  2. EXPLAIN dry-run on a READ-ONLY connection — SQLite compiles the statement
     (catching syntax / unknown-column / unknown-table errors) WITHOUT executing
     its effects, and the read-only connection makes writes physically impossible.

If validation fails, the error is fed back to the LLM to correct the SQL, for up
to MAX_QUERY_ROUNDS attempts. If it still can't produce a valid, safe query we
say so — we never fabricate an answer.
"""
from __future__ import annotations

import sqlite3

import sqlglot
from sqlglot import exp

from backend.domain.schemas import QueryAnswer
from backend.llm.client import LLMClient
from backend.storage.db import connect

MAX_QUERY_ROUNDS = 2  # initial attempt + correction rounds

_ALLOWED_TABLES = {
    "shipments", "documents", "customers", "rulesets",
    "extractions", "validations", "decisions", "agent_runs",
}
# Statement types that must never appear anywhere in the tree.
_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
    exp.Command,  # raw commands: PRAGMA, VACUUM, ATTACH, etc.
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


def validate_sql(sql: str) -> str:
    """AST-validate the SQL and return a normalised, LIMIT-guarded SELECT.
    Raises UnsafeQuery on any structural violation."""
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as e:  # noqa: BLE001 — parse failure is unsafe/invalid
        raise UnsafeQuery(f"could not parse SQL: {e}")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeQuery("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, exp.Select):
        raise UnsafeQuery("only SELECT statements are allowed")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeQuery(f"disallowed statement type: {type(node).__name__}")

    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if name and name not in _ALLOWED_TABLES:
            raise UnsafeQuery(f"table '{table.name}' is not queryable")

    # Inject a LIMIT if the model forgot one (cost / runaway-result guard).
    if not tree.find(exp.Limit):
        tree = tree.limit(100)

    return tree.sql(dialect="sqlite")


def explain_dry_run(sql: str) -> None:
    """Compile the statement with EXPLAIN on a read-only connection. This catches
    syntax / unknown-column / unknown-table errors WITHOUT running the query.
    Raises sqlite3.Error if the statement is invalid."""
    conn = connect(read_only=True)
    try:
        conn.execute("EXPLAIN " + sql)
    finally:
        conn.close()


def _summarize(rows: list[dict]) -> str:
    if not rows:
        return "No matching records were found."
    if len(rows) == 1 and len(rows[0]) == 1:
        (_, v), = rows[0].items()
        return f"{v}"
    return f"Found {len(rows)} matching record(s)."


def answer_question(question: str, client: LLMClient | None = None) -> QueryAnswer:
    client = client or LLMClient()
    raw_sql: str | None = None
    last_error: str | None = None

    for attempt in range(MAX_QUERY_ROUNDS):
        # 1) generate (or, on a later round, correct) the SQL
        try:
            if attempt == 0:
                raw_sql = client.nl_to_sql(question, SCHEMA_DESC)
            else:
                raw_sql = client.fix_sql(question, SCHEMA_DESC, raw_sql or "", last_error or "")
        except Exception as e:  # noqa: BLE001
            return QueryAnswer(question=question, answer="", grounded=False,
                               error=f"could not generate a query: {e}")

        # 2) validate: AST structural checks, then EXPLAIN dry-run
        try:
            sql = validate_sql(raw_sql)
            explain_dry_run(sql)
        except (UnsafeQuery, sqlite3.Error) as e:
            last_error = f"{type(e).__name__}: {e}"
            continue  # feed the error back to the LLM and try again

        # 3) safe + valid -> execute for real (still read-only)
        conn = connect(read_only=True)
        try:
            rows = [dict(r) for r in conn.execute(sql).fetchall()]
        except Exception as e:  # noqa: BLE001
            last_error = f"execution failed: {e}"
            conn.close()
            continue
        finally:
            conn.close()

        return QueryAnswer(question=question, answer=_summarize(rows),
                           sql=sql, rows=rows, grounded=True)

    return QueryAnswer(
        question=question, answer="", sql=raw_sql, grounded=False,
        error=f"could not produce a valid, safe query after {MAX_QUERY_ROUNDS} "
              f"attempts: {last_error}",
    )
