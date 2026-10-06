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


# ── CHAT-D-3: a GOAL turn runs with the session's agent on every path ─────────

# Two agents whose configs differ: the tenant default and the session's agent.
_AGENTS: dict[str | None, dict[str, Any]] = {
    None: {"name": "default", "system_prompt": "You are a general assistant.", "model": "m-1"},
    "agent-billing": {
        "name": "billing", "system_prompt": "You only handle invoices.", "model": "m-billing",
    },
}


class _ConfigRecordingGoalService(_FakeGoalService):
    """Resolves the agent the way GoalService does: no agent id -> the default."""

    def __init__(self) -> None:
        super().__init__([])
        self.ran_with: dict[str, Any] | None = None

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.ran_with = _AGENTS[kwargs.get("agent_id")]
        return await super().submit_goal(**kwargs)


class _StubRepository:
    """The DB-backed path: the session exists ONLY in the repository."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def get_session(
        self, session_id: str, tenant_id: str, *, scope: Any
    ) -> dict[str, Any] | None:
        row = self.rows.get(session_id)
        return row if row is not None and row["tenant_id"] == tenant_id else None


async def test_run_goal_db_mode_uses_the_sessions_agent_config() -> None:
    fake = _ConfigRecordingGoalService()
    repo = _StubRepository(
        {"s-db": {"id": "s-db", "tenant_id": "t1", "agent_id": "agent-billing"}}
    )
    svc = ChatService(goal_service=fake, repository=repo)
    assert svc.get_session("s-db", "t1") is None  # nothing in process memory

    await svc.run_goal(
        session_id="s-db", tenant_id="t1", tenant_ctx=_ctx(), message_id="m1",
        user_message="Deploy the billing service to staging",
    )

    assert fake.submitted is not None
    assert fake.submitted["agent_id"] == "agent-billing"
    assert fake.ran_with == _AGENTS["agent-billing"]
    assert fake.ran_with != _AGENTS[None]


async def test_run_goal_in_memory_uses_the_sessions_agent_config() -> None:
    fake = _ConfigRecordingGoalService()
    svc = ChatService(goal_service=fake)
    session = svc.create_session("t1", agent_id="agent-billing")

    await svc.run_goal(
        session_id=session.id, tenant_id="t1", tenant_ctx=_ctx(), message_id="m1",
        user_message="Deploy the billing service to staging",
    )

    assert fake.ran_with == _AGENTS["agent-billing"]


async def test_run_goal_for_a_missing_session_fails_closed() -> None:
    import pytest

    fake = _ConfigRecordingGoalService()
    svc = ChatService(goal_service=fake, repository=_StubRepository({}))

    with pytest.raises(LookupError):
        await svc.run_goal(
            session_id="gone", tenant_id="t1", tenant_ctx=_ctx(), message_id="m1",
            user_message="Deploy the billing service to staging",
        )
    assert fake.submitted is None  # never ran on the default agent instead


async def test_channel_goal_action_uses_the_sessions_agent() -> None:
    fake = _ConfigRecordingGoalService()
    svc = ChatService(goal_service=fake)
    session = svc.create_session("t1", agent_id="agent-billing")

    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="Deploy the billing service to staging",
    )

    assert "goal" in out["actions"]
    assert fake.submitted is not None
    assert fake.submitted["agent_id"] == "agent-billing"
    assert fake.submitted["execution_context"]["session_id"] == session.id


async def test_channel_goal_action_does_not_claim_a_failed_submit_started() -> None:
    class _Failing(_FakeGoalService):
        async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("budget exceeded")

    svc = ChatService(goal_service=_Failing([]))
    session = svc.create_session("t1")

    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="Deploy the billing service to staging",
    )

    assert "started working" not in out["reply"]
    assert "could not" in out["reply"].lower()
