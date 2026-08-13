from __future__ import annotations

import pytest

from app.coordination.ledger.models import LedgerRevision
from app.coordination.magentic.replan_manager import ReplanManager


def test_replan_preserves_verified_state_and_only_invalidates_contradictions() -> None:
    current = LedgerRevision(
        tenant_id="tenant",
        session_id="session",
        version=2,
        objective="restore service",
        open_work=("restart", "verify"),
        completed_work=("inspect",),
        verified_facts=("database healthy", "cache unhealthy"),
        evidence_references=("evidence://db",),
        satisfaction_criteria=("service healthy",),
        reset_count=0,
        idempotency_key="revision-2",
    )
    replanned = ReplanManager(max_resets=2).replan(
        current,
        open_work=("flush cache", "verify"),
        contradicted_facts=("cache unhealthy",),
        stall_evidence_reference="evidence://stall",
        idempotency_key="replan-1",
    )
    assert replanned.completed_work == ("inspect",)
    assert replanned.verified_facts == ("database healthy",)
    assert replanned.evidence_references == ("evidence://db",)
    assert replanned.reset_count == 1 and replanned.predecessor_version == 2
    assert replanned.stall_evidence_references == ("evidence://stall",)


def test_replan_exhaustion_is_bounded() -> None:
    current = LedgerRevision(
        tenant_id="tenant",
        session_id="session",
        version=3,
        objective="goal",
        reset_count=2,
        idempotency_key="revision-3",
    )
    with pytest.raises(RuntimeError, match="exhausted"):
        ReplanManager(max_resets=2).replan(
            current,
            open_work=("retry",),
            contradicted_facts=(),
            stall_evidence_reference="evidence://stall",
            idempotency_key="replan",
        )
