"""complete_decision: narrow-decision LLM calls are charged and circuit-broken.

Guardrail judges, routers, eval scorers and RAG graders used to call
``provider.complete`` directly — no budget, no ledger, no circuit breaker, no
timeout. These tests pin the shared path they now use.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers import guarded_completion as gc
from app.providers.base import CompletionRequest, CompletionResponse, Message


class FakeProvider:
    def __init__(self, reply: str = "ok", *, hang: bool = False, fail: bool = False) -> None:
        # A unique model per instance keeps each test on its own circuit.
        self._default_model = f"test-model-{uuid.uuid4().hex[:8]}"
        self.reply, self.hang, self.fail, self.calls = reply, hang, fail, 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(30)
        if self.fail:
            raise ConnectionError("provider down")
        return CompletionResponse(
            content=self.reply, model="gpt-4o-mini", input_tokens=1000, output_tokens=500
        )


class FakeController:
    def __init__(self, *, allow: bool = True, remaining: bool = True) -> None:
        self.allow, self.remaining = allow, remaining
        self.recorded: list[tuple[str, float, str]] = []

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append((goal_id, cost_usd, tenant_ctx.tenant_id))
        return self.allow

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return self.remaining


class FakeTracker:
    def __init__(self) -> None:
        self.usage: list[dict[str, Any]] = []

    async def record_llm_usage(self, **kwargs: Any) -> None:
        self.usage.append(kwargs)


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="classify")], model="")


@pytest.fixture(autouse=True)
def _restore_platform_services() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_outside_a_goal_charges_the_tenant_under_a_per_call_id() -> None:
    ctrl, tracker = FakeController(), FakeTracker()
    gc.set_platform_cost_services(lambda: (ctrl, tracker))
    resp = await gc.complete_decision(FakeProvider(), _req(), role="chat_intent", tenant_id="t1")
    assert resp.content == "ok"
    [(goal_id, cost, tenant)] = ctrl.recorded
    assert goal_id.startswith("decision:chat_intent:") and cost > 0 and tenant == "t1"
    assert tracker.usage and tracker.usage[0]["role"] == "chat_intent"


async def test_inside_a_goal_charges_that_goal_like_the_planner() -> None:
    ctrl, tracker = FakeController(), FakeTracker()
    graph = SimpleNamespace(_cost_controller=ctrl, _cost_tracker=tracker, _state_lock=None)
    state = SimpleNamespace(goal_id="goal-7", context={})
    tenant = SimpleNamespace(tenant_id="t1")
    with gc.goal_charge_scope(graph, state, tenant):
        await gc.complete_decision(FakeProvider(), _req(), role="guardrail_judge")
    assert [r[0] for r in ctrl.recorded] == ["goal-7"]
    assert state.context["total_cost_usd"] > 0


async def test_goal_budget_denial_latches_and_blocks_the_next_decision() -> None:
    ctrl = FakeController(allow=False)
    graph = SimpleNamespace(_cost_controller=ctrl, _cost_tracker=None, _state_lock=None)
    state = SimpleNamespace(goal_id="goal-8", context={})
    provider = FakeProvider()
    with gc.goal_charge_scope(graph, state, SimpleNamespace(tenant_id="t1")):
        await gc.complete_decision(provider, _req(), role="agent_router")
        assert state.context["_budget_exhausted"] is True
        with pytest.raises(gc.DecisionBudgetExceededError):
            await gc.complete_decision(provider, _req(), role="agent_router")
    assert provider.calls == 1  # the second call never reached the provider


async def test_tenant_over_budget_is_refused_before_the_call() -> None:
    gc.set_platform_cost_services(lambda: (FakeController(remaining=False), None))
    provider = FakeProvider()
    with pytest.raises(gc.DecisionBudgetExceededError):
        await gc.complete_decision(provider, _req(), role="eval_accuracy", tenant_id="t1")
    assert provider.calls == 0


async def test_denied_charge_outside_a_goal_raises() -> None:
    gc.set_platform_cost_services(lambda: (FakeController(allow=False), None))
    with pytest.raises(gc.DecisionBudgetExceededError):
        await gc.complete_decision(FakeProvider(), _req(), role="chat_intent", tenant_id="t1")


async def test_hung_provider_times_out_instead_of_stalling() -> None:
    provider = FakeProvider(hang=True)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            gc.complete_decision(provider, _req(), role="guardrail_judge", timeout_seconds=0.05),
            timeout=5,
        )


async def test_provider_failures_open_the_circuit_then_fail_fast() -> None:
    from app.providers.circuit_breaker import _provider_cb, breaker_key

    provider = FakeProvider(fail=True)
    key = breaker_key(provider, _req())
    for _ in range(_provider_cb._failure_threshold):
        with pytest.raises(ConnectionError):
            await gc.complete_decision(provider, _req(), role="agent_router", charge=False)
    calls = provider.calls
    with pytest.raises(RuntimeError, match="circuit open"):
        await gc.complete_decision(provider, _req(), role="agent_router", charge=False)
    assert provider.calls == calls  # failed fast, provider not called
    assert _provider_cb.is_open(key)


async def test_budget_refusals_do_not_open_the_provider_circuit() -> None:
    from app.providers.circuit_breaker import _provider_cb, breaker_key

    class Refusing(FakeProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            raise gc.DecisionBudgetExceededError("budget")

    provider = Refusing()
    for _ in range(10):
        with pytest.raises(gc.DecisionBudgetExceededError):
            await gc.complete_decision(provider, _req(), role="rag_corrective", charge=False)
    assert not _provider_cb.is_open(breaker_key(provider, _req()))


async def test_charge_false_does_not_charge() -> None:
    ctrl = FakeController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    await gc.complete_decision(
        FakeProvider(), _req(), role="rag_corrective", tenant_id="t1", charge=False
    )
    assert ctrl.recorded == []
