"""Worker goals write their planner / executor / verifier LLM calls to the cost ledger.

Regression (live stack, 2026-10-06): goals run by the Celery worker completed
normally, but none of their planner / executor / verifier calls produced a
``cost_ledger`` row — only out-of-goal decision calls (OCR vision) did.
``run_goal`` built the AgentGraph with ``cost_tracker=None``, so
``charge_llm_call`` (planner / verifier / reasoning / in-goal decision calls) and
the executor's step-1b ledger write both skipped the token ledger — and its
per-goal / per-tenant CostTracker Redis counters — without a trace (the ledger
write was also wrapped in ``contextlib.suppress(Exception)``).

The worker graph now gets :class:`app.scaling.worker_cost.WorkerCostTracker`,
which resolves the SAME per-event-loop ``CostTracker`` the worker's decision-call
charges use, so each goal LLM call is ledgered exactly once on the worker; the
API path keeps handing the graph ``app.state.cost_tracker``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.tenancy.context import PlanTier, TenantContext

GOAL_ID = "g-cost-ledger"
TENANT_ID = "t-cost-ledger"
CTX = TenantContext(tenant_id=TENANT_ID, plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


def _resp(model: str = "gpt-4o", prompt: int = 1000, completion: int = 500) -> Any:
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        input_tokens=prompt,
        output_tokens=completion,
        content="ok",
    )


class _FakeTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.loops: list[asyncio.AbstractEventLoop] = []

    async def record_llm_usage(self, **kwargs: Any) -> float:
        self.calls.append(kwargs)
        self.loops.append(asyncio.get_running_loop())
        return 0.01


def install_worker_harness(
    monkeypatch: pytest.MonkeyPatch, *, stub_db: bool = True
) -> dict[str, Any]:
    """Run ``run_goal`` for real up to the graph, whose ``run`` charges goal LLM calls.

    The stubbed ``run`` makes the same cost calls a real goal makes, against the
    graph ``run_goal`` assembled: a planner and a verifier call through
    ``charge_llm_call`` and an executor call through the graph's ``_cost_tracker``
    (executor_mixin step 1b).
    """
    import app.agent.graph as graph_mod
    from app.agent.nodes.llm_cost import charge_llm_call
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    seen: dict[str, Any] = {"graphs": [], "charges": []}
    real_graph_cls = graph_mod.AgentGraph

    class _Graph(real_graph_cls):  # type: ignore[misc, valid-type]
        async def run(self, **kwargs: Any) -> Any:
            seen["graphs"].append(self)
            agent_state = SimpleNamespace(goal_id=kwargs["goal_id"], context={})
            tenant_ctx = kwargs["tenant_ctx"]
            for role in ("planner", "verifier"):
                seen["charges"].append(
                    await charge_llm_call(
                        self,
                        resp=_resp(),
                        role=role,
                        model="gpt-4o",
                        agent_state=agent_state,
                        tenant_ctx=tenant_ctx,
                    )
                )
            if self._cost_tracker is not None:
                await self._cost_tracker.record_llm_usage(
                    model="gpt-4o",
                    prompt_tokens=200,
                    completion_tokens=100,
                    tenant_ctx=tenant_ctx,
                    goal_id=kwargs["goal_id"],
                    agent_id=None,
                    role="executor",
                )
            return _State()

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    async def _update(self: Any, *a: Any, **k: Any) -> bool:
        return True

    async def _ctx(goal_id: str, tenant_id: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(GoalService, "_db_merge_context_key", _noop)
    monkeypatch.setattr(GoalService, "_db_ensure_goal_row", _noop)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    if stub_db:

        class _NoDb:
            async def __aenter__(self) -> Any:
                raise RuntimeError("no database in this unit test")

            async def __aexit__(self, *a: object) -> None:
                return None

        monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: _NoDb())
        # No Redis: the in-memory budget controller (the suite's REDIS_URL is unreachable).
        monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setattr(tasks, "_goal_execution_context", _ctx)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def run_worker_goal() -> dict[str, Any]:
    from app.scaling import tasks

    result: dict[str, Any] = tasks.run_goal.run(
        GOAL_ID, TENANT_ID, "write a report", "normal", False
    )
    return result


def test_worker_goal_graph_gets_a_ledger_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import worker_cost

    seen = install_worker_harness(monkeypatch)
    tracker = _FakeTracker()
    monkeypatch.setattr(worker_cost, "_build", lambda: (None, tracker))

    result = run_worker_goal()

    assert result["status"] == "complete", result
    graph = seen["graphs"][-1]
    assert isinstance(graph._cost_tracker, worker_cost.WorkerCostTracker)
    # planner + verifier (charge_llm_call) + executor (step 1b): each ledgered ONCE.
    assert [c["role"] for c in tracker.calls] == ["planner", "verifier", "executor"]
    assert {c["goal_id"] for c in tracker.calls} == {GOAL_ID}
    assert {c["tenant_ctx"].tenant_id for c in tracker.calls} == {TENANT_ID}
    assert {c["model"] for c in tracker.calls} == {"gpt-4o"}
    assert tracker.calls[0]["prompt_tokens"] == 1000
    assert tracker.calls[0]["completion_tokens"] == 500
    assert all(cost > 0 for cost in seen["charges"])


async def test_worker_cost_tracker_uses_the_running_loops_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The facade resolves the per-loop tracker at call time (async clients are loop-bound)."""
    from app.scaling import worker_cost

    built: list[_FakeTracker] = []

    def _build() -> tuple[Any, Any]:
        built.append(_FakeTracker())
        return None, built[-1]

    monkeypatch.setattr(worker_cost, "_build", _build)
    facade = worker_cost.WorkerCostTracker()  # built outside any loop, like run_goal

    await facade.record_llm_usage(
        model="m", prompt_tokens=1, completion_tokens=1, tenant_ctx=CTX, goal_id="g", role="r"
    )
    await facade.record_llm_usage(
        model="m", prompt_tokens=2, completion_tokens=2, tenant_ctx=CTX, goal_id="g", role="r"
    )

    assert len(built) == 1  # one tracker per loop, shared with decision-call charges
    assert worker_cost.worker_cost_services()[1] is built[0]
    assert [c["prompt_tokens"] for c in built[0].calls] == [1, 2]
    assert built[0].loops == [asyncio.get_running_loop()] * 2


