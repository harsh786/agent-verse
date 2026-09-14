"""Phase 0.3a — ChatService drives the REAL engine for GOAL turns.

Replaces the old simulation: a GOAL-intent chat turn submits to GoalService
(binding the conversation) and streams the goal's real events back as canonical
chat SSE. Unit-tested with a fake GoalService (no DB / no real agent needed).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext


class _FakeGoalService:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.submitted: dict[str, Any] | None = None

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.submitted = kwargs
        return {"goal_id": "g-123", "status": "accepted"}

    async def subscribe_events(
        self, goal_id: str, tenant_ctx: Any, since_sequence: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        assert goal_id == "g-123"
        for e in self.events:
            yield e


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_run_goal_submits_with_conversation_binding() -> None:
    fake = _FakeGoalService([])
    svc = ChatService(goal_service=fake)
    session = svc.create_session("t1", title="chat", agent_id="agent-7")

    goal_id = await svc.run_goal(
        session_id=session.id,
        tenant_id="t1",
        tenant_ctx=_ctx(),
        message_id="m1",
        user_message="search Jira for open bugs",
    )

    assert goal_id == "g-123"
    assert fake.submitted is not None
    assert fake.submitted["goal"] == "search Jira for open bugs"
    assert fake.submitted["dry_run"] is False
    assert fake.submitted["agent_id"] == "agent-7"  # inherited from the session
    ec = fake.submitted["execution_context"]
    assert ec["source"] == "chat"
    assert ec["conversation_id"] == session.id
    assert ec["session_id"] == session.id
    assert ec["message_id"] == "m1"


async def test_stream_goal_emits_canonical_events_from_real_bus() -> None:
    fake = _FakeGoalService(
        [
            {"type": "goal_started", "goal_id": "g-123"},
            {"type": "cache_hit", "x": 1},  # internal noise — dropped
            {"type": "plan_ready", "steps": ["search", "summarize"]},
            {"type": "tool_call_complete", "tool_name": "jira.search"},
            {"type": "goal_complete", "status": "complete"},
        ]
    )
    svc = ChatService(goal_service=fake)

    frames = [
        f
        async for f in svc.stream_goal(
            goal_id="g-123", tenant_ctx=_ctx(), session_id="s1", message_id="m1"
        )
    ]
    events = [json.loads(f[len("data: "):].strip()) for f in frames]
    types = [e["type"] for e in events]

    assert types == ["message_started", "plan_ready", "tool_call", "goal_complete", "done"]
    assert all(e.get("session_id") == "s1" for e in events)
    assert next(e for e in events if e["type"] == "tool_call")["tool"] == "jira.search"


async def test_run_goal_without_goal_service_is_explicit_error() -> None:
    svc = ChatService()  # no goal_service wired
    session = svc.create_session("t1")
    import pytest

    with pytest.raises(RuntimeError, match="GoalService"):
        await svc.run_goal(
            session_id=session.id,
            tenant_id="t1",
            tenant_ctx=_ctx(),
            message_id="m1",
            user_message="do a thing",
        )
