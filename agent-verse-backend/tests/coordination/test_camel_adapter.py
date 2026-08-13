from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.camel.adapter import CamelRuntime
from app.coordination.camel.models import RoleContract
from app.coordination.patterns.common import InMemoryPatternCheckpointStore
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService


def _roles() -> tuple[RoleContract, ...]:
    base = {
        "objective": "answer",
        "prohibited_actions": ("change policy",),
        "tool_allowlist": frozenset({"search"}),
        "connector_allowlist": frozenset({"docs"}),
        "data_scopes": frozenset({"internal"}),
        "authority_ceiling": frozenset({"read"}),
        "communication_schema": "camel.turn.v1",
        "termination_conditions": ("verified",),
        "version": 1,
    }
    return (
        RoleContract(role_name="researcher", responsibilities=("research",), **base),
        RoleContract(role_name="reviewer", responsibilities=("verify",), **base),
    )


@pytest.mark.asyncio
async def test_camel_completes_without_repeating_turns_after_restart() -> None:
    calls: list[str] = []
    transcript = TranscriptService(InMemoryTranscriptRepository())
    runtime = CamelRuntime(
        checkpoint_store=InMemoryPatternCheckpointStore(), transcript_service=transcript
    )

    async def turn(role, _context):
        calls.append(role.role_name)
        return {
            "content": "evidence found" if role.role_name == "researcher" else "verified",
            "completed": role.role_name == "reviewer",
            "agreement": role.role_name == "reviewer",
            "safe_output": "verified answer",
            "tokens": 5,
            "cost_usd": 0.01,
        }

    kwargs = {
        "tenant_id": "tenant",
        "session_id": "session",
        "execution_id": "execution",
        "roles": _roles(),
        "available_tools": frozenset({"search"}),
        "available_connectors": frozenset({"docs"}),
        "platform_authority": frozenset({"read"}),
        "run_turn": turn,
        "authorize_tools": lambda *_: True,
        "maximum_turns": 4,
        "maximum_tokens": 100,
        "maximum_cost_usd": 1,
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
    }
    result = await runtime.execute(**kwargs)
    resumed = await runtime.execute(**kwargs)
    assert result == resumed and result.phase == "completed"
    assert result.safe_output == "verified answer"
    assert calls == ["researcher", "reviewer"]
    assert len(await transcript.page("tenant", "session")) == 2


@pytest.mark.asyncio
async def test_camel_rejects_tool_escalation_and_honors_cancellation() -> None:
    runtime = CamelRuntime(
        checkpoint_store=InMemoryPatternCheckpointStore(),
        transcript_service=TranscriptService(InMemoryTranscriptRepository()),
    )
    common = {
        "tenant_id": "tenant",
        "roles": _roles(),
        "available_tools": frozenset({"search"}),
        "available_connectors": frozenset({"docs"}),
        "platform_authority": frozenset({"read"}),
        "maximum_turns": 2,
        "maximum_tokens": 100,
        "maximum_cost_usd": 1,
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
    }
    denied = await runtime.execute(
        **common,
        session_id="denied",
        execution_id="denied",
        run_turn=lambda *_: {"content": "try admin", "requested_tools": ["admin"]},
        authorize_tools=lambda *_: False,
    )
    assert denied.phase == "failed" and denied.terminal_reason == "tool_authorization_denied"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await runtime.execute(
        **common,
        session_id="cancel",
        execution_id="cancel",
        run_turn=lambda *_: pytest.fail("cancelled before turn"),
        authorize_tools=lambda *_: True,
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"
