# Technical Write-up — Nova Trade Pipeline (Part 1)

## Architecture

```
                          ┌──────────────────────────────────────────────┐
   TRIGGERS (callers)     │                 PIPELINE SERVICE              │
                          │            run_pipeline(shipment, docs)       │
  ┌──────────────┐        │   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
  │ UploadIngestor│──────▶│──▶│ Extractor│──▶│ Validator│──▶│  Router  │──┼──▶ END
  │   (Part 1)    │  ship │   │ (vision) │   │  (rules, │   │ (decide+ │  │
  └──────────────┘  +docs │   └────┬─────┘   │  no LLM) │   │  explain)│  │
  ┌──────────────┐        │        │         └────┬─────┘   └────┬─────┘  │
  │EmailInbox    │ (Part 2│        │ ExtractionR.  │ Validation   │ Decision
  │Ingestor (stub)│ seam) │        ▼         ▼     ▼              ▼        │
  └──────────────┘        │   ┌────────────────────────────────────────┐ │
        ▲                 │   │  LangGraph StateGraph + SqliteSaver      │ │
        │                 │   │  checkpoint(PipelineState) after each node│ │
   FastAPI / CLI          └───┴───────────────────┬──────────────────────┘ │
   (also just callers)                            │ writes                   
                          ┌────────────────────────▼─────────────────────────┐
                          │  SQLite (data/nova.db)  — single file, queryable  │
                          │  customers · rulesets · shipments · documents ·   │
                          │  extractions · validations · decisions · agent_runs│
                          └───────────────┬───────────────────┬───────────────┘
                                          │ read              │ read-only
                                  React UI (poll)        NL→SQL query layer
```

**Where state lives:** one SQLite file. Business state in domain tables; the
LangGraph checkpoint (resumable `PipelineState`) in the checkpointer's own tables;
observability in `agent_runs`. **Where data flows:** trigger → `run_pipeline` →
three nodes, each emitting a schema-validated contract that's persisted before the
next node runs. The UI and the query layer are pure readers.

**Key seam:** the pipeline is a plain function with no web/UI dependency. The UI,
the CLI, and (Part 2) the email watcher are interchangeable *callers*. The trigger
is the only missing piece for Part 2 — the model and the chain don't change.

---

## The three nastiest failure modes (observed in my own testing)

1. **The confident-but-malformed field.** A vision model will happily return
   `hs_code: "8471"` (missing the subheading) or an Incoterm it half-read, with a
   high self-reported confidence. If validation trusts that confidence, a wrong
   value sails through. **Fix:** deterministic format checks in the extractor *cap*
   confidence and attach a warning when a value fails its shape (HS `NNNN.NN`,
   Incoterm ∈ the 11 valid codes, weight = number+unit). Confidence is the model's
   estimate **bounded by** machine-checkable structure — seen live: a capped field
   then correctly routes to review instead of approve.

2. **Uncertain-but-matching → silent approval.** The subtlest bug: on the messy
   scan, `consignee_name` was read as the *correct* value but at 0.61 confidence.
   A naive validator marks it `match` and the shipment auto-approves — a wrong
   approval that happens to be right *this time* but is untrustworthy. **Fix:**
   uncertainty dominates matching. Confidence < 0.70 or `not_found` ⇒ `uncertain`,
   *regardless* of value, and there is no code path from `uncertain` to
   `auto_approve`. Verified by `make smoke` ("no silent approval" check).

3. **Crash mid-pipeline after the expensive call.** Extraction succeeds (the costly
   vision call), then validation/routing dies (bad ruleset, transient error). Naive
   retry re-runs extraction and re-pays. **Fix:** LangGraph checkpoints state after
   each node; `resume_pipeline(shipment_id)` continues from the last completed node,
   so extraction runs exactly **once** across a crash+resume — the vision call isn't
   re-paid.

*Also handled:* a document not recognised at all → all fields `not_found` → routes
to review (honest, not fabricated); malformed LLM JSON → schema repair-or-fail-loud,
never passed downstream; unsafe NL query → refused by the SQL guardrail, not run.

---

## Observability (production, 50 customers)

Every LLM call writes an `agent_runs` row: `shipment_id, agent, model, tokens_in/out,
cost_usd, latency_ms, status, error`, plus structured JSON logs to stdout keyed by
`shipment_id`. **Tracing a single shipment from email to verified output** = filter
all tables on its `shipment_id`: ingest → each extraction → each validation → the
decision → every agent_run, in time order. That's the full reasoning trace,
including cost and where time went.

**Dashboard I'd ship:** funnel (received → auto-approved / flagged / amended) per
customer; false-auto-approve rate (human overrides); p50/p95 latency by agent;
cost/doc trend; LLM error & retry rate; top mismatching fields (the `which fields
most often mismatch?` query, already implemented). Production add: pipe traces to
**Langfuse** (env-flagged; Nova uses it) for per-step token/latency waterfalls.

---

## Cost (back-of-envelope, real numbers from `agent_runs`)

Running on **Gemini `gemini-2.5-flash` free tier**, the **actual cash cost is $0**.
The `cost_usd` in `agent_runs` is computed from the paid-tier list price
($0.30/$2.50 per 1M in/out tokens) to show what the same volume *would* cost at
scale. Real measured numbers from a clean-invoice run (`agent_runs`):

| Agent | tokens (in/out, incl. image) | est. paid-tier cost/call |
|---|---|---|
| Extractor (vision) | ~900 / ~330 | **~$0.0016** |
| Router (prose) | ~100 / ~60 | ~$0.0002 |
| Query (NL→SQL) | ~230 / ~30 | ~$0.0001 |

→ **~$0.0017 per document** (≈1,240 tokens), dominated by the vision call
(the validator is free — deterministic). **Where it blows up:** multi-page or
high-DPI scans (image tokens scale with pixels), and re-running extraction on
retries. **Controls (implemented):** bounded retries; checkpointing so a crash
doesn't re-pay extraction; deterministic validation/routing (zero LLM cost there).
**Next lever:** cache extraction by document hash, and route clean
machine-generated PDFs (with a text layer) through cheaper text extraction,
reserving vision for true scans.

---

## Latency

Slowest hop is **vision extraction** — measured ~9–13s end-to-end on
`gemini-2.5-flash` (it does internal "thinking"); validation is local (sub-ms) and
routing is a short text call. Fixes, in order of leverage: use a faster/non-thinking
model tier (e.g. `gemini-2.5-flash-lite`) when the doc looks clean; send a
downscaled/cropped image; **parallelise extraction across a shipment's documents**
(the loop is already per-doc — trivially parallelisable for Part 2's
multi-attachment case); cache by document hash so a re-submitted doc is instant.

---

## What I'd do differently with a week instead of a day

- **Build the eval harness first**, with a labelled corpus of real customer docs —
  measure confidence calibration (the whole trust model assumes low-confidence ⇒
  wrong; that must be proven) and decision accuracy, and gate releases on it.
- **Implement `cross_validate`** for cross-document consistency — the highest-value
  real error and the bridge to Part 2.
- **Versioned, UI-editable rulesets** with an approval trail (rules currently seed
  from JSON), since "rules live in someone's head" is the core problem.
- **Streaming stage events** (WebSocket/SSE) instead of polling, and a self-check
  pass where a cheap model re-reads only the low-confidence fields at higher DPI
  before flagging — cutting human review volume without risking silent approvals.