async def test_worker_ledger_increments_goal_and_tenant_redis_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the facade the real CostTracker bumps cost:goal / cost:daily counters."""
    from app.agent.nodes.llm_cost import charge_llm_call
    from app.intelligence.cost_tracker import CostTracker
    from app.scaling import worker_cost

    class _Redis:
        def __init__(self) -> None:
            self.values: dict[str, float] = {}

        async def incrbyfloat(self, key: str, amount: float) -> float:
            self.values[key] = self.values.get(key, 0.0) + float(amount)
            return self.values[key]

        async def expire(self, key: str, ttl: int) -> bool:
            return True

        async def get(self, key: str) -> Any:
            return None

        async def set(self, *a: Any, **k: Any) -> bool:
            return True

        async def publish(self, *a: Any, **k: Any) -> int:
            return 0

    redis = _Redis()
    tracker = CostTracker(redis=redis, db_factory=None)
    monkeypatch.setattr(worker_cost, "_build", lambda: (None, tracker))
    graph = SimpleNamespace(
        _cost_controller=None, _cost_tracker=worker_cost.WorkerCostTracker(), _state_lock=None
    )

    cost = await charge_llm_call(
        graph,
        resp=_resp(),
        role="planner",
        model="gpt-4o",
        agent_state=SimpleNamespace(goal_id=GOAL_ID, context={}),
        tenant_ctx=CTX,
    )

    assert cost > 0
    assert redis.values[f"cost:goal:{GOAL_ID}"] == pytest.approx(cost)
    daily = [k for k in redis.values if k.startswith(f"cost:daily:{TENANT_ID}:")]
    assert len(daily) == 1 and redis.values[daily[0]] == pytest.approx(cost)


async def test_worker_tracker_leaves_the_daily_budget_counter_to_the_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cost:daily:{tenant}:{day}`` is the RedisCostController's budget counter too.

    Its check_and_record increments it for every charged call; a tracker sharing
    that Redis must not add the same call again (double billing of the tenant's
    daily budget). It still bumps its own per-goal counter and writes the ledger.
    """
    from app.governance.cost import RedisCostController
    from app.intelligence.cost_tracker import CostTracker
    from app.scaling import worker_cost

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # never connected here
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None)
    controller, tracker = worker_cost._build()
    assert isinstance(controller, RedisCostController)
    assert isinstance(tracker, CostTracker)
    assert tracker._count_daily_spend is False

    class _Redis:
        def __init__(self) -> None:
            self.incremented: list[str] = []

        async def incrbyfloat(self, key: str, amount: float) -> float:
            self.incremented.append(key)
            return float(amount)

        async def expire(self, key: str, ttl: int) -> bool:
            return True

        async def get(self, key: str) -> Any:
            return None

        async def set(self, *a: Any, **k: Any) -> bool:
            return True

    redis = _Redis()
    owned = CostTracker(redis=redis, count_daily_spend=False)
    await owned.record_llm_usage(
        model="gpt-4o", prompt_tokens=10, completion_tokens=5, tenant_ctx=CTX, goal_id=GOAL_ID
    )
    assert redis.incremented == [f"cost:goal:{GOAL_ID}"]


