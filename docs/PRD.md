# PRD — Multi-Agent Trade Document Pipeline (Nova, Part 1)

*Execution-oriented. An engineer should be able to read this and start building —
and in fact this repo was built from it.*

---

## 1 | Understanding Nova

**What is Nova? What can it do that traditional SaaS can't?**
Traditional SaaS gives you screens and a database: it *records* what a human
decided and *shows* it back. The human still does the deciding. Nova is the
opposite — it is a governed layer of AI agents that run *on the customer's own
data, inside the customer's existing workflow*, are *triggered by workflow events*
(an email lands, a doc is uploaded), and **do the boring decision work the human
used to do** — extract, validate, decide — handing back not just data but a
recommended action *with its reasoning and source citations*. Traditional SaaS
can't do this because its unit of value is a stored record; Nova's unit of value
is a correct, explained outcome. It treats exceptions as first-class and knows
when to act vs. when to ask a human.

**What is the FDE (Forward Deployed Engineer) model, and why use it for Nova?**
An FDE embeds with the customer, learns the messy real workflow (the rules that
"live in someone's head"), and wires the agents into it — then folds what they
learned back into the product. GoComet uses it because trade workflows aren't
uniform: every customer's rules, document quirks, and exception patterns differ.
You can't capture that from a sales call or a generic config screen. The value is
in the last mile — and the last mile is different for every customer. The FDE
makes the agent fit a real process instead of forcing the process to fit the
software.

**"System of Outcomes" vs. Record vs. Engagement.**
A *System of Record* stores truth (an ERP: "this shipment exists"). A *System of
Engagement* moves work between people (email, a review queue: "look at this").
A **System of Outcomes** *closes the loop*: it decides what should happen next,
executes the safe 80% automatically, routes the exceptional 20% to a human with
context, and learns from every decision trace. The difference is accountability
for the result, not just the data or the hand-off. My pipeline embodies this: it
doesn't just store extracted fields — it *decides* (approve / review / amend),
*explains why*, and surfaces exactly what a human must look at.

---

## 2 | Problem Statement

**Where the current trade-doc validation flow breaks (named failure modes):**
1. **Tribal rules.** Customer requirements live in a veteran CG operator's head;
   a new hire makes mistakes for weeks and rules are applied inconsistently.
2. **Manual field-by-field reading** is slow and error-prone — a transposed HS
   code or a wrong consignee slips through and causes customs holds / penalties.
3. **Silent-approval risk.** Under time pressure, an ambiguous field gets waved
   through. The worst outcome isn't a flagged error — it's an *unflagged* one.
4. **No visibility / audit trail.** Nobody knows how many docs are pending, where
   the bottleneck is, or *why* a given doc was approved when a dispute arises.
5. **Latency stacks up.** Each amendment cycle adds 4–24h; 2–4 cycles is normal.

**Success in a CG operator's first 5 minutes:** they upload a document and within
seconds see (a) every field extracted with a confidence badge, (b) a clear
match/mismatch/uncertain verdict per field against *their* customer's rules, and
(c) a decision — approve, review, or a ready-to-edit amendment email — *with a
plain-English reason*. They trust it because nothing uncertain is hidden, and
they didn't have to read the document field-by-field themselves.

---

## 3 | Users + Jobs-to-be-Done

**Persona A — Priya, CG operator (validator).** Reviews dozens of document sets a
day against per-customer rules. Cares about: not missing an error, not wasting
time on clean docs, and being able to defend a decision later. Non-technical.

**Persona B — Sven, SU shipping coordinator (supplier).** Generates and emails the
docs. Cares about: getting "approved" fast, and knowing *exactly* what to fix when
he doesn't — not a vague "please review."

**JTBDs (testable):**
1. When a clean document arrives, I want it auto-approved without my attention, so
   that I only spend time on documents that actually need a human.
2. When a field is low-confidence or unreadable, I want it flagged and shown to me,
   so that I never unknowingly approve a wrong value.
