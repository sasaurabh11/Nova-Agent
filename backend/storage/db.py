from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from backend.config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rulesets (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    version     INTEGER NOT NULL,
    rules_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipments (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    status      TEXT NOT NULL,   -- processing | approved | needs_review | amendment_requested
    source      TEXT NOT NULL,   -- upload | email (email reserved for Part 2)
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    shipment_id TEXT NOT NULL REFERENCES shipments(id),
    doc_type    TEXT NOT NULL,   -- bill_of_lading | commercial_invoice | packing_list | certificate_of_origin | unknown
    filename    TEXT NOT NULL,
    mime        TEXT NOT NULL,
    source      TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    fields_json TEXT NOT NULL,
    model       TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validations (
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL REFERENCES documents(id),
    shipment_id    TEXT NOT NULL REFERENCES shipments(id),
    ruleset_id     TEXT NOT NULL REFERENCES rulesets(id),
    results_json   TEXT NOT NULL,
    overall_status TEXT NOT NULL,   -- all_match | has_mismatch | has_uncertain
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id                 TEXT PRIMARY KEY,
    shipment_id        TEXT NOT NULL REFERENCES shipments(id),
    decision           TEXT NOT NULL,   -- auto_approve | flag_for_review | request_amendment
    reasoning          TEXT NOT NULL,
    discrepancies_json TEXT NOT NULL,
    draft_json         TEXT,
    requires_human     INTEGER NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id          TEXT PRIMARY KEY,
    shipment_id TEXT,
    agent       TEXT NOT NULL,
    model       TEXT NOT NULL,
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,   -- ok | error
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_shipment   ON documents(shipment_id);
CREATE INDEX IF NOT EXISTS idx_extractions_document ON extractions(document_id);
CREATE INDEX IF NOT EXISTS idx_validations_shipment ON validations(shipment_id);
CREATE INDEX IF NOT EXISTS idx_decisions_shipment   ON decisions(shipment_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_shipment  ON agent_runs(shipment_id);
"""


def connect(read_only: bool = False) -> sqlite3.Connection:
    cfg = get_config()
    if read_only:
        # WHY: NL->SQL query layer must never be able to write; enforce at the driver.
        conn = sqlite3.connect(
            f"file:{cfg.db_abspath}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(cfg.db_abspath, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def cursor(read_only: bool = False) -> Iterator[sqlite3.Cursor]:
    conn = connect(read_only=read_only)
    try:
        cur = conn.cursor()
        yield cur
        if not read_only:
            conn.commit()
    finally:
        conn.close()
