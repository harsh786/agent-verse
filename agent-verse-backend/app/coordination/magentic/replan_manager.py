"""Bounded Magentic reset preserving accepted evidence and completed work."""

from __future__ import annotations

from app.coordination.ledger.models import LedgerRevision


class ReplanManager:
    def __init__(self, *, max_resets: int) -> None:
        if max_resets < 0:
            raise ValueError("max_resets cannot be negative")
        self._maximum = max_resets

    def replan(
        self,
        current: LedgerRevision,
        *,
        open_work: tuple[str, ...],
        contradicted_facts: tuple[str, ...],
        stall_evidence_reference: str,
        idempotency_key: str,
    ) -> LedgerRevision:
        if current.reset_count >= self._maximum:
            raise RuntimeError("Magentic reset budget exhausted")
        contradicted = set(contradicted_facts)
        return current.model_copy(
            update={
                "version": current.version + 1,
                "open_work": open_work,
                "verified_facts": tuple(
                    fact for fact in current.verified_facts if fact not in contradicted
                ),
                "reset_count": current.reset_count + 1,
                "predecessor_version": current.version,
                "stall_evidence_references": (
                    *current.stall_evidence_references,
                    stall_evidence_reference,
                ),
                "idempotency_key": idempotency_key,
            }
        )


__all__ = ["ReplanManager"]
