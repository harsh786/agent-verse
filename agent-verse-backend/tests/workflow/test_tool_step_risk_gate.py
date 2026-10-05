"""OI-2: workflow tool steps go through the tool risk gate.

A workflow ``tool`` step called ``mongodb_delete_one`` with no approval: the step
dispatched straight to the MCP client. Now it is classified like a goal's tool
call — ``destructive`` is denied, ``write_high`` waits for a human on the
workflow's durable approval barrier (the run suspends ``waiting_hitl``, nothing
downstream runs, the reviewer's decision resumes it) and only an explicit
approval runs the call.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.mcp.client import ToolCallResult
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus
from tests.workflow.test_hitl_approval_barrier import _outputs, _RunStore, _set

pytestmark = pytest.mark.asyncio


class _Store(_RunStore):
    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        return {"inputs": {}}


class _MCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool_by_name(self, **kwargs: Any) -> ToolCallResult:
        self.calls.append(kwargs)
        return ToolCallResult(
            tool_name=str(kwargs.get("tool_name")), success=True, output={"deleted": 1}
        )


def _build(
    definition: WorkflowDefinition,
) -> tuple[WorkflowRunner, WorkflowCompiler, HITLWorkflowGateway, _Store, _MCP]:
    store = _Store()
    store.register_definition(definition)
    gateway = HITLWorkflowGateway()
    mcp = _MCP()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        run_store=store,
        hitl_workflow_gateway=gateway,
        mcp_client=mcp,
    )
    runner = WorkflowRunner(compiler=compiler, run_store=store)

    async def _resume(req: WorkflowHITLRequest) -> None:
        await runner.resume_from_hitl(
            run_id=req.run_id,
            step_id=req.step_id,
            action=req.action_taken or "",
            actor_id=req.reviewed_by or "",
            note=req.note,
            form_data=req.form_data,
            tenant_id=req.tenant_id,
        )

    gateway._resume_callback = _resume
    return runner, compiler, gateway, store, mcp


def _tool(step_id: str, tool: str, depends_on: list[str] | None = None) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        type="tool",
        tool=tool,
        depends_on=depends_on or [],
        input={"collection": "orders", "filter": {"order_no": "ORD-WF-1"}},
    )


def _definition(tool: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"wf-{tool}",
        name="tool gate",
        steps=[_tool("call", tool), _set("after", ["call"])],
    )


async def test_write_high_tool_step_waits_for_approval_before_calling() -> None:
    definition = _definition("mongodb_delete_one")
    runner, compiler, gw, store, mcp = _build(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-1", inputs={})

    values = await _outputs(compiler, definition, run_id)
    assert values["status"] == WorkflowRunStatus.WAITING_HITL
    assert mcp.calls == []
    assert "after" not in values["step_outputs"]
    assert store.last_status() == WorkflowRunStatus.WAITING_HITL
    (req,) = (await gw.list_pending(tenant_id="t-oi2-1"))[0]
    assert req.step_id == "call"
    shown = {c["label"]: str(c["value"]) for c in req.context}
    assert shown["Tool"] == "mongodb_delete_one"
    assert shown["Risk"] == "write_high"
    assert "ORD-WF-1" in shown["Arguments"]


async def test_approval_runs_the_call_once_and_releases_downstream() -> None:
    definition = _definition("mongodb_delete_one")
    runner, compiler, gw, store, mcp = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-2", inputs={})
    (req,) = (await gw.list_pending(tenant_id="t-oi2-2"))[0]

    await gw.decide(req.request_id, action="approve", actor_id="reviewer-1")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert len(mcp.calls) == 1
    assert mcp.calls[0]["arguments"]["filter"] == {"order_no": "ORD-WF-1"}
    assert outs["call"]["output"] == {"deleted": 1}
    assert outs["call"]["approval"]["reviewer"] == "reviewer-1"
    assert "after" in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


async def test_rejection_never_calls_the_tool_and_fails_the_run() -> None:
    definition = _definition("mongodb_delete_one")
    runner, compiler, gw, store, mcp = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-3", inputs={})
    (req,) = (await gw.list_pending(tenant_id="t-oi2-3"))[0]

    await gw.decide(req.request_id, action="reject", actor_id="reviewer-1")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert mcp.calls == []
    assert "after" not in outs
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "reject" in str(store.statuses[-1][2].get("error") or "").lower()


async def test_destructive_tool_step_is_denied_without_an_approval() -> None:
    definition = _definition("mongodb_drop_collection")
    runner, compiler, gw, store, mcp = _build(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-4", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert mcp.calls == []
    assert (await gw.list_pending(tenant_id="t-oi2-4"))[0] == []
    assert "after" not in outs
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "destructive" in str(store.statuses[-1][2].get("error") or "")


async def test_read_tool_step_runs_without_approval() -> None:
    definition = _definition("mongodb_find")
    runner, compiler, gw, store, mcp = _build(definition)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-5", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert len(mcp.calls) == 1
    assert (await gw.list_pending(tenant_id="t-oi2-5"))[0] == []
    assert "after" in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


async def test_connection_prefixed_name_is_classified_on_the_tool() -> None:
    definition = _definition("orders-db.mongodb_drop_collection")
    runner, _compiler, _gw, store, mcp = _build(definition)

    await runner.run(workflow_id=definition.id, tenant_id="t-oi2-6", inputs={})

    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED


async def test_join_with_sibling_waits_for_the_tool_approval() -> None:
    definition = WorkflowDefinition(
        id="wf-tool-join",
        name="tool join",
        steps=[
            _tool("call", "mongodb_delete_one"),
            _set("sibling", []),
            _set("join", ["call", "sibling"]),
        ],
    )
    runner, compiler, gw, _store, mcp = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-7", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert "sibling" in outs
    assert "join" not in outs
    assert mcp.calls == []

    (req,) = (await gw.list_pending(tenant_id="t-oi2-7"))[0]
    await gw.decide(req.request_id, action="approve", actor_id="r")
    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert "join" in outs
    assert len(mcp.calls) == 1


async def test_no_approval_gateway_fails_closed() -> None:
    from app.workflow.state import WorkflowConfigurationError
    from app.workflow.steps.tool_step import ToolStepNode

    node = ToolStepNode(_tool("call", "mongodb_delete_one"), ContextResolver(), mcp_client=_MCP())
    with pytest.raises(WorkflowConfigurationError, match="approval"):
        await node.execute({"run_id": "r", "tenant_id": "t", "step_outputs": {}})  # type: ignore[typeddict-item]


async def test_cross_process_resume_runs_the_approved_call_once() -> None:
    """The decision is applied in another process (execute_resume_fresh)."""
    definition = _definition("mongodb_delete_one")
    runner, _compiler, gw, store, mcp = _build(definition)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-oi2-8", inputs={})
    assert mcp.calls == []

    await runner.execute_resume_fresh(
        run_id,
        definition.id,
        "t-oi2-8",
        step_id="call",
        action="approve",
        actor_id="reviewer-2",
    )

    assert len(mcp.calls) == 1
    assert store.last_status() == WorkflowRunStatus.COMPLETE
