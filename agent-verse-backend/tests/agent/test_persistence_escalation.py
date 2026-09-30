"""CORE-14: persistence ESCALATE really asks a human; abort stops a running attempt.

ESCALATE emitted "escalating to human" and broke out of the loop — nobody was
asked and the goal simply failed. Abort controls were honoured only between
attempts, so a running attempt continued to completion.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock


from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-esc", plan=PlanTier.PROFESSIONAL, api_key_id="k")
GID = "goal-esc"


class _FailingAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, **kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            iterations=1,
            context={},
            verification_success=False,
            status="failed",
            error_message="nope",
            verification_feedback="",
        )


def _engine(gateway: Any = None, redis: Any = None, **cfg: Any) -> GoalPersistenceEngine:
    config = PersistenceConfig(
        max_attempts=cfg.pop("max_attempts", 3),
        escalate_after_failures=cfg.pop("escalate_after_failures", 2),
        base_backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        **cfg,
    )
    return GoalPersistenceEngine(config, redis=redis, hitl_gateway=gateway)


def _collector() -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    return events, _cb


def _gateway(status: ApprovalStatus) -> HITLGateway:
    gw = HITLGateway()
    gw.wait_for_approval = AsyncMock(return_value=status)  # type: ignore[method-assign]
    return gw


async def test_escalation_files_an_approval_and_approval_resumes_attempts() -> None:
    gw = _gateway(ApprovalStatus.APPROVED)
    agent = _FailingAgent()
    events, cb = _collector()

    await _engine(gw).run(
        goal="g", agent_factory=agent, tenant_ctx=T, event_callback=cb, goal_id=GID
    )

    gw.wait_for_approval.assert_awaited()  # type: ignore[attr-defined]
    assert len(gw._requests) >= 1
    req = next(iter(gw._requests.values()))
    assert req.goal_id == GID
    types = [e["type"] for e in events]
    assert "persistence_escalating" in types
    assert "persistence_escalation_approved" in types
    assert agent.calls >= 2  # attempts continued after the human said yes


async def test_rejected_escalation_stops_the_goal() -> None:
    gw = _gateway(ApprovalStatus.REJECTED)
    agent = _FailingAgent()
    events, cb = _collector()

    success, _ = await _engine(gw).run(
        goal="g", agent_factory=agent, tenant_ctx=T, event_callback=cb, goal_id=GID
    )

    assert success is False
    assert agent.calls == 1
    assert "persistence_escalation_declined" in [e["type"] for e in events]


async def test_without_a_gateway_the_event_does_not_claim_a_human_handoff() -> None:
    agent = _FailingAgent()
    events, cb = _collector()

    await _engine(None).run(
        goal="g", agent_factory=agent, tenant_ctx=T, event_callback=cb, goal_id=GID
    )

    types = [e["type"] for e in events]
    assert "persistence_escalating" not in types
    gave_up = next(e for e in events if e["type"] == "persistence_gave_up")
    assert "no human approver" in gave_up["reason"]


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


async def test_abort_during_a_running_attempt_cancels_it() -> None:
    redis = _FakeRedis()
    cancelled = asyncio.Event()

    class _SlowAgent:
        async def run(self, **kwargs: Any) -> Any:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    engine = _engine(None, redis=redis, max_attempts=3, escalate_after_failures=10)
    engine._ABORT_POLL_SECONDS = 0.01  # type: ignore[misc]
    events, cb = _collector()

    async def _abort_soon() -> None:
        await asyncio.sleep(0.05)
        redis.data[GoalPersistenceEngine.control_key("abort", T.tenant_id, GID)] = "1"

    aborter = asyncio.create_task(_abort_soon())
    success, attempts = await asyncio.wait_for(
        engine.run(
            goal="g", agent_factory=_SlowAgent(), tenant_ctx=T, event_callback=cb, goal_id=GID
        ),
        timeout=5,
    )
    await aborter

    assert success is False
    assert cancelled.is_set()
    assert len(attempts) == 1
    assert attempts[0].failure_reason == "aborted"
    assert "persistence_aborted" in [e["type"] for e in events]
