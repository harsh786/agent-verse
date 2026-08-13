from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.coordination.patterns.consensus_adapter import DurableConsensusRuntime


def _vote(verifier_id: str, *, success: bool = True) -> dict[str, object]:
    return {
        "provider_id": f"provider-{verifier_id}",
        "model_id": f"model-{verifier_id}",
        "success": success,
        "safe_reason": "bounded rationale",
        "confidence": 0.9,
        "evidence_references": (f"evidence://{verifier_id}",),
        "cost_usd": 0.01,
    }


@pytest.mark.asyncio
async def test_consensus_persists_lineage_and_restart_is_exact_once() -> None:
    store = InMemoryPatternCheckpointStore()
    calls: list[str] = []

    async def verify(verifier_id: str) -> dict[str, object]:
        calls.append(verifier_id)
        return _vote(verifier_id)

    runtime = DurableConsensusRuntime(checkpoint_store=store)
    result = await runtime.execute(
        session_id="session",
        execution_id="execution",
        verifier_ids=("primary", "cross", "independent"),
        verify=verify,
        judge=lambda *_: {"success": True, "evidence_reference": "judge://1"},
        rubric_id="safety",
        rubric_version="2",
        quorum=2,
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    resumed = await runtime.execute(
        session_id="session",
        execution_id="execution",
        verifier_ids=("primary", "cross", "independent"),
        verify=lambda _: pytest.fail("must not verify twice"),
        judge=lambda *_: pytest.fail("must not judge twice"),
        rubric_id="safety",
        rubric_version="2",
        quorum=2,
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert result == resumed and result.phase == "completed"
    assert result.quorum_met and result.judge_reference == "judge://1"
    assert {vote.provider_id for vote in result.votes} == {
        "provider-primary",
        "provider-cross",
        "provider-independent",
    }
    assert calls == ["primary", "cross", "independent"]


@pytest.mark.asyncio
async def test_consensus_escalates_disagreement_quorum_and_budget() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    disagreement = await runtime.execute(
        session_id="s",
        execution_id="disagreement",
        verifier_ids=("a", "b", "c"),
        verify=lambda verifier: _vote(verifier, success=verifier != "b"),
        judge=lambda *_: {"success": True, "evidence_reference": "judge://1"},
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert disagreement.phase == "escalated" and disagreement.disagreement
    assert disagreement.terminal_reason == "disagreement"

    async def unavailable(_verifier: str) -> dict[str, object]:
        raise RuntimeError("down")

    quorum = await runtime.execute(
        session_id="s",
        execution_id="quorum",
        verifier_ids=("a", "b"),
        verify=unavailable,
        judge=lambda *_: pytest.fail("no quorum must not judge"),
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert quorum.terminal_reason == "quorum_lost" and quorum.requires_hitl

    budget = await runtime.execute(
        session_id="s",
        execution_id="budget",
        verifier_ids=("a", "b"),
        verify=lambda verifier: {**_vote(verifier), "cost_usd": 1.0},
        judge=lambda *_: pytest.fail("budget must stop before judge"),
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=0.5,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert budget.terminal_reason == "budget_exhausted"
