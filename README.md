# Nova · Multi-Agent Trade Document Pipeline 

A multi-agent system that takes a trade document (PDF or image), **extracts** its
fields with per-field confidence (Gemini vision), **validates** them against a
customer's rule set, and **decides** what to do next — **auto-approve**,
**flag for human review**, or **draft an amendment request** — explaining every
decision. Verified output is stored in SQLite and is queryable in plain English. A
single-page React UI shows the whole pipeline running on one real document.

Three agents, wired with **LangGraph** (+ a SQLite checkpointer so a crashed run
resumes), served by **FastAPI**, using **Google Gemini** (`gemini-2.5-flash`,
free tier — one vision-capable model for extraction, decision prose, and NL→SQL).
**Every call is real — there is no mock data or canned response anywhere.**

```
Extractor  →  Validator  →  Router            (auto-approve / flag / amend)
 (vision)     (rules, code)  (decision + reasoning + draft, all deterministic)
```

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (for the UI)
- A **free Google AI Studio API key** — create one in 30 seconds at
  **https://aistudio.google.com/apikey** (no billing required)

---

## Setup (one time)

```bash
cd Nova-Agent
make setup
```

`make setup` creates a Python virtualenv, installs backend + frontend
dependencies, and copies `.env.example → .env`.

Then **open `.env` and paste your key**:

```
LLM_API_KEY=AIza...your-key...
GEMINI_MODEL=gemini-2.5-flash
```

> **If you get `429 ... limit: 0` or `404 ... model is no longer available`** when
> you run it, your key's project doesn't have quota for that model id. Change
> `GEMINI_MODEL` in `.env` to `gemini-2.5-flash-lite` (or `gemini-flash-latest`)
> and retry. `gemini-2.5-flash` works on most fresh keys.

### Seed the database (one time)

```bash
make seed
```

This creates the demo customer **ACME Imports GmbH** with its rule set, and
generates the sample documents under `samples/`.

---

## Run it

Two terminals:

**Terminal 1 — backend API (port 8099):**
```bash
make run-api
```

**Terminal 2 — frontend UI (port 5173):**
```bash
make run-ui
```

Open **http://localhost:5173**.

> Prefer a single process? Run `make build-ui` once, then just `make run-api` and
> open **http://localhost:8099/** — the backend serves the built UI itself.

---

## 60-second demo

In the UI, keep customer **ACME Imports GmbH** and upload these in turn (watch the
**Extract → Validate → Decide** stepper light up each time):

| Upload | Expected result |
|---|---|
| `samples/clean/commercial_invoice_acme.pdf` | **Auto-approved** — all 8 fields match, high confidence |
| `samples/clean/commercial_invoice_mismatch.pdf` | **Amendment requested** — Incoterms `EXW` and discharge `Rotterdam` violate ACME's rules; an editable draft email lists both |
| `samples/clean/commercial_invoice_incomplete.pdf` | **Flagged for review** — gross weight & invoice number are missing → surfaced as uncertain, never silently approved |
| `samples/messy/commercial_invoice_scan.png` | a degraded scan (one clean + one messy sample, as required) |

Then, in the **query box** at the bottom, ask:
> *how many shipments were flagged for review this week?*

You'll get a grounded answer plus the exact SQL and rows it used.

The draft email's **Send button is intentionally disabled** — the agent never
sends on its own.

---

## Run without the UI (CLI)

The pipeline is a plain function; the UI is just one caller. To prove it runs
headless:

```bash
# one document, full pipeline
.venv/bin/python -m backend.cli run samples/clean/commercial_invoice_mismatch.pdf

# a natural-language query over stored results
.venv/bin/python -m backend.cli query "show me everything pending review for customer ACME"

# or run all three sample docs at once
make demo
```

### Sample queries to try
- `how many shipments were flagged for review this week?`
- `show me everything pending review for customer ACME`
- `which fields most often mismatch?`
- `how many shipments were approved?`

Unsafe input (e.g. `DELETE …`, multiple statements, non-whitelisted tables) is
**refused**, not executed.

---

## Project layout

```
backend/
  config.py            env, model id, thresholds, price table
  domain/schemas.py    the JSON contracts between agents (pydantic)
  llm/client.py        Gemini client: extract (vision) / compose / nl_to_sql
  llm/tracing.py       wraps every call -> agent_runs (tokens, cost, latency)
  agents/extractor.py  vision -> fields + confidence (3 anti-hallucination layers)
  agents/validator.py  rules -> match/mismatch/uncertain (+ cross_validate stub)
  agents/router.py     decision (in code) + reasoning/draft (LLM)
  pipeline/graph.py    LangGraph extract->validate->route + SqliteSaver + run_pipeline()
  storage/db.py        SQLite schema       storage/query.py  NL -> guardrailed SELECT
  app.py               FastAPI (thin layer over run_pipeline)   cli.py  headless runner
  ingest/base.py       Ingestor interface  <-- Part 2 email-trigger seam
frontend/              single-page React UI (Vite + TypeScript)
samples/               generated documents + ACME ruleset (customer_acme.json)
docs/                  PRD.md, TECHNICAL_WRITEUP.md
```

---

## How it works (in one paragraph)

A trigger (the UI upload, the CLI) persists a
**shipment + its documents**, then calls `run_pipeline()`. LangGraph runs three
nodes in order: the **Extractor** sends the raw PDF/image to Gemini and gets back
8 fields each with a confidence and source snippet; the **Validator** (pure
Python — auditable, no LLM) checks each field against the customer's rules and
marks it match / mismatch / **uncertain** (uncertainty always wins, so nothing
low-confidence is silently approved); the **Router** picks one of three decisions
*in code* and writes the reasoning and the draft reply **deterministically** (no
LLM). The only LLM call in the whole pipeline is the extractor (vision). Each node
is **idempotent** and records progress in `shipments.stage`, so a failed run is
re-enqueued and **resumes from where it left off** — extraction is never re-done.
Everything is stored and queryable.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing LLM_API_KEY` | Set `LLM_API_KEY` in `.env`. |
| `429 ... limit: 0` or `404 ... no longer available` | Your key lacks quota for that model — set `GEMINI_MODEL=gemini-2.5-flash-lite` in `.env`. |
| UI says "Cannot reach the API" | Make sure `make run-api` is running on port 8099. |
| `make` not available | Use the manual commands below. |
| A run shows **needs_review** with an error | That's fail-loud behaviour — check the error banner; usually a model/quota issue above. |

### Manual commands (no `make`)
```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                 # then set LLM_API_KEY
.venv/bin/python -m backend.seed
.venv/bin/python -m uvicorn backend.app:app --port 8099
# in another terminal:
cd frontend && npm install && npm run dev
```
