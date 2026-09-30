"""CORE-13: a persistence goal stops on an emergency stop / cancel / governance denial.

The worker's step gate raises GoalCancelledError on an emergency stop, but the
persistence engine caught it as an ordinary failed attempt and re-planned (an
LLM call each) up to max_attempts times, ending "failed" instead of stopped. A
governance denial (PermissionError) was retried the same way.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig
from app.reliability.goal_lifecycle import GoalCancelledError
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-pstop", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Agent:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def run(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc


def _engine() -> GoalPersistenceEngine:
    return GoalPersistenceEngine(
        PersistenceConfig(max_attempts=5, base_backoff_seconds=0.0, max_backoff_seconds=0.0)
    )


@pytest.mark.parametrize(
    "exc",
    [
        GoalCancelledError("Goal g stopped: tenant emergency stop is active"),
        PermissionError("Step 'deploy' requires human approval (high-risk step)"),
    ],
)
async def test_stop_or_denial_ends_the_run_after_one_attempt(exc: Exception) -> None:
    agent = _Agent(exc)
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    engine = _engine()
    with pytest.raises(type(exc)):
        await engine.run(goal="g", agent_factory=agent, tenant_ctx=T, event_callback=_cb)

    assert agent.calls == 1
    assert len(engine._attempts) == 1
    assert engine._attempts[0].success is False
    types = [e["type"] for e in events]
    assert "persistence_waiting" not in types  # no retry was scheduled
    assert "persistence_stopped" in types


async def test_an_ordinary_error_is_still_retried() -> None:
    agent = _Agent(RuntimeError("transient tool error"))
    engine = GoalPersistenceEngine(
        PersistenceConfig(max_attempts=2, base_backoff_seconds=0.0, max_backoff_seconds=0.0)
    )
    success, attempts = await engine.run(goal="g", agent_factory=agent, tenant_ctx=T)
    assert success is False
    assert agent.calls > 1