3. When a document violates a customer rule, I want a ready-to-send amendment that
   lists each discrepancy (field, found, expected), so that I can reply in one edit.
4. When I'm asked about workload, I want to ask "how many docs are pending for
   customer X?" in plain language, so that I get an answer without a BI tool.
5. As an SU, when my submission is rejected, I want a precise field-level fix list,
   so that I correct it once instead of guessing across multiple cycles.

---

## 4 | Agent Architecture *(technical core)*

**Why three agents — not one prompt, not five?**
One giant prompt that "reads the doc, checks rules, and decides" fails on three
axes that matter here: **trust, testability, and cost.** You cannot independently
verify extraction if it's entangled with the decision; you cannot unit-test a rule
change without re-running vision; and you'd pay for the expensive vision model to
also do cheap text reasoning. Splitting into **Extractor / Validator / Router**
gives a clean *planner-free* pipeline where each stage has one job, one contract,
and one failure surface:

- The boundary between **extraction** and **validation** is the boundary between
  *"what does the document say"* (perception, non-deterministic, vision model) and
  *"is that allowed"* (policy, **deterministic**, no LLM). These must be separable
  so rules can change without touching extraction and so validation is auditable.
- The boundary between **validation** and **routing** is *"what's true"* vs.
  *"what do we do about it"* (decision policy + human-facing communication).

Why not five? More agents than there are genuine responsibility boundaries just
adds handoffs, latency, and failure modes. Three maps exactly to the three
irreducible jobs (perceive → check → act). The natural fourth — *cross-document*
validation — is a Part 2 extension of the Validator, not a new agent.

| Agent | Responsibility | Input | Output | Model |
|---|---|---|---|---|
| **Extractor** | Perceive fields from pixels | doc bytes + ids | `ExtractionResult` (8 fields × {value, confidence, status, source_snippet}) | Gemini (vision) |
| **Validator** (verifier) | Check fields vs. customer rules | `ExtractionResult` + ruleset | `ValidationResult` (per-field match/mismatch/uncertain) | none (deterministic) |
| **Router** (executor) | Decide + communicate | `ValidationResult` | `DecisionResult` (one of 3 + reasoning + draft) | Gemini (prose only) |

**How they talk:** structured handoff via validated **pydantic contracts**
(`domain/schemas.py`), carried in a single `PipelineState` that flows through a
LangGraph `StateGraph` (`extractor → validator → router → END`). No shared mutable
memory; each node returns a delta. Every LLM output is validated against its
schema and *repaired-or-failed-loud* — raw model text never flows downstream.

**How state survives a crash mid-pipeline:** LangGraph's **`SqliteSaver`
checkpointer** persists `PipelineState` to the same SQLite file after every node,
keyed by `shipment_id` (the thread id). If the process dies after extraction,
`resume_pipeline(shipment_id)` continues at validation — extraction is *not*
re-run, so we don't re-pay the expensive vision call. (`make crash-demo` proves
it.)

---

## 5 | LLM & Tooling Choices *(every pick defended)*

- **Model: Google Gemini `gemini-2.0-flash`.** One vision-capable model for all
  three LLM tasks. Why: (1) it's **free-tier** — the right call for a POC, with no
  cost ceiling on iteration; (2) it reads **PDFs and images natively**, so there's
  no local rasterisation step and no extra dependency on the critical path; (3)
  `flash` is fast and cheap enough that a separate "cheap text tier" would add
  config surface for marginal benefit at this scale. If extraction quality ever
  needs more, the model id is a single env var (`GEMINI_MODEL`) and the only stage
  that would warrant an upgrade is extraction — so the seam to split tiers later is
  trivial.
- **Bad-quality fallback.** For a degraded scan the extractor still returns valid
  JSON, marks unreadable fields `not_found`/low-confidence, and attaches a
  `warning` — it never throws. Those low-confidence fields then force `uncertain`
  downstream. *Future fallback:* a second higher-resolution pass on only the
  low-confidence fields, or an OCR cross-check.
