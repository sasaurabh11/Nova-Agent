from __future__ import annotations

from pathlib import Path

from backend.config import get_config
from backend.domain.models import DocumentRef
from backend.ingest.base import BaseIngestor, IngestedShipment
from backend.storage import repo

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_DOC_EXTS = set(_MIME_BY_EXT)


def _email_dir(email_id: str) -> Path:
    return Path(get_config().emails_abspath) / email_id


class EmailInboxIngestor(BaseIngestor):
    source = "email"

    def save_attachments(self, email_id: str, attachments: list[tuple[str, bytes]]) -> int:
        """Persist a fetched email's attachments to disk. Returns the count saved."""
        folder = _email_dir(email_id)
        folder.mkdir(parents=True, exist_ok=True)
        saved = 0
        for filename, blob in attachments:
            ext = Path(filename).suffix.lower()
            if ext not in _DOC_EXTS:
                continue
            (folder / Path(filename).name).write_bytes(blob)
            saved += 1
        return saved

    def ingest(self, email_id: str, customer_id: str) -> IngestedShipment:
        """Build a shipment + documents from the email's saved attachments."""
        folder = _email_dir(email_id)
        files = sorted(p for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in _DOC_EXTS)
        if not files:
            raise ValueError(f"email {email_id!r} has no document attachments")

        shipment_id = repo.create_shipment(customer_id, source=self.source)
        docs: list[DocumentRef] = []
        for f in files:
            mime = _MIME_BY_EXT.get(f.suffix.lower(), "application/octet-stream")
            doc_id = repo.create_document(shipment_id, f.name, mime, source=self.source)
            docs.append(DocumentRef(
                document_id=doc_id, shipment_id=shipment_id, filename=f.name,
                mime=mime, path=str(f), source=self.source,
            ))
        return IngestedShipment(shipment_id, customer_id, docs)
