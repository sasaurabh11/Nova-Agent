from __future__ import annotations

from pathlib import Path

from backend.domain.models import DocumentRef
from backend.ingest.base import BaseIngestor, IngestedShipment
from backend.storage import repo

_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class UploadIngestor(BaseIngestor):
    source = "upload"

    def ingest(self, customer_id: str, files: list[tuple[str, bytes]]) -> IngestedShipment:
        """files: list of (filename, bytes). Persists shipment + documents to disk/DB."""
        _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        shipment_id = repo.create_shipment(customer_id, source=self.source)
        docs: list[DocumentRef] = []
        for filename, blob in files:
            ext = Path(filename).suffix.lower()
            mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
            doc_id = repo.create_document(shipment_id, filename, mime, source=self.source)
            path = _UPLOAD_DIR / f"{doc_id}{ext}"
            path.write_bytes(blob)
            docs.append(DocumentRef(
                document_id=doc_id, shipment_id=shipment_id, filename=filename,
                mime=mime, path=str(path), source=self.source,
            ))
        return IngestedShipment(shipment_id, customer_id, docs)
