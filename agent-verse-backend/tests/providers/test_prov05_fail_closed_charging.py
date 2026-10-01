"""PROV-05: out-of-goal LLM charging fails closed; worker cost services are per task.

``_platform()`` swallowed resolver errors, so ``_charge`` silently skipped the
charge; a call with no goal scope and no tenant was never charged; the worker
installed its cost services as a module global inside every ``run_goal`` (no
CostTracker, so no ledger rows; last writer wins across tasks); and
``RedisCostController.try_record_and_check`` was dead fail-open code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.providers import guarded_completion as gc
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.guarded_completion import (
    DecisionBudgetExceededError,
    complete_decision,
    tenant_charge_scope,
)
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="q")], model="gpt-4o-mini")


class _Provider:
    def __init__(self) -> None:
        self._default_model = "gpt-4o-mini"
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        await asyncio.sleep(0)
        return CompletionResponse(
            content="ok", model="gpt-4o-mini", input_tokens=100, output_tokens=10
        )


@dataclass
class _Controller:
    recorded: list[str] = field(default_factory=list)

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append(tenant_ctx.tenant_id)
        return True

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return True


@dataclass
class _Tracker:
    rows: list[tuple[str, str]] = field(default_factory=list)

    async def record_llm_usage(self, **kw: Any) -> float:
        self.rows.append((kw["tenant_ctx"].tenant_id, kw["role"]))
        return 0.0


@pytest.fixture(autouse=True)
def _restore() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_cost_resolver_error_blocks_the_call() -> None:
    def _broken() -> tuple[Any, Any]:
        raise RuntimeError("redis down")

    gc.set_platform_cost_services(_broken)
    provider = _Provider()
    with pytest.raises(DecisionBudgetExceededError, match="cost services unavailable"):
        await complete_decision(provider, _req(), role="x", tenant_ctx=_ctx("t1"))
    assert provider.calls == 0


async def test_tenantless_call_is_refused_when_cost_services_are_configured() -> None:
    ctrl = _Controller()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    provider = _Provider()
    with pytest.raises(DecisionBudgetExceededError, match="no tenant"):
        await complete_decision(provider, _req(), role="x")
    assert provider.calls == 0 and ctrl.recorded == []


async def test_system_job_scope_allows_an_explicit_uncharged_system_call() -> None:
    ctrl = _Controller()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    provider = _Provider()
    with gc.system_job_scope("model_probe"):
        await complete_decision(provider, _req(), role="x")
    assert provider.calls == 1 and ctrl.recorded == []


def test_system_job_scope_rejects_unknown_jobs() -> None:
    with pytest.raises(ValueError), gc.system_job_scope("anything-goes"):
        pass


async def test_worker_cost_services_charge_each_tasks_own_tenant_and_write_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scaling import worker_cost

    ctrl, tracker = _Controller(), _Tracker()
    monkeypatch.setattr(worker_cost, "_build", lambda: (ctrl, tracker))
    worker_cost._by_loop.clear()
    worker_cost.install_worker_cost_services()

    async def _task(tid: str) -> None:
        with tenant_charge_scope(_ctx(tid)):
            await complete_decision(_Provider(), _req(), role="eval")

    await asyncio.gather(_task("tenant-a"), _task("tenant-b"))
    assert sorted(ctrl.recorded) == ["tenant-a", "tenant-b"]
    assert sorted(tracker.rows) == [("tenant-a", "eval"), ("tenant-b", "eval")]


def test_worker_cost_services_are_per_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import worker_cost

    built: list[object] = []

    def _build() -> tuple[Any, Any]:
        built.append(object())
        return built[-1], None

    monkeypatch.setattr(worker_cost, "_build", _build)
    worker_cost._by_loop.clear()

    async def _get() -> Any:
        return worker_cost.worker_cost_services()

    a1 = asyncio.run(_get())
    a2 = asyncio.run(_get())
    assert a1 is not a2  # a new loop (a new Celery task) never reuses loop-bound clients


def test_run_goal_no_longer_installs_a_global_per_task() -> None:
    import inspect

    from app.scaling import tasks

    assert "set_platform_cost_services(lambda" not in inspect.getsource(tasks)


def test_dead_fail_open_try_record_and_check_is_gone() -> None:
    from app.governance.cost import RedisCostController

    assert not hasattr(RedisCostController, "try_record_and_check")
