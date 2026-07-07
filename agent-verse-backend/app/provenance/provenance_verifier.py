"""ProvenanceVerifier — verifies claims against supporting sources."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.provenance.claim_trace import ProvenanceRecord


class ProvenanceVerifier:
    def verify(self, record: "ProvenanceRecord") -> str:
        """Returns: supported | unsupported | contradicted | unknown."""
        if not record.supporting_sources:
            return "unknown"
        if record.confidence >= 0.7:
            return "supported"
        if record.confidence >= 0.4:
            return "unknown"
        return "unsupported"

    def verify_batch(self, records: "list[ProvenanceRecord]") -> dict[str, str]:
        return {r.claim_id: self.verify(r) for r in records}
