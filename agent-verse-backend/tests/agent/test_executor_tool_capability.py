"""Executor: tool steps vs. tool-less providers, and token_reset on stream failure."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.graph_types import StepNotExecutedError
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="exec-cap-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _state() -> AgentState:
    state = AgentState(goal="goal", tenant_ctx=T)
    state.steps.append(StepResult(description="search jira", status=StepStatus.RUNNING))
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="s1",
                server_name="Jira",
                name="jira_search",
                description="search",
                input_schema={"type": "object"},
            )
        ],
    )
    return state


async def test_tool_step_on_tool_less_executor_fails_clearly_without_calling_llm() -> None:
    executor = FakeProvider(responses=["I searched (not really)"], tool_use=False)
    graph = AgentGraph(planner=FakeProvider(), executor=executor, verifier=FakeProvider())
    with pytest.raises(StepNotExecutedError, match="tool calling"):
        await graph._execute_step("search jira for bug", _state(), T)
    assert executor.call_history == []


async def test_tool_less_step_still_runs_on_tool_less_executor() -> None:
    executor = FakeProvider(responses=["42"], tool_use=False)
    graph = AgentGraph(planner=FakeProvider(), executor=executor, verifier=FakeProvider())
    state = AgentState(goal="goal", tenant_ctx=T)
    state.steps.append(StepResult(description="compute", status=StepStatus.RUNNING))
    out = await graph._execute_step("compute 6*7", state, T)
    assert "42" in out


class _PartialThenFail:
    _default_model = "m1"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
        self.calls += 1
        await on_token("half an ans")
        raise ConnectionError("dropped")


async def test_final_attempt_failure_after_tokens_emits_token_reset() -> None:
    events: list[dict[str, Any]] = []

    async def cb(ev: dict[str, Any]) -> None:
        events.append(ev)

    fake = FakeProvider()
    g = AgentGraph(planner=fake, executor=fake, verifier=fake)
    g._event_callback = cb
    g._executor = _PartialThenFail()  # type: ignore[assignment]
    g._role_fallback_models = lambda: []  # type: ignore[method-assign]
    buf: list[str] = []

    async def on_token(t: str) -> None:
        buf.append(t)

    with pytest.raises(ConnectionError):
        await g._stream_with_failover(
            CompletionRequest(messages=[Message(role="user", content="x")], model="m1"),
            on_token,
            buf,
        )
    assert [e["type"] for e in events] == ["token_reset"]
    assert buf == []


async def test_failover_without_emitted_tokens_needs_no_reset() -> None:
    events: list[dict[str, Any]] = []

    async def cb(ev: dict[str, Any]) -> None:
        events.append(ev)

    class _FailSilently:
        _default_model = "m1"

        async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
            if request.model == "m1":
                raise ConnectionError("refused")
            return CompletionResponse(content="ok", model=request.model)

    fake = FakeProvider()
    g = AgentGraph(planner=fake, executor=fake, verifier=fake)
    g._event_callback = cb
    g._executor = _FailSilently()  # type: ignore[assignment]
    g._role_fallback_models = lambda: ["m2"]  # type: ignore[method-assign]
    buf: list[str] = []

    async def on_token(t: str) -> None:
        buf.append(t)

    resp = await g._stream_with_failover(
        CompletionRequest(messages=[Message(role="user", content="x")], model="m1"), on_token, buf
    )
    assert resp.content == "ok"
    assert events == []
