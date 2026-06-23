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
**refused**, not executed. The NL→SQL is validated with a real SQL parser
(`sqlglot`) + an `EXPLAIN` dry-run, and self-corrects (feeds the error back to the
model) for up to 2 rounds.

---

## Part 2 — Real email workflow (CG inbox)

Part 2 wires the **same** pipeline into a real Cargo-Control (CG) email loop: a
supplier (SU) emails trade documents → Nova fetches them, runs the pipeline, and
hands CG a verification result + a draft reply to review and send. The agents are
unchanged — only the **trigger** is new.

**What it adds**
- **Real email trigger (IMAP + SMTP).** A background poller watches the mailbox;
  when a new email with attachments arrives it fetches it, saves the attachments,
  and enqueues it. An in-process **queue + worker** run the pipeline. Replies go
  out over SMTP — but only when CG clicks send.
- **Multi-document shipments.** One email with several attachments (BOL + Invoice
  + Packing List) becomes one shipment with N documents.
- **Cross-document validation.** Shipment-level fields (consignee, HS code, ports,
  Incoterms, invoice no.) must agree **across all documents**; a disagreement →
  amendment. (See the `conflict_shipment` sample.)
- **CG draft reply.** The Router drafts an approval / amendment / review email
  **deterministically**; CG edits and clicks **Send**. **The agent never sends on
  its own.**
- **Resilient & resumable.** Each node is **idempotent** and records progress in
  `shipments.stage`; a failed run is **re-enqueued (bounded retries)** and
  **resumes from where it left off** — already-extracted documents are reused, so
  the vision model is never re-paid.

**Enable real email** (optional — the app runs fully without it). In `.env` set:
```
EMAIL_USER=you@gmail.com        # the CG mailbox
EMAIL_PASSWORD=<app password>   # Gmail App Password (2FA on) — not your login
```
IMAP/SMTP hosts default to Gmail (override `IMAP_HOST`/`SMTP_HOST` for others). The
poller starts automatically with `make run-api`; you can also run it standalone
with `make run-watcher`. It captures a **baseline at startup**, so it **ignores
your existing backlog and only processes mail that arrives after it starts**, and
it polls continuously (`POLL_INTERVAL_S`, default 30s; `MAX_FETCH_PER_POLL` bounds
each batch; `IMAP_SEARCH` can target a subject, e.g. `(UNSEEN SUBJECT "shipment")`).

**Use the CG Inbox** — the **📥 CG Inbox** tab in the UI shows the four states:
1. **Incoming** — a new email is being processed.
2. **Verification result** — per-document, per-field: match / mismatch / uncertain + confidence.
3. **Discrepancy detail** — *click a flagged field* → found vs expected + the source snippet.
4. **Draft reply** — the editable email; CG reviews and sends.

**No mailbox? Test it instantly:** in the CG Inbox tab click **+ Simulate**, attach
the 3 docs in `samples/emails/conflict_shipment/` (an HS-code conflict across docs)
or `samples/emails/clean_shipment/`, and watch it flow through the same queue →
worker → pipeline to a draft reply.

---

## Testing

One command runs the full functional test suite — it seeds a clean DB and asserts
**every behaviour**: SQL guardrails, cross-document validation, bounded retry,
resume/idempotency, the three decision branches, the multi-doc conflict, router
determinism (no LLM in the decision), and the query layer:
```bash
make test
```
The non-LLM checks run without a key; the LLM checks use `LLM_API_KEY`.

---

## Project layout

```
backend/
  config.py            env, model id, thresholds, email (IMAP/SMTP), retry bounds
  domain/schemas.py    the JSON contracts between agents (pydantic)
  llm/client.py        Gemini client: extract (vision) / nl_to_sql / fix_sql
  llm/tracing.py       wraps every call -> agent_runs (tokens, cost, latency)
  agents/extractor.py  vision -> fields + confidence (3 anti-hallucination layers)
  agents/validator.py  per-doc rules -> match/mismatch/uncertain + cross_validate()
  agents/router.py     decision + reasoning + draft reply — fully deterministic (no LLM)
  pipeline/graph.py    LangGraph extract->validate->cross-validate->route (idempotent)
  storage/db.py        SQLite schema   storage/query.py  NL -> sqlglot+EXPLAIN guarded SELECT
  app.py               FastAPI (pipeline + /inbox + reply endpoints)   cli.py  headless runner
  email_client.py      real IMAP fetch (UID baseline) + SMTP send         <-- Part 2
  watcher.py           IMAP poller -> in-process queue -> worker (+ retry)  <-- Part 2
  ingest/email_inbox.py  email attachments -> multi-doc shipment           <-- Part 2
  ingest/upload.py / base.py   upload ingestor + interface
frontend/src/          React UI: App (tabs) · InboxView (CG inbox) · ShipmentResult (nodes)
samples/               generated docs, ACME ruleset, + emails/ (multi-doc bundles)
scripts/test_all.py    full functional test suite (make test)
docs/                  PRD.md, TECHNICAL_WRITEUP.md
```

---

## How it works (in one paragraph)

A trigger — a UI upload, the CLI, or an **incoming email** (Part 2) — persists a
**shipment + its documents**, then calls `run_pipeline()`. LangGraph runs four
nodes in order: the **Extractor** sends each raw PDF/image to Gemini and gets back
8 fields with confidence + source snippet; the **Validator** (pure Python —
auditable, no LLM) checks each field against the customer's rules as match /
mismatch / **uncertain** (uncertainty always wins, so nothing low-confidence is
silently approved); the **Cross-Validator** checks shipment-level fields agree
**across all documents**; the **Router** picks one of three decisions and writes
the reasoning + draft reply **deterministically** (no LLM). The only LLM call in
the whole pipeline is the extractor (vision). Each node is **idempotent** and
records progress in `shipments.stage`, so a failed run is re-enqueued and
**resumes from where it left off** — extraction is never re-done. Everything is
stored and queryable, and (Part 2) CG reviews the draft and clicks send.

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
