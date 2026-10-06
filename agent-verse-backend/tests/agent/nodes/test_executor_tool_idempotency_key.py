"""a06-F101-04: an agent goal's side-effecting tool calls carry an idempotency key.

A redelivered goal resumes from its latest GoalCheckpoint and the action ledger
skips calls whose result was recorded, but the call in flight when the worker
crashed was re-issued with no idempotency key (``idempotency_scope`` was entered
only by the workflow tool step), so an MCP server had no way to apply it once.
The executor now sends ``goal:<goal_id>:<call fingerprint>`` (Idempotency-Key
header + ``_meta.idempotencyKey``) on every non-read dispatch: the same call of
the same goal carries the same key across redeliveries.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.goal_action_ledger import call_fingerprint
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.mcp.client import _idempotency_request_parts, current_idempotency_key
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from tests.agent.nodes.test_executor_approved_action_idempotency import _Human

T = TenantContext(tenant_id="f101-t1", plan=PlanTier.ENTERPRISE, api_key_id="f101")
WRITE_ARGS = {"collection": "orders", "document": {"order_no": "ORD-9"}}
READ_ARGS = {"collection": "orders", "filter": {"order_no": "ORD-9"}}


class _Server:
    """Records the idempotency key (and request parts) in effect for each call."""

    def __init__(self) -> None:
        self.keys: list[str | None] = []
        self.headers: list[dict[str, str]] = []

    async def call_tool(self, **kwargs: Any) -> Any:
        self.keys.append(current_idempotency_key())
        self.headers.append(_idempotency_request_parts()[0])

        class Result:
            success = True
            output = {"ok": True}
            error = ""

        return Result()


def _tools() -> ToolContext:
    return ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="mongo-1", server_name="orders-db", name="mongodb_insert_one",
                    description="insert one document", input_schema={}),
            ToolRef(server_id="mongo-1", server_name="orders-db", name="update_record",
                    description="update a record", input_schema={}),
            ToolRef(server_id="mongo-1", server_name="orders-db", name="mongodb_find",
                    description="find documents", input_schema={}),
        ],
    )


def _graph(responses: list[str], server: _Server, mode: str) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=responses),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=_Human(),
        mcp_client=server,
        autonomy_mode=mode,
    )


def _state(goal_id: str) -> AgentState:
    state = AgentState(goal="record the order", tenant_ctx=T)
    state.goal_id = goal_id
    state.context["tool_context"] = _tools()
    return state


async def _run(graph: AgentGraph, state: AgentState, step: str) -> str:
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return await graph._execute_step(step, state, T)


def _call(tool: str, args: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": args})


def _expected(goal_id: str, tool: str = "mongodb_insert_one") -> str:
    return f"goal:{goal_id}:{call_fingerprint('mongo-1', tool, WRITE_ARGS)[:32]}"


# write_high runs only in supervised mode (after approval); write_low runs autonomously.
_CASES = (("supervised", "mongodb_insert_one"), ("bounded-autonomous", "update_record"))


async def test_write_call_carries_the_goal_scoped_key_and_survives_redelivery() -> None:
    for mode, tool in _CASES:
        goal_id = f"g-f101-{mode}"
        first = _Server()
        await _run(_graph([_call(tool, WRITE_ARGS)], first, mode),
                   _state(goal_id), "Insert order ORD-9")
        assert first.keys == [_expected(goal_id, tool)], mode
        assert first.headers == [{"Idempotency-Key": _expected(goal_id, tool)}]

        # Worker crashed mid-call: the redelivered goal (fresh process, no ledger
        # entry for the in-flight call) re-issues it under the SAME key.
        again = _Server()
        await _run(_graph([_call(tool, WRITE_ARGS)], again, mode),
                   _state(goal_id), "Insert order ORD-9")
        assert again.keys == first.keys, mode

        # Another goal making the same call is a different operation.
        other = _Server()
        await _run(_graph([_call(tool, WRITE_ARGS)], other, mode),
                   _state(goal_id + "-b"), "Insert order ORD-9")
        assert other.keys != first.keys


async def test_read_call_carries_no_key() -> None:
    server = _Server()
    await _run(_graph([_call("mongodb_find", READ_ARGS)], server, "bounded-autonomous"),
               _state("g-f101-read"), "Find order ORD-9")
    assert server.keys == [None]
    assert server.headers == [{}]


async def test_key_does_not_leak_past_the_call() -> None:
    server = _Server()
    await _run(_graph([_call("update_record", WRITE_ARGS)], server, "bounded-autonomous"),
               _state("g-f101-leak"), "Update order ORD-9")
    assert server.keys[0] is not None
    assert current_idempotency_key() is None


async def test_parallel_extra_write_calls_carry_their_own_keys() -> None:
    """The 2nd+ calls of a parallel turn (``_dispatch_parallel_extra_tool_calls``)."""
    from types import SimpleNamespace

    from tests.agent.nodes.test_executor_parallel_tool_gates import _MultiToolCallProvider

    seen: dict[str, str | None] = {}

    class _Rec:
        async def call_tool(self, **kwargs: Any) -> Any:
            seen[str(kwargs["tool_name"])] = current_idempotency_key()
            return SimpleNamespace(success=True, output={"ok": True}, error="")

    calls = [
        {"name": "search_a", "input": {}},
        {"name": "update_record", "input": WRITE_ARGS},
        {"name": "add_label", "input": {"label": "vip"}},
    ]
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=_MultiToolCallProvider(calls),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        mcp_client=_Rec(),
        autonomy_mode="bounded-autonomous",
    )
    state = _state("g-f101-par")
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="mongo-1", server_name="Custom", name=n, description=n,
                    input_schema={})
            for n in ("search_a", "update_record", "add_label")
        ],
    )
    state.context["_execution_strategy"] = SimpleNamespace(
        tool_mode=SimpleNamespace(value="parallel")
    )
    await _run(graph, state, "gather data")
    assert seen["search_a"] is None  # a read
    assert seen["update_record"] == _expected("g-f101-par", "update_record")
    label_fp = call_fingerprint("mongo-1", "add_label", {"label": "vip"})[:32]
    assert seen["add_label"] == f"goal:g-f101-par:{label_fp}"
