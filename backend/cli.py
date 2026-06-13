"""Command-line entry point 
Usage:
  python -m backend.cli run <path-to-document> [--customer cust_acme]
  python -m backend.cli query "how many shipments were flagged this week?"

The `run` command exercises the exact same `run_pipeline` the API and
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.ingest.upload import UploadIngestor
from backend.pipeline.graph import run_pipeline
from backend.storage import repo


def cmd_run(args: argparse.Namespace) -> None:
    path = Path(args.document)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    ingested = UploadIngestor().ingest(args.customer, [(path.name, path.read_bytes())])
    final = run_pipeline(ingested.shipment_id, ingested.documents, args.customer)

    full = repo.get_shipment_full(ingested.shipment_id)
    dec = full["decision"]
    print("\n" + "=" * 64)
    print(f"Shipment   : {ingested.shipment_id}")
    print(f"Status     : {full['shipment']['status']}")
    if dec:
        print(f"Decision   : {dec['decision']}  (requires_human={dec['requires_human']})")
        print(f"Reasoning  : {dec['reasoning']}")
        if dec["draft_amendment"]:
            print("\n--- DRAFT EMAIL (not sent) ---")
            print("Subject:", dec["draft_amendment"]["subject"])
            print(dec["draft_amendment"]["body"])
    if final.get("errors"):
        print("\nERRORS:", final["errors"])
    print(f"\nTotals     : {full['totals']}")
    print("=" * 64)


def cmd_query(args: argparse.Namespace) -> None:
    from backend.storage.query import answer_question

    res = answer_question(" ".join(args.question))
    print(json.dumps(res.model_dump(), indent=2))


def main() -> None:
    p = argparse.ArgumentParser(prog="nova", description="Nova trade pipeline CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="run the pipeline on one document")
    pr.add_argument("document")
    pr.add_argument("--customer", default="cust_acme")
    pr.set_defaults(func=cmd_run)

    pq = sub.add_parser("query", help="ask a natural-language question over stored output")
    pq.add_argument("question", nargs="+")
    pq.set_defaults(func=cmd_query)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
