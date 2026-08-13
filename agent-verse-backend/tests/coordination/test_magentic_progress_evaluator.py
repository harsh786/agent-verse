from __future__ import annotations

import pytest

from app.coordination.ledger.models import LedgerRevision
from app.coordination.magentic.progress_evaluator import evaluate_progress


def _revision(version: int, **updates) -> LedgerRevision:
    base = LedgerRevision(
        tenant_id="tenant",
        session_id="session",
        version=version,
        objective="Resolve incident",
        open_work=("inspect database", "restore service"),
        blockers=("no credentials",),
        idempotency_key=f"revision-{version}",
    )
    return base.model_copy(update=updates)


@pytest.mark.parametrize(
    ("updates", "kind"),
    [
        (
            {"completed_work": ("inspect database",), "open_work": ("restore service",)},
            "completed_work",
        ),
        ({"verified_facts": ("database is healthy",)}, "verified_fact"),
        ({"evidence_references": ("evidence://1",)}, "evidence"),
        ({"blockers": ()}, "removed_blocker"),
        ({"satisfied_criteria": ("service reachable",)}, "satisfied_criterion"),
    ],
)
def test_progress_accepts_only_material_typed_deltas(updates: dict, kind: str) -> None:
    result = evaluate_progress(_revision(1), _revision(2, **updates))
    assert result.progressed and kind in result.accepted_kinds


@pytest.mark.parametrize(
    "updates",
    [
        {"objective": "resolve   INCIDENT"},
        {"confidence": 0.9},
        {"last_action_signature": "tool:error:timeout"},
        {"assignment_history": ("agent-a", "agent-b", "agent-a")},
        {"claimed_completed_work": ("restore service",)},
    ],
)
def test_progress_rejects_cosmetic_repeated_or_unverified_claims(updates: dict) -> None:
    assert not evaluate_progress(_revision(1), _revision(2, **updates)).progressed


def test_evidence_removal_cannot_count_as_progress() -> None:
    previous = _revision(1, evidence_references=("evidence://1",))
    assert not evaluate_progress(previous, _revision(2)).progressed
