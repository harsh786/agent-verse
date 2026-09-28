"""Regression: persistence mode (retry until success) was never run on the worker.

GoalService runs ``persistence_mode`` goals through GoalPersistenceEngine, but
queued goals (every production goal) are executed by the Celery worker, which had
no reference to the engine — a persistence goal got exactly one attempt.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


from app.agent.state import AgentState, GoalStatus
from app.scaling import tasks
from app.tenancy.context import PlanTier, TenantContext



T = TenantContext(tenant_id="wp-t", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _FlakyRunner:
    """Fails the first N attempts, then completes."""

    def __init__(self, fail_first: int) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_first = fail_first

    async def run(self, **kwargs: Any) -> AgentState:
        self.calls.append(kwargs)
        state = AgentState(goal=kwargs["goal"], tenant_ctx=kwargs["tenant_ctx"])
        if len(self.calls) <= self._fail_first:
            state.status = GoalStatus.FAILED
            state.error_message = f"attempt {len(self.calls)} failed"
        else:
            state.status = GoalStatus.COMPLETE
            state.verification_success = True
        return state


def _config(max_attempts: int = 3) -> Any:
    return tasks._worker_persistence_config(
        {"max_attempts": max_attempts, "base_backoff_seconds": 0.0, "max_backoff_seconds": 0.0},
        goal_timeout_s=60.0,
    )


async def test_persistent_worker_runner_retries_until_success() -> None:
    inner = _FlakyRunner(fail_first=2)
    runner = tasks._PersistentWorkerRunner(inner, config=_config(3))

    state = await runner.run(
        goal="do the thing", tenant_ctx=T, initial_context={"k": "v"}, goal_id="g-1"
    )

    assert state.status is GoalStatus.COMPLETE
    assert len(inner.calls) == 3
    # Each attempt runs under the real goal id with its own attempt number and
    # keeps the worker's initial context.
    assert [c["attempt"] for c in inner.calls] == [1, 2, 3]
    assert all(c["goal_id"] == "g-1" for c in inner.calls)
    assert all(c["initial_context"] == {"k": "v"} for c in inner.calls)


async def test_persistent_worker_runner_exhaustion_is_failed() -> None:
    inner = _FlakyRunner(fail_first=99)
    runner = tasks._PersistentWorkerRunner(inner, config=_config(2))

    state = await runner.run(goal="do the thing", tenant_ctx=T, goal_id="g-2")

    assert state.status is GoalStatus.FAILED
    assert len(inner.calls) == 2
    assert "2 persistence attempt" in (state.error_message or "")


async def test_persistence_config_is_bounded_by_goal_timeout() -> None:
    cfg = tasks._worker_persistence_config({"total_timeout_seconds": 0}, goal_timeout_s=100.0)
    assert 0 < cfg.total_timeout_seconds <= 90.0
    cfg2 = tasks._worker_persistence_config({"total_timeout_seconds": 10}, goal_timeout_s=100.0)
    assert cfg2.total_timeout_seconds == 10.0


async def test_goal_persistence_settings_reads_flag_and_profile() -> None:
    session = MagicMock()
    exec_ctx = {
        "persistence_mode": False,
        "persistence_config": {"max_attempts": 4},
        "runtime_profile": {"agent_patterns": {"persistence_mode": True}},
    }
    session.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=exec_ctx)))

    class _Ctx:
        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, *a: object) -> None:
            return None

    with (
        patch("app.db.session.get_session_factory", return_value=lambda: _Ctx()),
        patch("app.db.rls.sqlalchemy_rls_context", return_value=_Ctx()),
    ):
        mode, cfg = await tasks._goal_persistence_settings("g1", "t1")

    assert mode is True  # the admitted profile wins over the raw flag
    assert cfg == {"max_attempts": 4}


def test_run_goal_wires_the_persistent_runner() -> None:
    src = inspect.getsource(tasks.run_goal)
    assert "_goal_persistence_settings(goal_id, tenant_id)" in src
    assert "_PersistentWorkerRunner(" in src


async def test_worker_mcp_runner_forwards_attempt() -> None:
    inner = MagicMock()
    inner.run = AsyncMock(return_value="state")

    async def _ctx_factory() -> tuple[None, None, None]:
        return None, None, None

    runner = tasks._WorkerMCPAgentRunner(inner, _ctx_factory)
    await runner.run(goal="g", tenant_ctx=T, goal_id="g1", attempt=2)
    assert inner.run.await_args.kwargs["attempt"] == 2
