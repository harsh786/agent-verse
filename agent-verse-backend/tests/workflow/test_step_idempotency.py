"""WF-14 / WF-48: a step in flight when its worker died is replayed safely.

Completed steps were already skipped after a crash, but the step that was
executing re-ran on re-dispatch with nothing to let the receiver recognise the
repeat. Side-effecting steps now carry a deterministic idempotency key
(``wf:<run>:<step>[:<foreach index>]``) — HTTP ``Idempotency-Key`` header, MCP
call header + ``_meta.idempotencyKey``, emit_event ``_idempotency_key`` — and a
re-dispatch that finds the step's row still ``running`` records a new attempt
that reuses the same key.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.idempotency import step_idempotency_key
from tests.workflow.test_hitl_multi_gate_resume import _RUN, _T, _WF, _HistoryRunStore


def _state(**extra: Any) -> dict[str, Any]:
    return {"run_id": "run-1", "tenant_id": "t", "step_outputs": {}, "vars": {}, **extra}


def test_key_is_deterministic_and_per_foreach_item() -> None:
    assert step_idempotency_key(_state(), "s") == "wf:run-1:s"
    assert step_idempotency_key(_state(), "s") == step_idempotency_key(_state(), "s")
    assert step_idempotency_key(_state(_foreach_ctx={"index": 3}), "s") == "wf:run-1:s:3"


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.net.ssrf_guard as g

    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("public_dns")
async def test_http_step_sends_the_same_key_on_every_execution() -> None:
    from app.workflow.steps.http_step import HTTPStepNode

    node = HTTPStepNode(
        StepDefinition(id="pay", type="http", url="https://api.example.com/pay", method="POST"),
        ContextResolver(),
    )
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.example.com/pay").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await node.execute(_state())  # type: ignore[arg-type]
        await node.execute(_state())  # type: ignore[arg-type]
    keys = [c.request.headers.get("idempotency-key") for c in route.calls]
    assert keys == ["wf:run-1:pay", "wf:run-1:pay"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("public_dns")
async def test_http_step_keeps_a_caller_supplied_key() -> None:
    from app.workflow.steps.http_step import HTTPStepNode

    node = HTTPStepNode(
        StepDefinition(
            id="pay", type="http", url="https://api.example.com/pay", method="POST",
            headers={"Idempotency-Key": "mine"},
        ),
        ContextResolver(),
    )
    with respx.mock() as mock:
        route = mock.post("https://api.example.com/pay").mock(return_value=httpx.Response(200))
        await node.execute(_state())  # type: ignore[arg-type]
    assert route.calls[0].request.headers["idempotency-key"] == "mine"


@pytest.mark.asyncio
async def test_emit_event_payload_carries_the_key() -> None:
    from app.workflow.steps.emit_event_step import EmitEventStepNode

    class _Redis:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def publish(self, channel: str, message: str) -> int:
            self.messages.append(message)
            return 1

    redis = _Redis()
    node = EmitEventStepNode(
        StepDefinition(id="e", type="emit_event", event_channel_out="c", event_payload={"a": 1}),
        ContextResolver(),
        redis=redis,
    )
    out = await node.execute(_state())  # type: ignore[arg-type]
    assert out["step_outputs"]["e"]["payload"]["_idempotency_key"] == "wf:run-1:e"
    assert '"_idempotency_key": "wf:run-1:e"' in redis.messages[0]


@pytest.mark.asyncio
async def test_tool_step_exposes_the_key_to_the_mcp_call() -> None:
    from app.mcp.client import current_idempotency_key
    from app.workflow.steps.tool_step import ToolStepNode

    seen: list[str | None] = []

    class _Client:
        async def call_tool_by_name(self, **kw: Any) -> dict[str, Any]:
            seen.append(current_idempotency_key())
            return {"ok": True}

    node = ToolStepNode(
        StepDefinition(id="t1", type="tool", tool="crm_create"), ContextResolver(),
        mcp_client=_Client(),
    )
    # crm_create is write_high (OI-2): it runs once a reviewer approved it.
    await node.execute(_state(hitl_request_id="t1", hitl_action="approve"))  # type: ignore[arg-type]
    assert seen == ["wf:run-1:t1"]
    assert current_idempotency_key() is None  # not leaked past the call


@pytest.mark.asyncio
async def test_mcp_jsonrpc_call_sends_header_and_meta() -> None:
    from app.mcp.client import _idempotency_request_parts, idempotency_scope

    with idempotency_scope("wf:r:s"):
        headers, meta = _idempotency_request_parts()
    assert headers == {"Idempotency-Key": "wf:r:s"}
    assert meta == {"_meta": {"idempotencyKey": "wf:r:s"}}
    assert _idempotency_request_parts() == ({}, {})


@pytest.mark.asyncio
async def test_redispatch_of_inflight_step_records_a_new_attempt_and_reuses_the_key() -> None:
    """The worker died while ``send`` was running: its row is still 'running'."""
    from app.workflow.compiler import WorkflowCompiler
    from app.workflow.runner import WorkflowRunner

    class _Redis:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def publish(self, channel: str, message: str) -> int:
            self.messages.append(message)
            return 1

    definition = WorkflowDefinition(
        name="crash", id=_WF,
        steps=[
            StepDefinition(id="prep", type="transform", input={"x": 1}),
            StepDefinition(
                id="send", type="emit_event", event_channel_out="c", depends_on=["prep"]
            ),
        ],
    ).to_json()

    class _Store(_HistoryRunStore):
        async def record_step_start(self, *, run_id: str, step_id: str, **kw: Any) -> str:
            self.rows.append({"run_id": run_id, "step_id": step_id, "status": "running",
                              "attempt_number": kw.get("attempt_number", 1)})
            return str(len(self.rows))

    store = _Store(definition)
    store.runs[_RUN]["status"] = "running"
    store.rows += [
        {"run_id": _RUN, "step_id": "prep", "status": "complete", "output": {"x": 1},
         "attempt_number": 1},
        {"run_id": _RUN, "step_id": "send", "status": "running", "attempt_number": 1},
    ]
    redis = _Redis()
    compiler = WorkflowCompiler(ContextResolver(), run_store=store, redis=redis)
    await WorkflowRunner(compiler=compiler, run_store=store).execute_fresh(_RUN, _WF, _T)

    assert store.runs[_RUN]["status"] == "complete"
    assert store.executions("prep") == 1  # completed work not redone
    send_rows = [r for r in store.rows if r["step_id"] == "send"]
    assert [r["attempt_number"] for r in send_rows] == [1, 2]
    assert send_rows[-1]["status"] == "complete"
    assert f'"_idempotency_key": "wf:{_RUN}:send"' in redis.messages[0]
