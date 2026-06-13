from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Optional

from backend.config import price_for
from backend.domain.ids import new_id, now_iso
from backend.storage.db import cursor

logger = logging.getLogger("nova")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


def log_event(event: str, **fields) -> None:
    """Structured JSON log line to stdout."""
    logger.info(json.dumps({"ts": now_iso(), "event": event, **fields}))


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def set(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


@contextmanager
def track(agent: str, model: str, shipment_id: Optional[str]) -> Iterator[Usage]:
    """Time a call, capture usage, persist an agent_runs row, log it.

    Re-raises on error after recording a failed row (fail loud, not silent).
    """
    usage = Usage()
    start = time.perf_counter()
    status, err = "ok", None
    try:
        yield usage
    except Exception as e:  # noqa: BLE001 — record then re-raise
        status, err = "error", f"{type(e).__name__}: {e}"
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        in_price, out_price = price_for(model)
        cost = (usage.tokens_in / 1_000_000) * in_price + (
            usage.tokens_out / 1_000_000
        ) * out_price
        with cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs(id, shipment_id, agent, model, tokens_in, "
                "tokens_out, cost_usd, latency_ms, status, error, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("run"), shipment_id, agent, model,
                    usage.tokens_in, usage.tokens_out, round(cost, 8),
                    latency_ms, status, err, now_iso(),
                ),
            )
        log_event(
            "agent_call", agent=agent, model=model, shipment_id=shipment_id,
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=round(cost, 6), latency_ms=latency_ms, status=status, error=err,
        )
