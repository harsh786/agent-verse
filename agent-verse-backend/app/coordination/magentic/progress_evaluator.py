"""Deterministic validation of material Magentic progress."""

from __future__ import annotations

from app.coordination.ledger.models import LedgerRevision
from app.coordination.magentic.models import ProgressAssessment


def _new(current: tuple[str, ...], previous: tuple[str, ...]) -> bool:
    return bool(set(current) - set(previous))


def evaluate_progress(
    previous: LedgerRevision, current: LedgerRevision
) -> ProgressAssessment:
    accepted: list[str] = []
    newly_completed = set(current.completed_work) - set(previous.completed_work)
    if newly_completed and newly_completed <= set(previous.open_work):
        accepted.append("completed_work")
    if _new(current.verified_facts, previous.verified_facts):
        accepted.append("verified_fact")
    if _new(current.evidence_references, previous.evidence_references):
        accepted.append("evidence")
    if set(previous.blockers) - set(current.blockers):
        accepted.append("removed_blocker")
    if _new(current.satisfied_criteria, previous.satisfied_criteria):
        accepted.append("satisfied_criterion")
    removed_open = set(previous.open_work) - set(current.open_work)
    if (
        removed_open
        and removed_open <= set(current.completed_work)
        and "completed_work" not in accepted
    ):
        accepted.append("narrowed_open_work")
    rejected: list[str] = []
    if current.claimed_completed_work and not newly_completed:
        rejected.append("unverified_completion_claim")
    if not accepted:
        rejected.append("no_material_delta")
    return ProgressAssessment(
        progressed=bool(accepted),
        accepted_kinds=tuple(accepted),
        rejected_reasons=tuple(rejected),
    )


__all__ = ["evaluate_progress"]
