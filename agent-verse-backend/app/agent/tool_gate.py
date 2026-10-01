"""Governed tool-call gate for execution paths outside ``AgentGraph``'s executor.

``WorkflowExecutor`` used to call MCP tools with no permission / policy / approval /
grant / cost / guardrail check at all. :class:`GovernedToolGate` applies the same
checks, in the same order and with the same fail-closed semantics, that the
AgentGraph executor applies to a tool call:

1. Guardrails 2.0 ``TOOL_ARGS`` evaluation (errors fail closed on high-risk tools)
2. Permission matrix (``DENY`` blocks)
3. Policy engine (``DENY`` blocks, ``REQUIRE_APPROVAL`` forces HITL)
4. Grantex grants (``enforce_tool_call``)
5. Tool risk → destructive denied, ``write_high`` (and anything needing approval)
   routed through the HITL gateway; only an explicit ``APPROVED`` lets it run
6. Cost: the goal/tenant budget must not already be exhausted
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agent.nodes._helpers import resolve_effective_tool_risk
from app.agent.tool_risk import classify_tool_risk
from app.governance.grants import enforce_tool_call
from app.governance.hitl import ApprovalStatus
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyResult


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""


class GovernedToolGate:
    """Authorise one tool call. Construct once per run with the tenant's services."""

    def __init__(
        self,
        *,
        policy_engine: Any = None,
        permission_matrix: Any = None,
        hitl_gateway: Any = None,
        grant_store: Any = None,
        enforce_grants: bool = False,
        cost_controller: Any = None,
        autonomy_mode: str = "bounded-autonomous",
        hitl_timeout: float | None = None,
        agent_id: str | None = None,
        guardrails: Any = "default",
    ) -> None:
        self._policy_engine = policy_engine
        self._permission_matrix = permission_matrix
        self._hitl = hitl_gateway
        self._grant_store = grant_store
        self._enforce_grants = enforce_grants
        self._cost_controller = cost_controller
        self._autonomy_mode = autonomy_mode
        self._hitl_timeout = hitl_timeout
        self._agent_id = agent_id or ""
        if guardrails == "default":
            try:
                from app.guardrails_v2.engine import guardrails_engine

                guardrails = guardrails_engine
            except ImportError:  # pragma: no cover - optional module
                guardrails = None
        self._guardrails = guardrails

    @property
    def cost_controller(self) -> Any:
        return self._cost_controller

    async def authorize(
        self,
        *,
        tool_name: str,
        server_name: str = "",
        arguments: dict[str, Any] | None,
        tenant_ctx: Any,
        goal_id: str,
        step_description: str = "",
        requires_approval: bool = False,
        auto_approve: bool = False,
    ) -> GateDecision:
        risk = classify_tool_risk(tool_name, server_name)
        high_risk = risk in ("write_high", "destructive")

        # 1. Guardrails on the tool arguments.
        if self._guardrails is not None and tenant_ctx is not None:
            try:
                self._guardrails.ensure_default_rules(tenant_ctx.tenant_id)
                from app.guardrails_v2.models import GuardrailLayer

                verdict = await self._guardrails.evaluate(
                    content=json.dumps(arguments or {}, default=str)[:4000],
                    layer=GuardrailLayer.TOOL_ARGS,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=goal_id,
                    step_description=step_description,
                )
                if verdict.get("blocked"):
                    rule = (verdict.get("violations") or [{}])[0].get("rule_name", "policy")
                    return GateDecision(False, f"blocked by guardrail: {rule}")
            except Exception as exc:
                if high_risk:
                    return GateDecision(False, f"guardrail check errored ({exc}); failing closed")

        # 2. Permission matrix.
        if self._permission_matrix is not None:
            level = self._permission_matrix.check(tool_name=tool_name, tenant_ctx=tenant_ctx)
            if level == ActionLevel.DENY:
                return GateDecision(False, f"'{tool_name}' denied by permission matrix")
            if level == ActionLevel.APPROVAL:
                requires_approval = True

        # 3. Policy engine.
        if self._policy_engine is not None:
            policy = self._policy_engine.evaluate(tool_name=tool_name, tenant_ctx=tenant_ctx)
            if policy == PolicyResult.DENY:
                return GateDecision(False, f"'{tool_name}' denied by governance policy")
            if policy == PolicyResult.REQUIRE_APPROVAL:
                requires_approval = True

        # 4. Grants.
        grant = await enforce_tool_call(
            self._grant_store,
            tenant_id=tenant_ctx.tenant_id,
            agent_id=self._agent_id,
            tool_name=tool_name,
            enabled=self._enforce_grants,
        )
        if not grant.allowed:
            return GateDecision(False, f"'{tool_name}' not granted ({grant.reason})")

        # 5. Risk → deny destructive, HITL for high-risk / approval-required.
        import os

        effective = resolve_effective_tool_risk(
            risk,
            autonomy_mode=self._autonomy_mode,
            connector_auto_approve=auto_approve,
            allow_fa_write_high=os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower()
            == "true",
        )
        if effective == "destructive":
            return GateDecision(False, f"'{tool_name}' denied as destructive")
        if effective == "write_high" or requires_approval:
            approval = await self._approve(tool_name, step_description, tenant_ctx, goal_id)
            if not approval.allowed:
                return approval

        # 6. Budget pre-flight: a zero-cost check fails once the budget is exhausted.
        if self._cost_controller is not None:
            try:
                ok = await self._cost_controller.check_and_record(
                    goal_id=goal_id, cost_usd=0.0, tenant_ctx=tenant_ctx
                )
            except Exception as exc:
                # Fail closed: an unverifiable budget must not admit the tool call.
                return GateDecision(
                    False,
                    f"budget_check_failed: cost budget could not be verified ({exc})"[:300],
                )
            if not ok:
                return GateDecision(False, "budget_exceeded: goal/tenant cost budget exhausted")
        return GateDecision(True)

    async def _approve(
        self, tool_name: str, step: str, tenant_ctx: Any, goal_id: str
    ) -> GateDecision:
        if self._hitl is None:
            return GateDecision(False, f"'{tool_name}' requires approval; no approval gateway")
        if self._autonomy_mode != "supervised":
            # Same as the AgentGraph executor: outside supervised mode nobody waits,
            # so the tool is NOT run and no request is filed (CORE-01) — one used
            # to be filed and left pending, and deciding it changed nothing.
            return GateDecision(
                False,
                f"'{tool_name}' requires approval ({self._autonomy_mode} mode awaits none); "
                "run the goal in supervised mode to approve it",
            )
        req_id = str(
            self._hitl.request_approval(
                goal_id=goal_id,
                action=f"{tool_name}: {step}"[:500],
                risk_level="high",
                tenant_ctx=tenant_ctx,
            )
        )
        status = await self._hitl.wait_for_approval(
            req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
        )
        if status != ApprovalStatus.APPROVED:
            return GateDecision(False, f"'{tool_name}' approval {status}")
        return GateDecision(True)

    async def charge_llm(self, *, goal_id: str, tenant_ctx: Any, resp: Any) -> bool:
        """Charge a workflow LLM call to the budget. False when the spend is denied."""
        if self._cost_controller is None:
            return True
        from app.agent.nodes.llm_cost import llm_call_tokens
        from app.intelligence.cost_tracker import calculate_cost

        prompt, completion = llm_call_tokens(resp)
        cost = calculate_cost(str(getattr(resp, "model", "") or ""), prompt, completion)
        try:
            from app.governance.cost import llm_spend

            return bool(
                await llm_spend(
                    self._cost_controller.check_and_record(
                        goal_id=goal_id, cost_usd=cost, tenant_ctx=tenant_ctx
                    )
                )
            )
        except Exception:
            # Fail closed: unmetered spend is denied, never waved through.
            return False


def gate_from_app_state(app_state: Any, *, agent_id: str | None = None) -> GovernedToolGate:
    """Build the gate from the services wired on ``app.state`` (mirrors GoalService)."""
    from app.core.config import get_settings

    def _get(name: str) -> Any:
        return getattr(app_state, name, None) if app_state is not None else None

    try:
        enforce = bool(getattr(get_settings(), "enforce_agent_grants", False))
    except Exception:
        enforce = False
    return GovernedToolGate(
        policy_engine=_get("policy_engine"),
        permission_matrix=_get("permission_matrix"),
        hitl_gateway=_get("hitl_gateway"),
        grant_store=_get("grant_store"),
        enforce_grants=enforce,
        cost_controller=_get("redis_cost_controller") or _get("cost_controller"),
        agent_id=agent_id,
    )
