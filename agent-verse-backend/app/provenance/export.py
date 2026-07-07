"""ProvenanceExport — exports provenance records in various formats."""
from __future__ import annotations
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.provenance.claim_trace import ProvenanceRecord


class ProvenanceExport:
    def to_json(self, records: "list[ProvenanceRecord]") -> str:
        return json.dumps([r.to_dict() for r in records], indent=2)

    def to_citation_list(self, records: "list[ProvenanceRecord]") -> list[dict[str, Any]]:
        return [
            {
                "claim": r.claim_text[:200],
                "confidence": r.confidence,
                "status": r.verification_status,
                "sources": [s.to_dict() for s in r.supporting_sources],
            }
            for r in records
        ]

    def to_summary(self, records: "list[ProvenanceRecord]") -> dict[str, Any]:
        if not records:
            return {"total": 0, "supported": 0, "unsupported": 0, "unknown": 0}
        supported = sum(1 for r in records if r.verification_status == "supported")
        unsupported = sum(1 for r in records if r.verification_status == "unsupported")
        unknown = sum(1 for r in records if r.verification_status == "unknown")
        avg_confidence = sum(r.confidence for r in records) / len(records)
        return {
            "total": len(records),
            "supported": supported,
            "unsupported": unsupported,
            "unknown": unknown,
            "avg_confidence": round(avg_confidence, 3),
        }