async def test_ledger_failure_is_logged_and_the_goal_keeps_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.nodes import llm_cost

    warnings: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            warnings.append((event, kw))

    class _Broken:
        async def record_llm_usage(self, **kwargs: Any) -> float:
            raise RuntimeError("permission denied for table cost_ledger")

    monkeypatch.setattr(llm_cost, "logger", _Log())
    graph = SimpleNamespace(_cost_controller=None, _cost_tracker=_Broken(), _state_lock=None)

    cost = await llm_cost.charge_llm_call(
        graph,
        resp=_resp(),
        role="verifier",
        model="gpt-4o",
        agent_state=SimpleNamespace(goal_id=GOAL_ID, context={}),
        tenant_ctx=CTX,
    )

    assert cost > 0  # charged, not raised
    events = [e for e, _ in warnings]
    assert "llm_cost_ledger_record_failed" in events
    kw = dict(warnings)["llm_cost_ledger_record_failed"]
    assert kw["role"] == "verifier" and kw["goal_id"] == GOAL_ID
    assert "permission denied" in kw["error"]


async def test_sub_agent_ledger_rows_go_to_the_parent_goal() -> None:
    from app.agent.nodes.llm_cost import charge_llm_call

    tracker = _FakeTracker()
    graph = SimpleNamespace(_cost_controller=None, _cost_tracker=tracker, _state_lock=None)

    await charge_llm_call(
        graph,
        resp=_resp(),
        role="planner",
        model="gpt-4o",
        agent_state=SimpleNamespace(
            goal_id="parent-1-sg-0-abc", context={"_budget_goal_id": "parent-1"}
        ),
        tenant_ctx=CTX,
    )

    assert tracker.calls[0]["goal_id"] == "parent-1"


def test_api_goal_graph_gets_the_app_state_cost_tracker() -> None:
    """The API path (GoalService) hands the graph ``app.state.cost_tracker``."""
    from app.services.goal_service import GoalService

    tracker = _FakeTracker()
    graph = GoalService()._make_agent_loop_for_tenant(CTX, SimpleNamespace(cost_tracker=tracker))

    assert graph._cost_tracker is tracker
