# Nova · Multi-Agent Trade Document Pipeline (Part 1)

Takes a trade document (PDF or image) → **extracts** structured fields with
per-field confidence (Gemini vision) → **validates** them against a customer rule
set → **decides** what to do next (auto-approve / flag for review / draft an
amendment) — and **explains** every decision. Verified output is stored in
SQLite and queryable in natural language. A minimal React UI shows the whole
chain running on one real document.

Built on **LangGraph** (stateful graph + SQLite checkpointer for crash survival),
**FastAPI**, and **Google Gemini** (`gemini-2.0-flash`, free tier — one
vision-capable model does extraction, decision prose, and NL→SQL). Every call is
real; there is no mock data or canned response anywhere.

---

## Quick start (laptop, ~2 minutes)

Prerequisites: **Python 3.11+**, **Node 18+**, and a **free Google AI Studio key**
(create one at https://aistudio.google.com/apikey).

```bash
make setup                       # venv + pip install + npm install + creates .env
# open .env and paste your key:   LLM_API_KEY=AIza...
make seed                        # create ACME customer + ruleset, generate samples
make run-api                     # FastAPI on http://localhost:8099   (leave running)
# in a second terminal:
make run-ui                      # Vite UI on  http://localhost:5173
```

Open **http://localhost:5173**. (Or `make build-ui` once, then the backend serves
the UI at **http://localhost:8099/**.)

No `make`? Equivalent commands:
```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # then set LLM_API_KEY
.venv/bin/python -m backend.seed
.venv/bin/python -m uvicorn backend.app:app --port 8099   # then: cd frontend && npm install && npm run dev
```

---

## The 60-second demo path

1. In the UI, keep customer **ACME Imports GmbH**.
2. Upload **`samples/clean/commercial_invoice_acme.pdf`** → watch Extract → Validate
   → Decide light up → **Auto-approved** (all 8 fields match, high confidence).
3. Upload **`samples/clean/commercial_invoice_mismatch.pdf`** → **Amendment
   requested**: the decision card shows an editable draft email listing both
   discrepancies (Incoterms `EXW`, discharge `Rotterdam`). *Send is disabled —
   the agent never sends on its own.*
4. Upload **`samples/messy/commercial_invoice_scan.png`** (a degraded scan) → fields
   the model can't read confidently surface as `uncertain` / `not_found` and the
   shipment is **flagged for review** — never silently approved.
5. In the query box ask **"how many shipments were flagged for review this
   week?"** → grounded answer + the SQL and rows it used.

Prefer the terminal? `make demo` runs all three through the CLI.

> Because extraction is a real LLM call, exact confidence values vary run to run.
> The clean→approve and mismatch→amendment outcomes are deterministic (the values
> are printed clearly); the messy scan demonstrates the uncertainty path.

---

## Sample NL queries (UI query box or `backend.cli query "..."`)

- `how many shipments were flagged for review this week?`
- `show me everything pending review for customer ACME`
- `which fields most often mismatch?`
- `how many shipments were approved?`

Every answer is grounded: it returns the executed SQL and the rows. Unsafe input
(`DELETE…`, multi-statement, non-whitelisted tables) is refused, not run.

---

## What's where

```
backend/
  config.py            env + model + thresholds + price table
  domain/schemas.py    the JSON contracts between agents (pydantic)
  llm/client.py        Gemini client: extract (vision) / compose / nl_to_sql
  llm/tracing.py       wraps every call -> agent_runs (tokens, cost, latency)
  agents/extractor.py  Behaviour A — vision -> fields + confidence (anti-hallucination)
  agents/validator.py  Behaviour B — rules -> match/mismatch/uncertain (+ cross_validate stub)
  agents/router.py     Behaviour C — deterministic decision + LLM-written reasoning/draft
  pipeline/graph.py    LangGraph extract->validate->route + SqliteSaver + run_pipeline()
  storage/query.py     Behaviour D — NL -> guardrailed SELECT -> grounded answer
  app.py               FastAPI — thin HTTP layer over run_pipeline()
  ingest/base.py       BaseIngestor  <-- Part 2 seam (email watcher slots in here)
  cli.py               run the pipeline with no UI
frontend/              Behaviour E — single-page React UI
samples/               generated docs + ACME ruleset
docs/                  PRD.md, TECHNICAL_WRITEUP.md
```

## Crash survival & Part 2 readiness

- **State survives a crash.** LangGraph's `SqliteSaver` checkpoints `PipelineState`
  after every node (keyed by `shipment_id`). `resume_pipeline(shipment_id)`
  continues an interrupted run at the next node — extraction isn't re-run, so the
  costly vision call isn't re-paid.
- **The pipeline is a service, not a screen.** `run_pipeline()` has zero web/UI
  dependency; the UI, the CLI, and a future email watcher are interchangeable callers.
- **Ingestor seam** (`ingest/base.py`) and **multi-document Shipment** model mean
  Part 2's email trigger + multi-attachment shipments are a feature add, not a
  rewrite. `validator.cross_validate(...)` marks where cross-document consistency
  will live. Draft emails are email-shaped (subject + body) for one-edit sending.

See [docs/PRD.md](docs/PRD.md) and [docs/TECHNICAL_WRITEUP.md](docs/TECHNICAL_WRITEUP.md).
