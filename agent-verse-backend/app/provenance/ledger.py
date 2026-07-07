from __future__ import annotations
import uuid
from typing import Any, TYPE_CHECKING
from app.provenance.claim_trace import ProvenanceRecord

if TYPE_CHECKING:
    from app.provenance.source_ref import SourceRef


class ProvenanceLedger:
    def __init__(self) -> None:
        self._records: list[ProvenanceRecord] = []

    def record(
        self,
        *,
        claim_text: str,
        sources: list["SourceRef"],
        step_id: str,
        model_id: str,
        confidence: float,
    ) -> ProvenanceRecord:
        status = "supported" if sources and confidence >= 0.5 else "unknown"
        if confidence < 0.3:
            status = "unsupported"
        rec = ProvenanceRecord(
            claim_id=uuid.uuid4().hex,
            claim_text=claim_text,
            supporting_sources=sources,
            generated_by_step=step_id,
            generated_by_model=model_id,
            confidence=confidence,
            verification_status=status,
        )
        self._records.append(rec)
        return rec

    def list_all(self) -> list[ProvenanceRecord]:
        return list(self._records)

    def export(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._records]

    def clear(self) -> None:
        self._records.clear()
