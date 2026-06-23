from __future__ import annotations

import threading

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from backend.config import get_config
from backend.domain.ids import new_id
from backend.email_client import send_reply
from backend.ingest.email_inbox import EmailInboxIngestor
from backend.ingest.upload import UploadIngestor
from backend.llm.tracing import log_event
from backend.pipeline.graph import run_pipeline
from backend.storage import repo
from backend.storage.db import cursor, init_db
from backend.storage.query import answer_question
from backend.watcher import enqueue_email, start_in_background

app = FastAPI(title="Nova Trade Pipeline", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    start_in_background()


def _run_async(shipment_id: str, documents, customer_id: str) -> None:
    try:
        run_pipeline(shipment_id, documents, customer_id)
    except Exception as e:  # noqa: BLE001 — fail loud; mark for human review
        log_event("pipeline_thread_error", shipment_id=shipment_id, error=str(e))
        repo.set_shipment_status(shipment_id, "needs_review")


# Routes
@app.get("/health")
def health() -> dict:
    cfg = get_config()
    return {"status": "ok", "model": cfg.model,
            "uncertain_threshold": cfg.uncertain_threshold,
            "auto_approve_threshold": cfg.auto_approve_threshold}


@app.get("/customers")
def customers() -> list[dict]:
    with cursor(read_only=True) as cur:
        rows = cur.execute("SELECT id, name FROM customers ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@app.post("/shipments", status_code=202)
async def create_shipment(
    customer_id: str = Form("cust_acme"),
    files: list[UploadFile] = File(...),
) -> dict:
    blobs = [(f.filename, await f.read()) for f in files]
    if not blobs:
        raise HTTPException(400, "no files uploaded")
    ingested = UploadIngestor().ingest(customer_id, blobs)
    threading.Thread(
        target=_run_async,
        args=(ingested.shipment_id, ingested.documents, customer_id),
        daemon=True,
    ).start()
    return {"shipment_id": ingested.shipment_id}


@app.get("/shipments")
def list_shipments() -> list[dict]:
    return repo.list_shipments()


@app.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: str) -> dict:
    full = repo.get_shipment_full(shipment_id)
    if full is None:
        raise HTTPException(404, "shipment not found")
    full["cross_validation"] = repo.get_cross_validation(shipment_id)
    full["reply"] = repo.get_reply_for_shipment(shipment_id)
    # A terminal status with no decision means the run failed (e.g. extraction
    # errored) — surface that as an error stage instead of a perpetual "processing".
    failed = (full["shipment"]["status"] not in ("processing",) and not full["decision"])

    def _stage(done: bool, prereq_done: bool) -> str:
        if done:
            return "done"
        if failed:
            return "error"
        return "processing" if prereq_done else "pending"

    has_ext = bool(full["extractions"])
    has_val = bool(full["validations"])
    has_cv = full["cross_validation"] is not None
    full["stages"] = {
        "extract": _stage(has_ext, True),
        "validate": _stage(has_val, has_ext),
        "cross_validate": _stage(has_cv, has_val),
        "decide": _stage(full["decision"] is not None, has_cv),
    }
    return full


class QueryIn(BaseModel):
    question: str


@app.post("/query")
def query(body: QueryIn) -> dict:
    return answer_question(body.question).model_dump()


@app.post("/inbox/emails", status_code=202)
async def receive_email(
    subject: str = Form("Shipment documents for approval"),
    sender: str = Form("supplier@su.example"),
    customer_id: str | None = Form(None),
    files: list[UploadFile] = File(...),
) -> dict:
    """Manually feed an email into the pipeline (dev/demo trigger) — same queue and
    worker the real IMAP poller uses. Reply via SMTP will go to `sender`."""
    blobs = [(f.filename, await f.read()) for f in files]
    email_id = new_id("eml")
    saved = EmailInboxIngestor().save_attachments(email_id, blobs)
    if saved == 0:
        raise HTTPException(400, "no PDF/image attachments found")
    repo.create_email(email_id, customer_id or get_config().default_customer_id,
                      sender, subject)
    enqueue_email(email_id)
    log_event("email_enqueued", email_id=email_id, attachments=saved, source="manual")
    return {"email_id": email_id, "status": "received"}


@app.get("/inbox")
def inbox() -> list[dict]:
    return repo.list_emails()


@app.get("/inbox/{email_id}")
def email_detail(email_id: str) -> dict:
    em = repo.get_email(email_id)
    if em is None:
        raise HTTPException(404, "email not found")
    out: dict = {"email": em}
    if em.get("shipment_id"):
        out["shipment"] = get_shipment(em["shipment_id"])  # includes stages + cv + reply
    return out


class ReplyEdit(BaseModel):
    subject: str
    body: str


@app.put("/replies/{reply_id}")
def edit_reply(reply_id: str, body: ReplyEdit) -> dict:
    r = repo.get_reply(reply_id)
    if r is None:
        raise HTTPException(404, "reply not found")
    if r["status"] != "draft":
        raise HTTPException(409, "reply already sent")
    repo.update_reply_body(reply_id, body.subject, body.body)
    return repo.get_reply(reply_id)


@app.post("/replies/{reply_id}/send")
def send_reply_route(reply_id: str) -> dict:
    r = repo.get_reply(reply_id)
    if r is None:
        raise HTTPException(404, "reply not found")
    if r["status"] == "sent":
        return r

    cfg = get_config()
    em = repo.get_email_by_shipment(r["shipment_id"])
    delivered = False
    if cfg.email_configured and em and em.get("sender"):
        try:
            send_reply(em["sender"], r["subject"], r["body"],
                       in_reply_to=em.get("message_id"))
            delivered = True
        except Exception as e:  # noqa: BLE001 — don't mark sent if delivery failed
            log_event("smtp_send_error", reply_id=reply_id, error=str(e))
            raise HTTPException(502, f"failed to send email: {e}")

    sent = repo.mark_reply_sent(reply_id)
    log_event("reply_sent_by_cg", reply_id=reply_id,
              shipment_id=sent["shipment_id"], delivered=delivered)
    sent["delivered"] = delivered
    return sent


_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
