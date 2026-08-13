from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.ledger.repository import InMemoryProgressLedgerRepository
from app.coordination.magentic.adapter import MagenticRuntime
from app.coordination.magentic.models import ParticipantCandidate
from app.coordination.patterns.common import InMemoryPatternCheckpointStore


def _participants() -> tuple[ParticipantCandidate, ...]:
    return (
        ParticipantCandidate(
            agent_id="researcher",
            capabilities=frozenset({"research"}),
            available=True,
            policy_eligible=True,
            current_load=0,
            estimated_latency_ms=50,
        ),
        ParticipantCandidate(
            agent_id="busy",
            capabilities=frozenset({"research"}),
            available=True,
            policy_eligible=True,
            current_load=10,
            estimated_latency_ms=50,
        ),
    )


@pytest.mark.asyncio
async def test_magentic_completes_and_restart_does_not_repeat_work() -> None:
    calls: list[str] = []
    runtime = MagenticRuntime(
        ledger_repository=InMemoryProgressLedgerRepository(),
        checkpoint_store=InMemoryPatternCheckpointStore(),
    )

    async def run(agent_id, _revision):
        calls.append(agent_id)
        return {
            "completed_work": ("research",),
            "open_work": (),
            "verified_facts": ("fact",),
            "evidence_references": ("evidence://1",),
            "satisfied_criteria": ("answer cited",),
            "last_action_signature": "research:complete",
        }

    kwargs = {
        "tenant_id": "tenant",
        "session_id": "session",
        "execution_id": "execution",
        "goal": "research safely",
        "plan": lambda _: {
            "objective": "research safely",
            "open_work": ("research",),
            "satisfaction_criteria": ("answer cited",),
        },
        "participants": _participants(),
        "required_capabilities": frozenset({"research"}),
        "run_participant": run,
        "replan": lambda *_: pytest.fail("must not replan"),
        "synthesize": lambda _: "final answer",
        "maximum_rounds": 3,
        "maximum_resets": 1,
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
    }
    state, answer = await runtime.execute(**kwargs)
    resumed, replay_answer = await runtime.execute(**kwargs)
    assert state.phase == resumed.phase == "completed"
    assert answer == replay_answer == "final answer"
    assert calls == ["researcher"]


@pytest.mark.asyncio
async def test_magentic_cancellation_and_exhausted_stall_escalate() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    runtime = MagenticRuntime(
        ledger_repository=InMemoryProgressLedgerRepository(),
        checkpoint_store=InMemoryPatternCheckpointStore(),
    )
    common = {
        "tenant_id": "tenant",
        "session_id": "session",
        "goal": "goal",
        "plan": lambda _: {"objective": "goal", "open_work": ("work",)},
        "participants": _participants(),
        "required_capabilities": frozenset({"research"}),
        "run_participant": lambda *_: pytest.fail("cancelled"),
        "replan": lambda *_: {"open_work": ("work",)},
        "synthesize": lambda _: "never",
        "maximum_rounds": 2,
        "maximum_resets": 0,
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
    }
    stopped, _ = await runtime.execute(
        **common, execution_id="cancel", cancelled=cancelled
    )
    assert stopped.phase == "cancelled"
    stall_arguments = {
        **common,
        "session_id": "session-stall",
        "execution_id": "stall",
        "cancelled": None,
        "run_participant": lambda *_: {
            "open_work": ("work",),
            "last_action_signature": "same",
        },
    }
    stalled, _ = await runtime.execute(**stall_arguments)
    assert stalled.phase == "awaiting_human" and stalled.terminal_reason == "reset_exhausted"
