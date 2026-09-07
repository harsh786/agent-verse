"""Verifiable receipt for a data-subject deletion cascade (GDPR/DPDP erasure).

A :class:`DeletionReceipt` is the tamper-evident proof of what a right-to-erasure
run actually did: how many rows were removed per store, whether the run was
suspended by a legal hold (in which case *nothing* is destroyed), and whether an
independent re-scan confirmed no residue survived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DeletionReceipt:
    """Immutable-ish record describing the outcome of one erasure run."""

    subject_ref: str
    tenant_id: str
    started_at: datetime
    completed_at: datetime
    per_store: dict[str, int] = field(default_factory=dict)
    total_deleted: int = 0
    suspended: bool = False
    suspension_reason: str = ""
    verified: bool = False
    # Human-readable notes, e.g. why a store is recorded-only (not subject-scoped).
    notes: dict[str, str] = field(default_factory=dict)
    # Residue found by the independent verification re-scan (empty == clean).
    residue: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for API responses / audit metadata."""
        return {
            "subject_ref": self.subject_ref,
            "tenant_id": self.tenant_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "per_store": dict(self.per_store),
            "total_deleted": self.total_deleted,
            "suspended": self.suspended,
            "suspension_reason": self.suspension_reason,
            "verified": self.verified,
            "notes": dict(self.notes),
            "residue": dict(self.residue),
        }
