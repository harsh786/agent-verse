"""Behavioral tests for the durable Consensus adapter's bounded escalation paths."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.coordination.patterns.consensus_adapter import (
    DurableConsensusRuntime,
    DurableConsensusState,
)


def _vote(verifier_id: str, *, success: bool = True) -> dict[str, object]:
    return {
        "provider_id": f"provider-{verifier_id}",
        "model_id": f"model-{verifier_id}",
        "success": success,
        "safe_reason": "reason",
        "confidence": 0.9,
        "evidence_references": (f"evidence://{verifier_id}",),
        "cost_usd": 0.01,
    }


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_consensus_escalates_when_deadline_already_passed() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    result = await runtime.execute(
        session_id="s",
        execution_id="deadline",
        verifier_ids=("a", "b"),
        verify=lambda _v: pytest.fail("deadline must stop before verifying"),
        judge=lambda *_: pytest.fail("deadline must stop before judging"),
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert result.phase == "escalated"
    assert result.terminal_reason == "deadline_exceeded" and result.requires_hitl


@pytest.mark.asyncio
async def test_consensus_escalates_on_cancellation_before_first_verifier() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    cancel = asyncio.Event()
    cancel.set()
    result = await runtime.execute(
        session_id="s",
        execution_id="cancel",
        verifier_ids=("a", "b"),
        verify=lambda _v: pytest.fail("cancelled run must not verify"),
        judge=lambda *_: pytest.fail("cancelled run must not judge"),
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=_future(),
        cancelled=cancel,
    )
    assert result.phase == "escalated" and result.terminal_reason == "cancelled"


@pytest.mark.asyncio
async def test_consensus_denied_by_policy_raises_permission_error() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    with pytest.raises(PermissionError, match="denied by policy"):
        await runtime.execute(
            session_id="s",
            execution_id="policy",
            verifier_ids=("a", "b"),
            verify=lambda _v: _vote("a"),
            judge=lambda *_: {"success": True, "evidence_reference": "j://1"},
            rubric_id="r",
            rubric_version="1",
            quorum=2,
            maximum_cost_usd=1,
            deadline=_future(),
            policy_allowed=False,
        )


@pytest.mark.asyncio
async def test_consensus_escalates_when_judge_raises() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())

    def judge(*_a: object) -> dict[str, object]:
        raise RuntimeError("judge exploded")

    result = await runtime.execute(
        session_id="s",
        execution_id="judge",
        verifier_ids=("a", "b"),
        verify=lambda verifier: _vote(verifier),
        judge=judge,
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=_future(),
    )
    assert result.phase == "escalated" and result.terminal_reason == "judge_failed"
    # Verifier votes were still captured before the judge failed.
    assert len(result.votes) == 2


@pytest.mark.asyncio
async def test_consensus_rejects_degenerate_verifiers_and_quorum() -> None:
    runtime = DurableConsensusRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    base = {
        "session_id": "s",
        "verify": lambda v: _vote(v),
        "judge": lambda *_: {"success": True, "evidence_reference": "j://1"},
        "rubric_id": "r",
        "rubric_version": "1",
        "maximum_cost_usd": 1,
        "deadline": _future(),
    }
    with pytest.raises(ValueError, match="distinct verifier"):
        await runtime.execute(
            **base, execution_id="solo", verifier_ids=("only",), quorum=2
        )
    with pytest.raises(ValueError, match="distinct verifier"):
        await runtime.execute(
            **base, execution_id="dup", verifier_ids=("a", "a"), quorum=2
        )
    with pytest.raises(ValueError, match="quorum"):
        await runtime.execute(
            **base, execution_id="q1", verifier_ids=("a", "b"), quorum=1
        )
    with pytest.raises(ValueError, match="quorum"):
        await runtime.execute(
            **base, execution_id="q3", verifier_ids=("a", "b"), quorum=3
        )


@pytest.mark.asyncio
async def test_consensus_returns_terminal_state_without_reverifying() -> None:
    store = InMemoryPatternCheckpointStore()
    seeded = DurableConsensusState(
        session_id="s",
        execution_id="terminal",
        phase="escalated",
        rubric_id="r",
        rubric_version="1",
        verifier_ids=("a", "b"),
        requires_hitl=True,
        terminal_reason="disagreement",
    )
    await store.save(seeded)
    runtime = DurableConsensusRuntime(checkpoint_store=store)
    result = await runtime.execute(
        session_id="s",
        execution_id="terminal",
        verifier_ids=("a", "b"),
        verify=lambda _v: pytest.fail("terminal state must not re-verify"),
        judge=lambda *_: pytest.fail("terminal state must not re-judge"),
        rubric_id="r",
        rubric_version="1",
        quorum=2,
        maximum_cost_usd=1,
        deadline=_future(),
    )
    assert result == seeded
