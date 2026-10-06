"""QA-7: workflow tool steps honour the tenant's governance policies.

A workflow ``tool`` step only applied the tool risk tier, so a tenant policy
denying ``mongodb_find`` (or requiring an approval for it) was ignored by every
workflow run. The tenant's PolicyEngine (and its policy-as-code rules) is now
evaluated BEFORE the risk gate: a deny fails the step without calling the
connector, a require_approval suspends the run on the workflow's durable
approval barrier, and no matching policy leaves the step as it was.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.policies import Policy, PolicyEngine
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus
from tests.workflow.test_hitl_approval_barrier import _outputs, _set
from tests.workflow.test_tool_step_risk_gate import _MCP, _Store, _tool

pytestmark = pytest.mark.asyncio


def _build(
    definition: WorkflowDefinition, engine: PolicyEngine, **services: Any
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
        policy_engine=engine,
        **services,
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


def _definition(tool: str = "mongodb_find") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=f"wf-policy-{tool}",
        name="policy gate",
        steps=[_tool("call", tool), _set("after", ["call"])],
    )


async def test_deny_policy_blocks_the_tool_step() -> None:
    engine = PolicyEngine(
        [Policy(name="no-find", denied_tools=["mongodb_find"], tenant_id="t-qa7-1")]
    )
    definition = _definition()
    runner, compiler, gw, store, mcp = _build(definition, engine)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-qa7-1", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert mcp.calls == []
    assert outs["call"]["_denied"] is True
    assert "after" not in outs
    assert (await gw.list_pending(tenant_id="t-qa7-1"))[0] == []
    assert store.last_status() == WorkflowRunStatus.FAILED
    error = str(store.statuses[-1][2].get("error") or "")
    assert "policy" in error and "mongodb_find" in error


async def test_deny_policy_matches_the_connection_qualified_name() -> None:
    engine = PolicyEngine(
        [Policy(name="no-orders", denied_tools=["orders-db/*"], tenant_id="t-qa7-2")]
    )
    definition = WorkflowDefinition(
        id="wf-policy-server",
        name="policy gate",
        steps=[
            _tool("call", "mongodb_find").model_copy(update={"server_id": "orders-db"}),
            _set("after", ["call"]),
        ],
    )
    runner, _compiler, _gw, store, mcp = _build(definition, engine)

    await runner.run(workflow_id=definition.id, tenant_id="t-qa7-2", inputs={})

    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED


async def test_require_approval_policy_routes_to_hitl_then_runs_once_approved() -> None:
    engine = PolicyEngine(
        [Policy(name="ask-find", approval_tools=["mongodb_find"], tenant_id="t-qa7-3")]
    )
    definition = _definition()
    runner, compiler, gw, store, mcp = _build(definition, engine)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-qa7-3", inputs={})

    values = await _outputs(compiler, definition, run_id)
    assert values["status"] == WorkflowRunStatus.WAITING_HITL
    assert mcp.calls == []
    assert "after" not in values["step_outputs"]
    (req,) = (await gw.list_pending(tenant_id="t-qa7-3"))[0]
    assert req.step_id == "call"
    shown = {c["label"]: str(c["value"]) for c in req.context}
    assert "policy" in shown["Reason"]

    await gw.decide(req.request_id, action="approve", actor_id="reviewer-1")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert len(mcp.calls) == 1
    assert "after" in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


async def test_require_approval_policy_rejection_never_calls_the_tool() -> None:
    engine = PolicyEngine(
        [Policy(name="ask-find", approval_tools=["mongodb_find"], tenant_id="t-qa7-4")]
    )
    definition = _definition()
    runner, compiler, gw, store, mcp = _build(definition, engine)
    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-qa7-4", inputs={})
    (req,) = (await gw.list_pending(tenant_id="t-qa7-4"))[0]

    await gw.decide(req.request_id, action="reject", actor_id="reviewer-1")

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert mcp.calls == []
    assert "after" not in outs
    assert store.last_status() == WorkflowRunStatus.FAILED


async def test_no_matching_policy_leaves_the_step_unchanged() -> None:
    # Another tenant's deny and a policy for another tool do not apply.
    engine = PolicyEngine(
        [
            Policy(name="other-tenant", denied_tools=["*"], tenant_id="t-other"),
            Policy(name="other-tool", denied_tools=["slack_post"], tenant_id="t-qa7-5"),
        ]
    )
    definition = _definition()
    runner, compiler, gw, store, mcp = _build(definition, engine)

    run_id = await runner.run(workflow_id=definition.id, tenant_id="t-qa7-5", inputs={})

    outs = (await _outputs(compiler, definition, run_id))["step_outputs"]
    assert len(mcp.calls) == 1
    assert (await gw.list_pending(tenant_id="t-qa7-5"))[0] == []
    assert "after" in outs
    assert store.last_status() == WorkflowRunStatus.COMPLETE


async def test_tenant_policies_are_loaded_before_evaluation() -> None:
    """With a DB the tenant's slice is (re)loaded first, like a goal's run."""
    loaded: list[str] = []

    class _Engine(PolicyEngine):
        async def ensure_tenant_loaded(self, db: Any, tenant_id: str, **_kw: Any) -> None:
            loaded.append(tenant_id)
            self.add_policy(Policy(name="db", denied_tools=["*"], tenant_id=tenant_id))

    definition = _definition()
    runner, _compiler, _gw, store, mcp = _build(definition, _Engine(), db_session_factory=object())

    import app.governance.compliance_bundles as bundles
    import app.governance.policy_rules as rules

    async def _no_rule(*_a: Any, **_k: Any) -> None:
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "policy_rules_denial", _no_rule)
        mp.setattr(bundles, "bundle_hitl_requirement", _no_rule)
        await runner.run(workflow_id=definition.id, tenant_id="t-qa7-6", inputs={})

    assert loaded == ["t-qa7-6"]
    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED


async def test_policy_as_code_rule_denial_blocks_the_tool_step() -> None:
    definition = _definition()
    runner, _compiler, _gw, store, mcp = _build(
        definition, PolicyEngine(), db_session_factory=object()
    )
    import app.governance.compliance_bundles as bundles
    import app.governance.policy_rules as rules

    async def _deny(*_a: Any, **_k: Any) -> str:
        return "denied by policy rule 'no-find': nope"

    async def _none(*_a: Any, **_k: Any) -> None:
        return None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rules, "policy_rules_denial", _deny)
        mp.setattr(bundles, "bundle_hitl_requirement", _none)
        await runner.run(workflow_id=definition.id, tenant_id="t-qa7-7", inputs={})

    assert mcp.calls == []
    assert store.last_status() == WorkflowRunStatus.FAILED
    assert "no-find" in str(store.statuses[-1][2].get("error") or "")
