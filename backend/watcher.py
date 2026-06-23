"""Email worker: IMAP poller -> in-process queue -> processing worker.

Flow :
  poller thread   : every POLL_INTERVAL_S, fetch UNSEEN mail with attachments,
                    save the attachments, create an `emails` row, ENQUEUE it.
  worker thread   : pull an email_id off the queue, build a (multi-doc) shipment,
                    run the SAME run_pipeline, which cross-validates, decides, and
                    drafts the CG reply.
Run:  python -m backend.watcher
Otherwise it starts as daemon threads on FastAPI startup.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from backend.config import get_config
from backend.domain.ids import new_id
from backend.domain.models import DocumentRef
from backend.email_client import fetch_new_emails
from backend.ingest.email_inbox import EmailInboxIngestor
from backend.llm.tracing import log_event
from backend.pipeline.graph import run_pipeline
from backend.storage import repo
from backend.storage.db import init_db

_queue: "queue.Queue[str]" = queue.Queue(maxsize=1000)
_ingestor = EmailInboxIngestor()


def enqueue_email(email_id: str) -> None:
    _queue.put(email_id)


def ingest_fetched_email(fetched) -> str | None:
    """Persist a freshly-fetched email + its attachments and enqueue it.
    Returns the new email_id"""
    cfg = get_config()
    if fetched.message_id and repo.get_email_by_message_id(fetched.message_id):
        return None
    email_id = new_id("eml")
    saved = _ingestor.save_attachments(email_id, fetched.attachments)
    if saved == 0:
        return None
    repo.create_email(email_id, cfg.default_customer_id, fetched.sender,
                      fetched.subject, message_id=fetched.message_id)
    log_event("email_received", email_id=email_id, sender=fetched.sender,
              attachments=saved)
    enqueue_email(email_id)
    return email_id


def _documents_for(email_id: str, shipment_id: str) -> list[DocumentRef]:
    """Rebuild DocumentRefs for an existing shipment (retry/resume) from its saved
    attachments — reusing the SAME document_ids so the extractor can skip done docs."""
    folder = Path(get_config().emails_abspath) / email_id
    refs: list[DocumentRef] = []
    for d in repo.get_documents(shipment_id):
        refs.append(DocumentRef(
            document_id=d["id"], shipment_id=shipment_id, filename=d["filename"],
            mime=d["mime"], path=str(folder / d["filename"]), source=d["source"],
        ))
    return refs


def process_email(email_id: str) -> None:
    em = repo.get_email(email_id)
    if em is None:
        return
    cfg = get_config()

    # Reuse the shipment across retries so document_ids stay stable (enables resume).
    if em.get("shipment_id"):
        shipment_id = em["shipment_id"]
        documents = _documents_for(email_id, shipment_id)
    else:
        ingested = _ingestor.ingest(email_id, em["customer_id"])
        shipment_id = ingested.shipment_id
        documents = ingested.documents
        repo.set_email_status(email_id, "processing", shipment_id=shipment_id)

    attempt = repo.bump_shipment_attempts(shipment_id)
    try:
        run_pipeline(shipment_id, documents, em["customer_id"])
        repo.set_email_status(email_id, "verified")
    except Exception as e:  # noqa: BLE001
        if attempt < cfg.max_pipeline_attempts:
            repo.set_shipment_stage(shipment_id, "failed")
            delay = cfg.retry_backoff_s * attempt  # linear backoff
            log_event("pipeline_retry_scheduled", email_id=email_id, shipment_id=shipment_id,
                      attempt=attempt, delay_s=delay, error=str(e)[:200])
            threading.Timer(delay, enqueue_email, args=[email_id]).start()
        else:
            repo.set_shipment_status(shipment_id, "needs_review")
            repo.set_shipment_stage(shipment_id, "failed")
            repo.set_email_status(email_id, "verified")
            log_event("pipeline_gave_up", email_id=email_id, shipment_id=shipment_id,
                      attempts=attempt, error=str(e)[:200])


def _worker_loop() -> None:
    log_event("worker_start")
    while True:
        email_id = _queue.get()
        try:
            process_email(email_id)
        except Exception as e:  # noqa: BLE001
            log_event("worker_loop_error", email_id=email_id, error=str(e))
        finally:
            _queue.task_done()


def _poller_loop() -> None:
    cfg = get_config()
    log_event("poller_start", imap_host=cfg.imap_host, user=cfg.email_user,
              interval_s=cfg.poll_interval_s, max_total_fetch=cfg.max_total_fetch)
    fetched_total = 0
    while True:
        if fetched_total >= cfg.max_total_fetch:
            # Hard session cap reached — stop fetching so we never go beyond the
            # newest N unread (protects a busy mailbox / backlog).
            log_event("poller_limit_reached", total=fetched_total)
            return
        try:
            remaining = cfg.max_total_fetch - fetched_total
            n_fetched, emails = fetch_new_emails(limit=remaining)
            fetched_total += n_fetched  # count every message read (marked seen)
            for fetched in emails:
                ingest_fetched_email(fetched)
        except Exception as e:  # noqa: BLE001 — never let a transient IMAP error kill the loop
            log_event("poller_error", error=str(e))
        time.sleep(cfg.poll_interval_s)


def start_in_background() -> None:
    """Start the worker and the IMAP poller. Re-enqueues any emails left unprocessed by a previous run."""
    init_db()
    threading.Thread(target=_worker_loop, daemon=True, name="email-worker").start()

    for em in repo.list_pending_emails():  # resume after a restart
        enqueue_email(em["id"])

    cfg = get_config()
    if cfg.email_configured:
        threading.Thread(target=_poller_loop, daemon=True, name="imap-poller").start()
    else:
        log_event("poller_disabled", reason="email not configured (set EMAIL_USER/EMAIL_PASSWORD)")


def run() -> None:
    start_in_background()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run()
