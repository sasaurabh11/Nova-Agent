from __future__ import annotations

import json
from pathlib import Path

from backend.config import get_config
from backend.storage.db import init_db
from backend.storage.repo import upsert_customer, upsert_ruleset

_RULESET_FILE = Path(__file__).resolve().parent.parent / "samples" / "rulesets" / "customer_acme.json"


def seed() -> None:
    cfg = get_config()
    db = Path(cfg.db_abspath)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    init_db()

    spec = json.loads(_RULESET_FILE.read_text())
    upsert_customer(spec["customer_id"], spec["customer_name"])
    upsert_ruleset(spec["ruleset_id"], spec["customer_id"], spec["version"], spec["rules"])
    print(f"Seeded customer {spec['customer_name']!r} with ruleset {spec['ruleset_id']!r}.")

    # Generate sample docs + mock registry if missing.
    try:
        from samples.generate_samples import main as gen

        gen()
    except Exception as e:  # noqa: BLE001
        print(f"(sample generation skipped: {e})")

    print(f"DB ready at {cfg.db_abspath}")


if __name__ == "__main__":
    seed()
