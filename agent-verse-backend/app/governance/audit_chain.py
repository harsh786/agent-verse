"""Tamper-evident audit via per-tenant hash chaining (Grantex G5).

Each audit record carries the hash of the previous record, so any edit,
insertion, or deletion in the middle of the chain breaks every subsequent hash —
making the trail tamper-evident and exportable as a compliance evidence pack.
This complements (does not replace) the append-only audit trail: it adds
verifiability on top.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

_GENESIS = "0" * 64


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, payload: dict, *, seq: int, at: str) -> str:
    material = f"{prev_hash}\x00{seq}\x00{at}\x00{_canonical(payload)}"
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    at: str  # ISO timestamp
    payload: dict
    prev_hash: str
    record_hash: str


@dataclass
class AuditChain:
    """An append-only, hash-chained audit log for one tenant."""

    tenant_id: str
    records: list[AuditRecord] = field(default_factory=list)

    @property
    def head_hash(self) -> str:
        return self.records[-1].record_hash if self.records else _GENESIS

    def append(self, payload: dict, *, at: datetime) -> AuditRecord:
        seq = len(self.records)
        prev = self.head_hash
        at_iso = at.isoformat()
        record = AuditRecord(
            seq=seq,
            at=at_iso,
            payload=payload,
            prev_hash=prev,
            record_hash=compute_hash(prev, payload, seq=seq, at=at_iso),
        )
        self.records.append(record)
        return record

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the chain. Returns (ok, first_broken_seq_or_None)."""
        prev = _GENESIS
        for rec in self.records:
            expected = compute_hash(prev, rec.payload, seq=rec.seq, at=rec.at)
            if rec.prev_hash != prev or rec.record_hash != expected:
                return False, rec.seq
            prev = rec.record_hash
        return True, None

    def export_evidence_pack(self) -> dict:
        """A verifiable, self-describing export for compliance (SOC2/GDPR)."""
        ok, broken = self.verify()
        return {
            "tenant_id": self.tenant_id,
            "count": len(self.records),
            "head_hash": self.head_hash,
            "verified": ok,
            "first_broken_seq": broken,
            "records": [
                {
                    "seq": r.seq,
                    "at": r.at,
                    "payload": r.payload,
                    "prev_hash": r.prev_hash,
                    "record_hash": r.record_hash,
                }
                for r in self.records
            ],
        }


__all__ = ["AuditChain", "AuditRecord", "compute_hash"]
