"""LLM client — Google Gemini (free tier, vision-capable).

Exposes TASK-level methods, not raw completions, so the agents stay logic-only:
  - extract()    : document (PDF/image) -> structured fields   (Extractor)
  - compose()    : structured facts -> prose                    (Router reasoning / draft)
  - nl_to_sql()  : NL question -> a single SELECT               (Query layer)

Gemini accepts PDFs and images natively (no local rasterisation needed), does
both vision and text, and the free tier needs only a Google AI Studio key. Every
call is wrapped in tracing.track() -> one agent_runs row (tokens/cost/latency).
"""
from __future__ import annotations

import json
import time
from typing import Optional

from google import genai
from google.genai import types

from backend.config import Config, get_config
from backend.domain.schemas import REQUIRED_FIELDS
from backend.llm.tracing import Usage, track

_EXTRACTION_SYSTEM = (
    "You are a meticulous trade-document data extractor. Extract ONLY fields that "
    "are visibly present in the document. Rules:\n"
    "- For each requested field return: value, confidence (0..1), status "
    "('found' or 'not_found'), source_snippet (verbatim text you actually saw), page.\n"
    "- If a field is NOT present, set status='not_found', value=null, confidence=0.0. "
    "NEVER guess or invent a plausible value.\n"
    "- Lower confidence when text is blurry, ambiguous, or partially legible.\n"
    "- Also classify doc_type as one of: bill_of_lading, commercial_invoice, "
    "packing_list, certificate_of_origin, unknown.\n"
    "Return STRICT JSON only, no markdown fences."
)


def _extraction_prompt() -> str:
    fields = ", ".join(REQUIRED_FIELDS)
    return (
        f"Extract these fields: {fields}.\n"
        'Return JSON shaped exactly as: {"doc_type": "...", "fields": '
        '{"<field>": {"value": <str|null>, "confidence": <float>, '
        '"status": "found"|"not_found", "source_snippet": <str|null>, '
        '"page": <int|null>}}, "warnings": [<str>]}'
    )


class LLMClient:
    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or get_config()
        if not self.cfg.api_key:
            raise RuntimeError(
                "Missing LLM_API_KEY. Create a free Google AI Studio key at "
                "https://aistudio.google.com/apikey and set it in .env."
            )
        self._client = genai.Client(
            api_key=self.cfg.api_key,
            http_options=types.HttpOptions(timeout=self.cfg.llm_timeout_s * 1000),
        )

    def _retry(self, fn):
        """Bounded retries w/ exponential backoff — no infinite loops/cost."""
        last = None
        for attempt in range(self.cfg.max_llm_retries + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < self.cfg.max_llm_retries:
                    time.sleep(0.5 * (2 ** attempt))
        raise last  # exhausted -> fail loud

    def _record(self, usage: Usage, resp) -> None:
        meta = getattr(resp, "usage_metadata", None)
        if meta:
            usage.set(meta.prompt_token_count or 0, meta.candidates_token_count or 0)

    # TASK: extraction (vision)
    def extract(self, doc_bytes: bytes, mime: str, shipment_id: str) -> dict:
        with track("extractor", self.cfg.model, shipment_id) as usage:
            contents = [
                _extraction_prompt(),
                types.Part.from_bytes(data=doc_bytes, mime_type=mime),
            ]

            def call():
                return self._client.models.generate_content(
                    model=self.cfg.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_EXTRACTION_SYSTEM,
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )

            resp = self._retry(call)
            self._record(usage, resp)
            return json.loads(resp.text)

    # TASK: compose prose from STRUCTURED FACTS (cannot change the decision)
    def compose(self, task: str, facts: dict, shipment_id: str) -> str:
        with track("router", self.cfg.model, shipment_id) as usage:
            system = (
                "You write concise, professional text for a cargo-validation operator. "
                "You are GIVEN the facts and the decision — never change them, never "
                "invent fields. Use ONLY what is provided."
            )
            instruction = {
                "router_reasoning": "Write 1-3 sentences explaining the decision, naming "
                "the specific fields that drove it (with found vs expected). If "
                "cross_document_conflict is true, say the documents disagree with each other.",
                "amendment_body": "Write a polite amendment-request email body to the supplier "
                "listing each discrepancy as a numbered item: field, what the doc shows, what "
                "is required. If a discrepancy says values differ across documents, ask them "
                "to make the documents consistent.",
                "approval_body": "Write a short, professional email body to the supplier "
                "confirming the documents passed validation and are approved. No action needed.",
            }[task]
            prompt = f"{instruction}\n\nFACTS (JSON):\n{json.dumps(facts, indent=2)}"

            def call():
                return self._client.models.generate_content(
                    model=self.cfg.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system, temperature=0.2,
                    ),
                )

            resp = self._retry(call)
            self._record(usage, resp)
            return (resp.text or "").strip()

    # TASK: NL -> a single SELECT statement
    def nl_to_sql(self, question: str, schema_desc: str, shipment_id=None) -> str:
        with track("query", self.cfg.model, shipment_id) as usage:
            system = (
                "You translate a question into ONE read-only SQLite SELECT statement. "
                "Rules: SELECT only; no INSERT/UPDATE/DELETE/DDL; a single statement; "
                "always include a LIMIT; use only the given tables/columns. "
                'Return STRICT JSON: {"sql": "<select ...>"}.'
            )
            prompt = f"SCHEMA:\n{schema_desc}\n\nQUESTION: {question}"

            def call():
                return self._client.models.generate_content(
                    model=self.cfg.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )

            resp = self._retry(call)
            self._record(usage, resp)
            return json.loads(resp.text)["sql"]

    def fix_sql(
        self, question: str, schema_desc: str, bad_sql: str, error: str,
        shipment_id=None,
    ) -> str:
        with track("query", self.cfg.model, shipment_id) as usage:
            system = (
                "You fix a SQLite SELECT that failed validation. Return a corrected "
                "single read-only SELECT (no INSERT/UPDATE/DELETE/DDL, one statement, "
                "include a LIMIT, use only the given tables/columns). "
                'Return STRICT JSON: {"sql": "<select ...>"}.'
            )
            prompt = (
                f"SCHEMA:\n{schema_desc}\n\nQUESTION: {question}\n\n"
                f"PREVIOUS SQL (rejected):\n{bad_sql}\n\nERROR:\n{error}\n\n"
                "Return corrected SQL."
            )

            def call():
                return self._client.models.generate_content(
                    model=self.cfg.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )

            resp = self._retry(call)
            self._record(usage, resp)
            return json.loads(resp.text)["sql"]
