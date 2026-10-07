"""a08-F198-01: the executor consults every applicable breaker, through the
Redis-shared (async) API.

* ``get("llm") or get(tool_name)`` skipped a tool's breaker whenever an LLM
  breaker existed (every API goal has one).
* The executor called the sync ``can_call`` / ``record_*``, which a
  ``RedisCircuitBreaker`` routes to its per-instance in-memory fallback: a
  provider outage seen by one replica/worker never opened the circuit for the
  others, and this replica's failures never reached Redis.
"""

from __future__ import annotations

import time
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent.graph import AgentGraph
from app.agent.graph_types import StepNotExecutedError
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.redis_circuit_breaker import build_llm_circuit_breaker
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="t-cb", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _graph(breakers: dict[str, Any], executor: Any = None) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do thing"]}']),
        executor=executor or FakeProvider(responses=["real output"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        circuit_breakers=breakers,
    )


def _state(step: str) -> AgentState:
    state = AgentState(goal="goal", tenant_ctx=T)
    state.goal_id = "goal-1"
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return state


async def test_an_open_tool_breaker_gates_the_step_even_with_an_llm_breaker() -> None:
    tool_breaker = CircuitBreaker(failure_threshold=1)
    tool_breaker.record_failure()  # OPEN
    executor = FakeProvider(responses=["must not run"])
    graph = _graph({"llm": CircuitBreaker(), "salesforce": tool_breaker}, executor)

    step = "call salesforce to fetch records"
    with pytest.raises(StepNotExecutedError, match="salesforce"):
        await graph._execute_step(step, _state(step), T)
    assert executor.call_history == []


async def test_a_circuit_opened_by_another_replica_stops_this_one() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # Another replica / worker opened the tenant's LLM circuit in Redis.
    await redis.set("cb:t-cb:llm_provider:state", "open", ex=240)
    await redis.set("cb:t-cb:llm_provider:opened_at", str(time.time()), ex=240)
    executor = FakeProvider(responses=["must not run"])
    graph = _graph({"llm": build_llm_circuit_breaker(redis, T.tenant_id)}, executor)

    with pytest.raises(StepNotExecutedError, match="Circuit breaker open"):
        await graph._execute_step("answer a question", _state("answer a question"), T)
    assert executor.call_history == []


class _RaisingProvider(FakeProvider):
    async def stream_tokens(self, request: Any, on_token: Any) -> Any:
        raise RuntimeError("provider connection reset")

    async def complete(self, request: Any) -> Any:
        raise RuntimeError("provider connection reset")


async def test_llm_failures_are_recorded_in_redis_for_the_whole_fleet() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    graph = _graph({"llm": build_llm_circuit_breaker(redis, T.tenant_id)}, _RaisingProvider())

    with pytest.raises(RuntimeError):
        await graph._execute_step("answer a question", _state("answer a question"), T)

    assert await redis.get("cb:t-cb:llm_provider:failures") == "1"
