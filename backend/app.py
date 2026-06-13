from __future__ import annotations

import threading

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from backend.config import get_config
from backend.ingest.upload import UploadIngestor
from backend.llm.tracing import log_event
from backend.pipeline.graph import run_pipeline
from backend.storage import repo
from backend.storage.db import cursor, init_db
from backend.storage.query import answer_question

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
    # A terminal status with no decision means the run failed (e.g. extraction
    # errored) — surface that as an error stage instead of a perpetual "processing".
    failed = (full["shipment"]["status"] != "processing" and not full["decision"])
    full["stages"] = {
        "extract": "done" if full["extractions"] else ("error" if failed else "processing"),
        "validate": "done" if full["validations"] else (
            ("error" if failed else "processing") if full["extractions"] else
            ("error" if failed else "pending")),
        "decide": "done" if full["decision"] else ("error" if failed else "pending"),
    }
    return full


class QueryIn(BaseModel):
    question: str


@app.post("/query")
def query(body: QueryIn) -> dict:
    return answer_question(body.question).model_dump()

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="ui")
