"""Provenance: a role call is attributed to the model that ACTUALLY served it.

Live ONPREM-ROUTING-FAILOVER evidence: the routing order was [dead model on a
closed port, Qwen/Qwen3.5-4B] with every role pinned to the dead one. The goal
completed (so the calls failed over), yet ``GET /agent-runtime/traces``
role_calls named the DEAD model as the executor's: the executor recorded the
model it REQUESTED (``_exec_model``), not the one that answered. Now every
recorded role call carries the serving model, and the models it failed over
from in ``fallback_from``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.observability import cost_breakdown as cbd
from app.providers import circuit_breaker as cb
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-provenance", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.fixture(autouse=True)
def _fresh_breakers(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(cb, "_provider_cb", cb.ProviderCircuitBreaker(failure_threshold=50))
    cbd.reset_db()
    yield
    cbd._goal_breakdowns.clear()


def _req(model: str) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="12*12?")], model=model)


class _DeadThenAlive:
    """A dispatcher-like provider: ``dead-*`` models are unreachable."""

    _default_model = ""

    def __init__(self, *, echo_model: bool = True) -> None:
        self.calls: list[str] = []
        self._echo = echo_model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request.model)
        if request.model.startswith("dead"):
            raise ConnectionError("connection refused")
        return CompletionResponse(
            content="144",
            model=request.model if self._echo else "",
            input_tokens=10,
            output_tokens=2,
        )

    async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
        self.calls.append(request.model)
        if request.model.startswith("dead"):
            raise ConnectionError("connection refused")
        await on_token("144")
        return CompletionResponse(
            content="144", model=request.model if self._echo else "", input_tokens=10,
            output_tokens=2,
        )


# ── complete_with_failover (planner / verifier / reasoning path) ────────────


async def test_failover_response_names_the_serving_model_and_the_dead_one() -> None:
    dead = f"dead-{uuid.uuid4().hex[:6]}"
    provider = _DeadThenAlive(echo_model=False)  # a provider that does not echo the model

    resp = await cb.complete_with_failover(provider, _req(dead), fallback_models=["qwen"])

    assert provider.calls == [dead, "qwen"]
    assert resp.model == "qwen"  # not the requested (dead) model
    assert cb.fallback_from_of(resp) == [dead]


async def test_no_failover_records_no_fallback() -> None:
    resp = await cb.complete_with_failover(
        _DeadThenAlive(), _req("qwen"), fallback_models=["other"]
    )
    assert resp.model == "qwen" and cb.fallback_from_of(resp) == []


def test_annotate_keeps_a_provider_echoed_model_and_tolerates_odd_responses() -> None:
    resp = CompletionResponse(content="x", model="Qwen/Qwen3.5-4B")
    cb.annotate_served_model(resp, "qwen-alias", ["dead"])
    assert resp.model == "Qwen/Qwen3.5-4B" and cb.fallback_from_of(resp) == ["dead"]

    class _Frozen:
        __slots__ = ("model",)

        def __init__(self) -> None:
            self.model = ""

    odd = _Frozen()
    assert cb.annotate_served_model(odd, "qwen", ["dead"]) is odd  # never raises
    assert cb.fallback_from_of(odd) == []
    assert cb.fallback_from_of(SimpleNamespace(fallback_from="dead")) == []


# ── charge_llm_call → per-role breakdown ────────────────────────────────────


async def test_charged_role_call_records_the_served_model_with_fallback_from() -> None:
    from app.agent.nodes.llm_cost import charge_llm_call

    dead = f"dead-{uuid.uuid4().hex[:6]}"
    goal_id = f"g-{uuid.uuid4().hex}"
    resp = await cb.complete_with_failover(_DeadThenAlive(), _req(dead), fallback_models=["qwen"])
    state = SimpleNamespace(goal_id=goal_id, context={})

    await charge_llm_call(
        SimpleNamespace(), resp=resp, role="planner", model=dead, agent_state=state,
        tenant_ctx=_CTX,
    )

    (entry,) = cbd.get_breakdown(goal_id).entries
    assert (entry.role, entry.model) == ("planner", "qwen")
    assert entry.fallback_from == [dead]


def test_breakdown_merges_fallback_provenance_and_serialises_it() -> None:
    bd = cbd.GoalCostBreakdown(goal_id="g")
    bd.record("executor", "qwen", 10, 2, 0.0, ["dead-a"])
    bd.record("executor", "qwen", 10, 2, 0.0, ["dead-a", "dead-b"])
    bd.record("executor", "qwen", 10, 2, 0.0)

    (entry,) = bd.entries
    assert entry.calls == 3 and entry.fallback_from == ["dead-a", "dead-b"]
    assert bd.to_dict()["roles"][0]["fallback_from"] == ["dead-a", "dead-b"]
    restored = cbd.GoalCostBreakdown.from_state(bd.to_state())
    assert restored.entries[0].fallback_from == ["dead-a", "dead-b"]
    # A state persisted before the field existed still loads.
    legacy = {"goal_id": "g", "entries": [{"role": "planner", "model": "m", "calls": 1}]}
    assert cbd.GoalCostBreakdown.from_state(legacy).entries[0].fallback_from == []


# ── executor streaming path ─────────────────────────────────────────────────


def _graph(executor: Any, fallbacks: list[str]) -> AgentGraph:
    fake = FakeProvider()
    g = AgentGraph(planner=fake, executor=fake, verifier=fake)
    g._executor = executor
    g._role_fallback_models = lambda: list(fallbacks)  # type: ignore[method-assign]
    return g


async def _noop(_: str) -> None:
    return None


async def test_executor_stream_failover_stamps_the_serving_model() -> None:
    dead = f"dead-{uuid.uuid4().hex[:6]}"
    ex = _DeadThenAlive(echo_model=False)

    resp = await _graph(ex, ["qwen"])._stream_with_failover(_req(dead), _noop, [])

    assert ex.calls == [dead, "qwen"]
    assert resp.model == "qwen" and cb.fallback_from_of(resp) == [dead]


async def test_executor_skip_of_an_open_circuit_is_recorded_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = f"dead-{uuid.uuid4().hex[:6]}"
    ex = _DeadThenAlive()

    async def _open(key: str) -> bool:
        return dead in key

    monkeypatch.setattr(cb, "circuit_open_anywhere", _open)

    resp = await _graph(ex, ["qwen"])._stream_with_failover(_req(dead), _noop, [])

    assert ex.calls == ["qwen"]  # the open circuit was never called
    assert resp.model == "qwen" and cb.fallback_from_of(resp) == [dead]


async def test_executor_step_records_the_served_model_not_the_pinned_dead_one() -> None:
    """End to end through _execute_step: the executor's role row names the server."""
    dead = f"dead-{uuid.uuid4().hex[:6]}"
    goal_id = f"g-{uuid.uuid4().hex}"
    ex = _DeadThenAlive()
    g = _graph(ex, ["qwen"])
    g._model_router = SimpleNamespace(model_for=lambda task: dead)

    from app.agent.state import AgentState

    state = AgentState(goal="Compute 12*12", tenant_ctx=_CTX, goal_id=goal_id)
    await g._execute_step("Compute 12 multiplied by 12", state, _CTX)

    executor_rows = [e for e in cbd.get_breakdown(goal_id).entries if e.role == "executor"]
    assert executor_rows, "no executor call recorded"
    assert {e.model for e in executor_rows} == {"qwen"}
    assert all(dead in e.fallback_from for e in executor_rows)
