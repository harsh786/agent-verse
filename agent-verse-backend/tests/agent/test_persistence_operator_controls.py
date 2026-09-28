"""The persistence engine honours operator controls and runs attempts under the goal id.

Audit item 4: POST /goals/{id}/persistence/{abort,skip-strategy,inject-guidance} wrote
Redis keys nothing read, and attempts called ``agent.run()`` without a goal_id.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig, RetryStrategy
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="pers-t", plan=PlanTier.ENTERPRISE, api_key_id="k")
GID = "goal-123"


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


class _Agent:
    """Fails every attempt; records what each attempt was run with."""

    def __init__(self, redis: _FakeRedis, on_attempt: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._on_attempt = on_attempt

    async def run(self, *, goal: str, tenant_ctx: Any, event_callback: Any = None, **kw: Any):
        self.calls.append({"goal": goal, **kw})
        if self._on_attempt is not None:
            self._on_attempt(len(self.calls))
        return SimpleNamespace(
            iterations=1,
            context={},
            verification_success=False,
            status="failed",
            error_message="nope",
            verification_feedback="",
        )


def _engine(redis: _FakeRedis, **cfg: Any) -> GoalPersistenceEngine:
    config = PersistenceConfig(
        max_attempts=cfg.pop("max_attempts", 3), base_backoff_seconds=0.0, **cfg
    )
    return GoalPersistenceEngine(config=config, redis=redis)


def _key(kind: str) -> str:
    return GoalPersistenceEngine.control_key(kind, T.tenant_id, GID)


async def test_attempts_run_under_the_goal_id() -> None:
    redis = _FakeRedis()
    agent = _Agent(redis)
    await _engine(redis, max_attempts=2).run(
        goal="g", agent_factory=agent, tenant_ctx=T, goal_id=GID
    )
    assert [c.get("goal_id") for c in agent.calls] == [GID, GID]
    assert [c.get("attempt") for c in agent.calls] == [1, 2]


async def test_abort_stops_the_loop_between_attempts() -> None:
    redis = _FakeRedis()
    agent = _Agent(redis, on_attempt=lambda n: redis.data.__setitem__(_key("abort"), "1"))
    events: list[dict[str, Any]] = []

    async def cb(e: dict[str, Any]) -> None:
        events.append(e)

    ok, attempts = await _engine(redis, max_attempts=5).run(
        goal="g", agent_factory=agent, tenant_ctx=T, goal_id=GID, event_callback=cb
    )
    assert ok is False
    assert len(agent.calls) == 1
    assert any(e["type"] == "persistence_aborted" for e in events)


async def test_injected_guidance_reaches_the_next_attempt() -> None:
    redis = _FakeRedis()
    agent = _Agent(
        redis,
        on_attempt=lambda n: (
            redis.data.__setitem__(_key("guidance"), "use the staging API") if n == 1 else None
        ),
    )
    await _engine(redis, max_attempts=2).run(
        goal="g", agent_factory=agent, tenant_ctx=T, goal_id=GID
    )
    assert "use the staging API" not in agent.calls[0]["goal"]
    assert "use the staging API" in agent.calls[1]["goal"]
    assert _key("guidance") not in redis.data  # consumed


async def test_skip_strategy_advances_the_rotation() -> None:
    redis = _FakeRedis()
    engine = _engine(redis, max_attempts=2, strategy_switch_after=5)
    assert engine._pick_strategy(2) == RetryStrategy.SAME_APPROACH
    redis.data[_key("skip_strategy")] = "1"
    assert await engine._apply_controls(T.tenant_id, GID) is False
    assert engine._pick_strategy(2) == RetryStrategy.DIFFERENT_TOOLS


async def test_retry_attempt_does_not_resume_previous_attempt_checkpoint() -> None:
    from unittest.mock import AsyncMock

    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    provider = FakeProvider(responses=['{"steps": ["s"]}', "out", '{"success": true}'])
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._load_checkpoint = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await graph.run(goal="g", tenant_ctx=T, goal_id=GID, attempt=1)
    assert graph._load_checkpoint.await_count == 1
    state = await graph.run(goal="g", tenant_ctx=T, goal_id=GID, attempt=2)
    assert graph._load_checkpoint.await_count == 1  # attempt 2 did not resume attempt 1
    assert state.goal_id == GID