- **Orchestration: LangGraph.** Chosen deliberately — Nova is built on it, and we
  want exactly its primitives: a typed stateful graph, a **checkpointer for crash
  survival**, and clean node boundaries. A hand-rolled loop would re-implement all
  of this worse.
- **Where I use structured output:** extraction and NL→SQL both request
  `response_mime_type=application/json` and are validated against pydantic
  contracts — raw model text never flows downstream. **Where I avoid it:** the
  *decision branch* is **not** an LLM call — it's computed deterministically in code
  from the validation summary. The LLM only writes the human-readable
  reasoning/draft from facts it's given. A model must never be the thing that
  decides to approve a shipment.

---

## 6 | Trust, Failure Handling & Evals

- **No hallucinated fields.** Three layers: (1) system prompt — *extract only what's
  visible; `not_found` over guessing*; (2) schema validation — output must match the
  contract or we repair/fail; (3) **deterministic format checks** — a value that
  fails its format (HS code pattern, valid Incoterm, numeric weight) has its
  confidence *capped* and a warning added, so a malformed field can't be
  "confident."
- **Low-confidence handling.** A field below `UNCERTAIN_THRESHOLD` (0.70) or
  `not_found` is forced to `uncertain` **regardless of whether the value would
  match**, and always surfaces. Silent approval of uncertain is forbidden *by
  construction* — there is no code path from `uncertain` to `auto_approve`.
- **No runaway loops / cost.** The graph is linear (no cycles). Every LLM call has a
  timeout and **bounded retries** (`MAX_LLM_RETRIES`, exponential backoff). A
  per-shipment wall-clock budget aborts to human review rather than hanging.
- **Fail loud.** Any node error is appended to `state.errors`, flips the shipment to
  `needs_review`, and surfaces in the UI — never swallowed.
- **Evals.**
  - *Offline:* a labelled set of documents with ground-truth fields → measure
    **per-field extraction accuracy** and, more importantly, **confidence
    calibration** (are low-confidence fields actually the wrong ones?) and
    **decision accuracy** vs. a human-labelled gold decision. `scripts/smoke_test.py`
    is the seed of this harness.
  - *Online:* **human-override rate on auto-approvals** — what fraction of
    auto-approved shipments does a human later correct? This is the real measure of
    whether the agent earned its autonomy.

---

## 7 | Metrics & Success Criteria

**North-star (one number, one sentence):**
> **% of documents resolved correctly without human touch** (auto-approved *and*
> not later overturned) — the share of the boring 80% the system actually took off
> the operator's plate without breaking trust.

**Supporting metrics:**
- *Agent quality:* per-field extraction accuracy; confidence calibration (ECE);
  decision accuracy vs. gold; false auto-approve rate (must be ~0).
- *System health:* p50/p95 end-to-end latency; LLM error/retry rate; cost per
  document.
- *Business outcome:* median amendment cycles per shipment (target ↓ from 2–4);
  operator throughput (docs/hour); time-to-first-verdict.

**Go / No-Go for a 2-week single-customer pilot:**
- **Go** if: false auto-approve rate ≤ 1%, extraction accuracy ≥ 95% on the
  customer's real docs, ≥ 60% of clean docs auto-approved, p95 latency < 30s/doc,
  and the operator reports the amendment drafts are sendable with ≤ 1 edit.
- **No-Go** if: any silent wrong approval in the pilot set, or operators distrust
  the confidence scores enough to re-read every field anyway.

---

## 8 | What's Next (after Part 1)

With two more weeks I'd build **cross-document consistency validation** (the
`cross_validate` seam) — because the highest-value real failure is a consignee or
HS code that's individually plausible on each doc but *inconsistent across* the
BOL, Invoice, and Packing List. That's the error a single-doc human reader misses
most and it's the natural bridge to Part 2's multi-attachment shipments. I'd pair
it with a **confidence-calibration eval** on real customer documents, because the
entire trust model rests on low-confidence actually meaning wrong — and that has
to be measured, not assumed.
