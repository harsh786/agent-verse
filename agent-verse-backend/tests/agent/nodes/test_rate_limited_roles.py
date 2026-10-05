"""P5-1: the planner and the verifier survive provider throttling (HTTP 429).

P0 baseline §4.5 / §4.9: ``openai.RateLimitError`` escaped ``_node_plan`` and
``_node_verify`` (they caught only RuntimeError/TimeoutError), so a single
throttled call failed the goal with a raw "Error code: 429".
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers import circuit_breaker as cb
from app.providers import rate_limit as rl
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="p5-ratelimit-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _429() -> openai.RateLimitError:
    resp = httpx.Response(
        429, headers={"retry-after": "1"}, request=httpx.Request("POST", "https://llm.test")
    )
    return openai.RateLimitError("Error code: 429", response=resp, body=None)


class _ThrottledThen(FakeProvider):
    """A FakeProvider that is throttled ``n`` times before answering."""

    def __init__(self, n: int, **kw: Any) -> None:
        super().__init__(**kw)
        self.throttles_left = n
        self.raw_calls = 0

    async def complete(self, request: Any) -> Any:
        self.raw_calls += 1
        if self.throttles_left > 0:
            self.throttles_left -= 1
            raise _429()
        return await super().complete(request)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cb, "_provider_cb", cb.ProviderCircuitBreaker(failure_threshold=1))

    async def _skip(_d: float) -> None:
        return None

    monkeypatch.setattr(rl, "_sleep", _skip)
    monkeypatch.setattr(rl, "current_policy", lambda: rl.RateLimitPolicy(max_retries=3))


def _graph(**roles: Any) -> AgentGraph:
    g = AgentGraph(
        planner=roles.get("planner") or FakeProvider(responses=["1. step one"]),
        executor=FakeProvider(responses=["out"]),
        verifier=roles.get("verifier") or FakeProvider(responses=['{"success": true}']),
    )
    g._role_fallback_models = lambda: []  # type: ignore[method-assign]
    return g


def _verified_state() -> AgentState:
    s = AgentState(goal="summarise the note", tenant_ctx=T)
    s.steps.append(StepResult(description="step 1", status=StepStatus.COMPLETE, output="done"))
    return s


async def test_verifier_retries_a_429_then_succeeds() -> None:
    verifier = _ThrottledThen(2, responses=['{"success": true, "reason": "ok"}'])
    g = _graph(verifier=verifier)
    out = await g._node_verify({"agent_state": _verified_state(), "tenant_ctx": T})
    assert out["agent_state"].verification_success is True
    assert verifier.raw_calls == 3


async def test_verifier_persistent_429_fails_honestly_not_raw() -> None:
    verifier = _ThrottledThen(99, responses=['{"success": true}'])
    g = _graph(verifier=verifier)
    with pytest.raises(PermissionError, match=r"Verification unavailable: .*rate-limited"):
        await g._node_verify({"agent_state": _verified_state(), "tenant_ctx": T})


async def test_planner_retries_a_429_then_plans() -> None:
    planner = _ThrottledThen(1, responses=['{"steps": ["look up the note"]}'])
    g = _graph(planner=planner)
    out = await g._node_plan(
        {"agent_state": AgentState(goal="summarise the note", tenant_ctx=T), "tenant_ctx": T}
    )
    assert planner.raw_calls >= 2
    assert out["agent_state"].plan


async def test_planner_persistent_429_fails_honestly_not_raw() -> None:
    planner = _ThrottledThen(99, responses=["1. x"])
    g = _graph(planner=planner)
    with pytest.raises(PermissionError, match=r"Planning unavailable: .*rate-limited"):
        await g._node_plan(
            {"agent_state": AgentState(goal="summarise the note", tenant_ctx=T), "tenant_ctx": T}
        )
