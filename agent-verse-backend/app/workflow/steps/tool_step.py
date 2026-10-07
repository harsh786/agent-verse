"""ToolStepNode — executes any registered MCP tool.

OI-2: the call goes through the same tool risk gate as a goal's tool call
(``classify_tool_risk`` + ``resolve_effective_tool_risk``, incl. a connector's
explicit "allow autonomous execution" opt-in): ``destructive`` is denied and the
run fails; ``write_high`` suspends the run on the workflow's durable approval
barrier (a persisted workflow approval, ``waiting_hitl``, nothing downstream
runs) and only an explicit approval decision runs the call. A rejection, an
unrecognised decision or a missing approval gateway never runs it.

QA-7: before the risk gate the call is checked against the tenant's governance,
as a goal's ``GovernedToolGate`` does — the PolicyEngine (its tenant slice
reloaded first when a DB is wired), the tenant's policy-as-code rules and its
compliance bundles' approval requirements. A deny fails the step without calling
the connector; a require_approval takes the same durable approval path as
``write_high``. Agent-scoped checks (grants, agent permissions) have no agent in
a workflow and are not applied here.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.retry_policy import StepAttemptError, classify_error_text
from app.workflow.state import WorkflowConfigurationError, WorkflowRunStatus, WorkflowState
from app.workflow.steps import StepServiceUnavailableError

_log = get_logger(__name__)

_RISK_ORDER = ("read", "write_low", "write_high", "destructive")
# Reviewer-facing approval actions of a gated tool step.
_APPROVAL_ACTIONS = [
    {"id": "approve", "label": "Approve", "style": "success"},
    {"id": "reject", "label": "Reject", "style": "danger"},
]
_APPROVAL_DEADLINE_HOURS = 48.0


def _bare_tool_name(tool: str) -> str:
    """``"<connection>.<tool>"`` / ``"<connection_slug>__<tool>"`` -> ``<tool>``."""
    bare = tool.rpartition(".")[2]
    if "__" in bare:
        bare = bare.split("__", 1)[1]
    return bare


def static_tool_risk(tool: str, arguments: dict[str, Any] | None = None) -> str:
    """Risk of a workflow tool step's call: the higher of the full and bare names.

    Without arguments (compile time) argument-dependent tools fail closed.
    """
    from app.agent.tool_risk import classify_tool_risk

    name = str(tool or "")
    risks = {classify_tool_risk(name, "", arguments)}
    bare = _bare_tool_name(name)
    if bare and bare != name:
        risks.add(classify_tool_risk(bare, "", arguments))
    known = [r for r in risks if r in _RISK_ORDER]
    if not known:
        return "write_high"
    return max(known, key=_RISK_ORDER.index)


def is_gated_tool_step(step: Any, *, governed: bool = False) -> bool:
    """A tool step that may suspend for approval or be denied (compile time).

    *governed*: tenant governance (policies / policy rules) is wired, so ANY tool
    step may be denied or need an approval at run time (QA-7).
    """
    if getattr(step, "type", "") != "tool":
        return False
    if governed:
        return True
    return static_tool_risk(str(getattr(step, "tool", "") or "")) in ("write_high", "destructive")


def has_tool_governance(services: dict[str, Any]) -> bool:
    """Whether the step services carry tenant governance for tool steps (QA-7)."""
    return (
        services.get("policy_engine") is not None or services.get("db_session_factory") is not None
    )


# A built-in connector's result ``status`` for a call it refused before (or
# instead of) running it: trying again cannot change the answer.
_NON_RETRYABLE_TOOL_STATUS = {
    "operator_refused": "refused",
    "egress_refused": "refused",
    "tls_refused": "refused",
    "credentials_required": "unauthorized",
    "invalid_arguments": "validation",
    "dependency_missing": "configuration",
    "connector_disabled": "configuration",
    # A stored credential this process cannot decrypt (vault key mismatch):
    # retrying on the same process cannot open it either.
    "credentials_undecryptable": "configuration",
}


def tool_failure(tool: str | None, result: Any) -> StepAttemptError:
    """The classified error of a failed ``ToolCallResult``."""
    message = str(getattr(result, "error", "") or f"tool '{tool}' failed")
    output = getattr(result, "output", None)
    status = str(output.get("status") or "") if isinstance(output, dict) else ""
    kind = _NON_RETRYABLE_TOOL_STATUS.get(status)
    if kind is not None:
        return StepAttemptError(message, kind=kind, retryable=False)
    failure = classify_error_text(message)
    return StepAttemptError(
        message,
        kind=failure.kind,
        retryable=failure.retryable,
        error_id=failure.error_id,
        status_code=failure.status_code,
    )


def _redacted_args(arguments: Any) -> str:
    from app.agent.sanitization import redact_sensitive_text

    try:
        text = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    return redact_sensitive_text(text)[:2000]


class ToolStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.mcp_client = services.get("mcp_client")
        self.hitl_gateway = services.get("hitl_workflow_gateway")
        # QA-7: the tenant's governance (policy engine + policy rules DB).
        self.policy_engine = services.get("policy_engine")
        self.governance_db = services.get("db_session_factory")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resolved_input = self.ctx.resolve_dict(self.step.input, state)

        _log.info("tool_step_executing", step_id=self.step.id, tool=self.step.tool)
        start = time.monotonic()

        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
            _log.debug("tool_step_mock", step_id=self.step.id)
        elif self.mcp_client is None:
            # Old bug: this returned {"_mock": True, ...} for REAL runs too, so a
            # run whose MCP client failed to wire "succeeded" without calling
            # anything. Only an explicit test/simulation run may simulate.
            if not state.get("is_test_run"):
                raise StepServiceUnavailableError(
                    f"tool step {self.step.id!r}: no MCP client is configured, "
                    f"cannot call tool {self.step.tool!r}"
                )
            output = {
                "_mock": True,
                "_simulated": True,
                "tool": self.step.tool,
                "input": resolved_input,
            }
        else:
            # Real MCP dispatch: resolve the connector that exposes this tool and
            # call it. Use the initiator's real TenantContext when present; else
            # reconstruct a LEAST-PRIVILEGE context from the run's tenant_id (no
            # admin, minimal plan) — MCP dispatch only needs the tenant_id for
            # registry scoping + secret resolution, never elevated roles. Fail
            # closed if there is no tenant to scope to.
            from app.tenancy.context import PlanTier, TenantContext

            _tctx = state.get("tenant_ctx")
            if _tctx is None:
                _tid = state.get("tenant_id", "")
                if not _tid:
                    raise PermissionError("workflow tool step missing tenant context")
                _tctx = TenantContext(
                    tenant_id=_tid, plan=PlanTier.FREE, api_key_id="workflow", roles=()
                )
            # OI-2: the tool risk gate, before anything is dispatched.
            risk = await self._effective_risk(resolved_input, _tctx)
            # QA-7: the tenant's governance policies come first.
            verdict, reason = await self._governance_verdict(resolved_input, _tctx)
            if verdict == "deny":
                return self._denied(
                    state,
                    f"tool step {self.step.id!r}: the call to '{self.step.tool}' was "
                    f"{reason}; it was not run",
                    risk,
                )
            if risk == "destructive":
                return self._denied(
                    state,
                    f"tool step {self.step.id!r}: '{self.step.tool}' is classified "
                    "destructive and is denied in workflows",
                    risk,
                )
            approval: dict[str, Any] | None = None
            if risk == "write_high" or verdict == "approval":
                if state.get("hitl_request_id") != self.step.id:
                    return await self._suspend_for_approval(
                        state, resolved_input, risk, reason=reason
                    )
                from app.workflow.steps.hitl_step import classify_hitl_decision

                action = state.get("hitl_action", "")
                if classify_hitl_decision(self.step, action).kind != "proceed":
                    return self._denied(
                        state,
                        f"tool step {self.step.id!r}: the call to '{self.step.tool}' was "
                        f"rejected by the reviewer ({action!r}); it was not run",
                        risk,
                    )
                approval = {
                    "action": action,
                    "reviewer": state.get("hitl_reviewer"),
                    "note": state.get("hitl_note"),
                }
                _log.info(
                    "tool_step_approved",
                    step_id=self.step.id,
                    run_id=state.get("run_id"),
                    reviewer=state.get("hitl_reviewer"),
                )
            # The step's saved connector instance when set; a bare tool name
            # exposed by several connectors is refused as ambiguous.
            from app.mcp.client import idempotency_scope
            from app.workflow.idempotency import step_idempotency_key

            # WF-14: the MCP call carries this step's deterministic key
            # (Idempotency-Key header + _meta.idempotencyKey). Every retry of the
            # step repeats the SAME key: an attempt that timed out may still
            # have been applied, and the key lets the receiver recognise it.
            with idempotency_scope(step_idempotency_key(state, self.step.id)):
                result = await self.mcp_client.call_tool_by_name(
                    tool_name=self.step.tool or "",
                    arguments=resolved_input,
                    tenant_ctx=_tctx,
                    server_id=self.step.server_id or None,
                )
            if hasattr(result, "success"):  # ToolCallResult
                # Raise on failure so the runner's retry and on_failure handling
                # (pause / skip / abort) apply, classified: a refused operator or
                # an authorization denial is never retried, a timeout is.
                if not result.success:
                    raise tool_failure(self.step.tool, result)
                output = {"success": True, "output": result.output, "error": ""}
                if getattr(result, "stale", False) is True:
                    # a02-F030-04: served from the read cache while the
                    # connector's circuit is open — earlier data, not live.
                    from app.mcp.client import stale_result_notice

                    output = {**output, "stale": True, "notice": stale_result_notice(result)}
            else:
                output = result if isinstance(result, dict) else {"result": result}
            if approval is not None:
                output = {**output, "approval": approval}
                duration_ms = int((time.monotonic() - start) * 1000)
                return {
                    # The decision is consumed: clear the resume markers.
                    "status": WorkflowRunStatus.RUNNING,
                    "hitl_request_id": None,
                    "hitl_action": None,
                    "hitl_note": None,
                    "hitl_reviewer": None,
                    "hitl_form_data": None,
                    "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
                    "step_timings": {
                        **(state.get("step_timings") or {}),
                        self.step.id: duration_ms,
                    },
                }

        duration_ms = int((time.monotonic() - start) * 1000)

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }

    def _governed_name(self) -> str:
        """The step's tool name carrying every form a policy may name it by."""
        from app.mcp.tool_naming import GovernedToolName

        tool = str(self.step.tool or "")
        bare = _bare_tool_name(tool)
        forms = [f"{self.step.server_id}/{bare}" if self.step.server_id else "", tool, bare]
        return GovernedToolName(tool, forms)

    async def _governance_verdict(
        self, arguments: dict[str, Any], tenant_ctx: Any
    ) -> tuple[str, str]:
        """``("deny" | "approval" | "allow", reason)`` from the tenant's governance.

        Same checks, order and fail-closed semantics as ``GovernedToolGate``
        steps 2c and 3: policy-as-code rules, compliance bundles, policy engine.
        """
        tool = self._governed_name()
        tenant_id = str(getattr(tenant_ctx, "tenant_id", "") or "")
        needs_approval = ""
        if self.governance_db is not None and tenant_id:
            from app.governance import compliance_bundles, policy_rules

            denial = await policy_rules.policy_rules_denial(
                self.governance_db,
                tenant_id,
                {
                    "tool_name": tool,
                    "arguments": arguments or {},
                    "agent_id": "",
                    "goal_id": "",
                    "step": self.step.name or self.step.id,
                },
            )
            if denial is not None:
                return "deny", denial
            try:
                bundle = await compliance_bundles.bundle_hitl_requirement(
                    self.governance_db, tenant_id, tool
                )
            except Exception as exc:
                return "deny", (
                    f"blocked: compliance bundles could not be read ({type(exc).__name__}), "
                    "failing closed"
                )
            if bundle:
                needs_approval = f"compliance bundle {bundle!r} requires an approval"
        if self.policy_engine is not None:
            from app.governance.policies import PolicyResult

            ensure = getattr(self.policy_engine, "ensure_tenant_loaded", None)
            if ensure is not None and self.governance_db is not None and tenant_id:
                # A first load that fails leaves a deny-all policy (fail closed).
                await ensure(self.governance_db, tenant_id)
            result = self.policy_engine.evaluate(tool, tenant_ctx=tenant_ctx)
            if result == PolicyResult.DENY:
                return "deny", "denied by governance policy"
            if result == PolicyResult.REQUIRE_APPROVAL:
                needs_approval = "governance policy requires an approval"
        if needs_approval:
            return "approval", needs_approval
        return "allow", ""

    async def _effective_risk(self, arguments: dict[str, Any], tenant_ctx: Any) -> str:
        """The call's risk tier, as a goal's tool gate would resolve it."""
        from app.agent.nodes._helpers import resolve_effective_tool_risk

        risk = static_tool_risk(str(self.step.tool or ""), arguments)
        auto_approve = False
        registry = getattr(self.mcp_client, "_registry", None)
        if self.step.server_id and registry is not None:
            try:
                cfg = await registry.get(self.step.server_id, tenant_ctx=tenant_ctx)
            except Exception as exc:  # unknown opt-in: stay gated (fail closed)
                _log.warning(
                    "tool_step_connector_lookup_failed",
                    step_id=self.step.id,
                    error=f"{type(exc).__name__}: {str(exc)[:120]}",
                )
                cfg = None
            if cfg is not None:
                from app.agent.tool_risk import classify_tool_risk

                # The connector's name may only raise the tier (never lower it).
                named = classify_tool_risk(
                    _bare_tool_name(str(self.step.tool or "")), str(cfg.name or ""), arguments
                )
                if named in _RISK_ORDER and _RISK_ORDER.index(named) > _RISK_ORDER.index(risk):
                    risk = named
                auto_approve = bool(getattr(cfg, "auto_approve", False))
        return resolve_effective_tool_risk(
            risk,
            autonomy_mode="supervised",
            connector_auto_approve=auto_approve,
            allow_fa_write_high=False,
        )

    def _denied(self, state: WorkflowState, reason: str, risk: str) -> dict[str, Any]:
        """Stop the run: the call was not (and will not be) made."""
        _log.warning(
            "tool_step_denied",
            step_id=self.step.id,
            run_id=state.get("run_id"),
            tool=self.step.tool,
            risk=risk,
        )
        return {
            "status": WorkflowRunStatus.FAILED,
            "error": reason,
            "error_step_id": self.step.id,
            "hitl_request_id": None,
            "hitl_action": None,
            "hitl_note": None,
            "hitl_reviewer": None,
            "hitl_form_data": None,
            "step_outputs": {
                **(state.get("step_outputs") or {}),
                self.step.id: {"_denied": True, "risk": risk, "error": reason},
            },
        }

    async def _suspend_for_approval(
        self, state: WorkflowState, arguments: dict[str, Any], risk: str, *, reason: str = ""
    ) -> dict[str, Any]:
        """File a durable workflow approval for this call and suspend the run."""
        assignee = self.step.assignee
        context_payload = [
            {"label": "Tool", "value": str(self.step.tool or ""), "display_type": "text"},
            {
                "label": "Connector",
                "value": str(self.step.server_id or "(resolved by tool name)"),
                "display_type": "text",
            },
            {"label": "Risk", "value": risk, "display_type": "text"},
            {"label": "Arguments", "value": _redacted_args(arguments), "display_type": "json"},
        ]
        if reason:
            context_payload.append({"label": "Reason", "value": reason, "display_type": "text"})
        if self.hitl_gateway is not None:
            escalation_hours = None
            escalation_role = None
            if self.step.escalation:
                from app.workflow.steps.hitl_step import HITLStepNode

                escalation_hours = HITLStepNode._parse_timeout_hours(self.step.escalation.after)
                escalation_role = self.step.escalation.to_role
            request_id = await self.hitl_gateway.create_workflow_approval(
                run_id=state.get("run_id", ""),
                step_id=self.step.id,
                step_name=self.step.name or f"Approve tool call: {self.step.tool}",
                workflow_name=state.get("workflow_name", ""),
                tenant_id=state.get("tenant_id", ""),
                workflow_id=str(state.get("workflow_id") or ""),
                assignee_role=(assignee.role if assignee else ""),
                strategy=(assignee.strategy if assignee else "round_robin"),
                specific_user=(assignee.specific_user if assignee else None),
                context_payload=context_payload,
                actions_config=list(_APPROVAL_ACTIONS),
                deadline_hours=_APPROVAL_DEADLINE_HOURS,
                escalation_hours=escalation_hours,
                escalation_to_role=escalation_role,
                priority="high",
                # An unanswered approval never runs the call by itself.
                timeout_action=(
                    "auto_reject"
                    if self.step.timeout_action == "auto_approve"
                    else self.step.timeout_action
                ),
            )
        elif state.get("is_test_run"):
            request_id = self.step.id
        else:
            raise WorkflowConfigurationError(
                f"Tool step {self.step.id!r} calls '{self.step.tool}' ({risk}), which needs a "
                "human approval, but no approval gateway is configured for the workflow "
                "engine; the call was not made"
            )
        _log.info(
            "tool_step_awaiting_approval",
            step_id=self.step.id,
            run_id=state.get("run_id"),
            request_id=request_id,
            tool=self.step.tool,
        )
        return {"status": WorkflowRunStatus.WAITING_HITL, "hitl_request_id": self.step.id}
