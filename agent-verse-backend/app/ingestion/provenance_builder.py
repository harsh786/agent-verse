"""ProvenanceBuilder — attaches provenance metadata to ingested chunks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.content_classifier import ContentType


@dataclass
class IngestionProvenance:
    provenance_id: str
    tenant_id: str
    content_type: str
    source_url: str
    source_name: str
    chunk_index: int = 0
    page_number: int | None = None
    ingestion_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "tenant_id": self.tenant_id,
            "content_type": self.content_type,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "ingestion_id": self.ingestion_id,
        }


class ProvenanceBuilder:
    def build(
        self,
        content_type: ContentType,
        *,
        source_url: str,
        source_name: str,
        tenant_id: str,
        chunk_index: int = 0,
        page_number: int | None = None,
    ) -> IngestionProvenance:
        return IngestionProvenance(
            provenance_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            content_type=content_type.value,
            source_url=source_url,
            source_name=source_name,
            chunk_index=chunk_index,
            page_number=page_number,
        )
