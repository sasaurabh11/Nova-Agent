from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DocumentRef:
    document_id: str
    shipment_id: str
    filename: str
    mime: str               # application/pdf | image/png | image/jpeg
    path: str               # absolute path on disk
    doc_type: str = "unknown"
    source: str = "upload"

    def read_bytes(self) -> bytes:
        return Path(self.path).read_bytes()


@dataclass
class Customer:
    id: str
    name: str


@dataclass
class Ruleset:
    id: str
    customer_id: str
    version: int
    rules: dict
