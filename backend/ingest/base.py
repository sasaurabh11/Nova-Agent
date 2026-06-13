from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.models import DocumentRef


class IngestedShipment:
    def __init__(self, shipment_id: str, customer_id: str, documents: list[DocumentRef]):
        self.shipment_id = shipment_id
        self.customer_id = customer_id
        self.documents = documents


class BaseIngestor(ABC):
    source: str = "upload"

    @abstractmethod
    def ingest(self, *args, **kwargs) -> IngestedShipment:
        raise NotImplementedError
