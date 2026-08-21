from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.provenance.source_ref import SourceRef


@dataclass
class ProvenanceRecord:
    claim_id: str
    claim_text: str
    supporting_sources: list[SourceRef]
    generated_by_step: str
    generated_by_model: str
    confidence: float
    verification_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "sources": [s.to_dict() for s in self.supporting_sources],
            "generated_by_step": self.generated_by_step,
            "generated_by_model": self.generated_by_model,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
        }
