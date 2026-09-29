"""The Celery worker observes cross-replica pause / cancel signals.

Regressions:
* a pause killed the in-flight run (mid tool call) and restarted the goal from
  scratch — the worker now installs a step-boundary pause gate on the graph;
* a cancel raised GoalCancelledError into run_goal's generic handler, which
  scheduled a Celery RETRY of a goal the operator had cancelled (and on the
  last attempt marked it failed / dead-lettered).
"""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import pytest

from app.reliability.goal_lifecycle import GoalCancelledError


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 2


@pytest.fixture
def sync_redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.scaling import tasks

    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: r)
    monkeypatch.setattr(tasks, "_WORKER_SIGNAL_POLL_SECONDS", 0.01)
    return r


@pytest.mark.asyncio
async def test_pause_blocks_at_a_step_boundary_without_restarting_the_run(
    sync_redis: Any,
) -> None:
    from app.scaling import tasks

    class _Graph:
        def __init__(self) -> None:
            self._pause_gate: Any = None
            self.runs = 0
            self.steps: list[str] = []

        async def run(self, **kwargs: Any) -> _State:
            self.runs += 1
            for step in ("one", "two"):
                await self._pause_gate()
                self.steps.append(step)
                if step == "one":
                    sync_redis.set("goal_paused:g-p", "1")
                    asyncio.get_running_loop().call_later(
                        0.2, sync_redis.delete, "goal_paused:g-p"
                    )
            return _State()

    graph = _Graph()
    runner = tasks._WorkerMCPAgentRunner(graph, _no_context)
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    state = await tasks._run_with_signals(runner, "g", object(), _cb, "g-p")

    assert state.iterations == 2
    assert graph.runs == 1, "a pause must not cancel and restart the run"
    assert graph.steps == ["one", "two"]
    types = [e["type"] for e in events]
    assert types.index("goal_paused_at_step_boundary") < types.index("goal_execution_resumed")


@pytest.mark.asyncio
async def test_cancel_is_raised_at_the_next_step_boundary(sync_redis: Any) -> None:
    from app.scaling import tasks

    class _Graph:
        def __init__(self) -> None:
            self._pause_gate: Any = None
            self.steps: list[str] = []

        async def run(self, **kwargs: Any) -> _State:
            for step in ("one", "two"):
                await self._pause_gate()
                self.steps.append(step)
                sync_redis.set("goal_cancelled:g-c", "1")
            return _State()

    graph = _Graph()
    with pytest.raises(GoalCancelledError):
        await tasks._run_with_signals(
            tasks._WorkerMCPAgentRunner(graph, _no_context), "g", object(), _noop_cb, "g-c"
        )
    assert graph.steps == ["one"]


def test_run_goal_reports_a_cancelled_goal_without_retrying(
    monkeypatch: pytest.MonkeyPatch, sync_redis: Any
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    statuses: list[str] = []

    async def _fake_decrement(tenant_id: str, redis_url: str) -> None:
        statuses.append("decremented")

    monkeypatch.setattr(tasks, "_decrement_after_completion", _fake_decrement)

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> _State:
            sync_redis.set("goal_cancelled:g-run-c", "1")
            await self._pause_gate()
            raise AssertionError("the gate must raise on cancel")

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)

    def _no_retry(*a: Any, **k: Any) -> Any:
        raise AssertionError("a cancelled goal must not be retried")

    monkeypatch.setattr(tasks.run_goal, "retry", _no_retry)
    result = tasks.run_goal.run("g-run-c", "tenant-c", "goal", "normal", False)

    assert result["status"] == "cancelled"
    assert statuses == ["decremented"]


async def _no_context() -> tuple[None, None, None]:
    return None, None, None


async def _noop_cb(event: dict[str, Any]) -> None:
    return None
