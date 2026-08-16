"""HITLStepNode — human-in-the-loop gate with suspend/resume pattern."""
from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus, WorkflowState

_log = get_logger(__name__)


class HITLStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.hitl_gateway = services.get("hitl_workflow_gateway")
        self.sse_broadcaster = services.get("sse_broadcaster")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        # ── Resuming after human decision ────────────────────────────────
        if state.get("hitl_request_id") == self.step.id:
            return self._process_decision(state)

        # ── First pass — create approval request and suspend ─────────────
        resolved_context = []
        for ctx_item in self.step.context:
            resolved_context.append({
                "label":            ctx_item.label,
                "value":            self.ctx.resolve(ctx_item.value, state),
                "display_type":     ctx_item.display_type,
                "threshold_red":    ctx_item.threshold_red,
                "threshold_yellow": ctx_item.threshold_yellow,
            })

        actions_config = [a.model_dump() for a in self.step.actions]

        timeout_hours = self._parse_timeout_hours(self.step.timeout)
        escalation_hours = None
        escalation_role = None
        if self.step.escalation:
            escalation_hours = self._parse_timeout_hours(self.step.escalation.after)
            escalation_role = self.step.escalation.to_role

        if self.hitl_gateway is not None:
            request_id = await self.hitl_gateway.create_workflow_approval(
                run_id=state.get("run_id", ""),
                step_id=self.step.id,
                step_name=self.step.name or self.step.id,
                workflow_name=state.get("workflow_name", ""),
                tenant_id=state.get("tenant_id", ""),
                assignee_role=(self.step.assignee.role if self.step.assignee else ""),
                strategy=(self.step.assignee.strategy if self.step.assignee else "round_robin"),
                specific_user=(self.step.assignee.specific_user if self.step.assignee else None),
                context_payload=resolved_context,
                actions_config=actions_config,
                deadline_hours=timeout_hours,
                escalation_hours=escalation_hours,
                escalation_to_role=escalation_role,
                custom_form_schema=self.step.custom_form_schema,
                priority=self._compute_priority(state),
                timeout_action=self.step.timeout_action,
            )
        else:
            # Test mode — use step_id as the request_id
            request_id = self.step.id

        _log.info(
            "hitl_step_suspended",
            step_id=self.step.id,
            run_id=state.get("run_id"),
            request_id=request_id,
        )

        # Suspend the graph — LangGraph checkpoints here
        return {
            "status": WorkflowRunStatus.WAITING_HITL,
            "hitl_request_id": self.step.id,
        }

    def _process_decision(self, state: WorkflowState) -> dict[str, Any]:
        """Called when graph resumes after reviewer decision."""
        action = state.get("hitl_action", "")
        _log.info(
            "hitl_step_decided",
            step_id=self.step.id,
            action=action,
            reviewer=state.get("hitl_reviewer"),
        )

        output = {
            "action":      action,
            "note":        state.get("hitl_note"),
            "reviewer":    state.get("hitl_reviewer"),
            "form_data":   state.get("hitl_form_data"),
        }

        # Find which step to route to based on chosen action
        next_step = None
        for act in self.step.actions:
            if act.id == action:
                next_step = act.next
                break

        return {
            "status":            WorkflowRunStatus.RUNNING,
            "hitl_request_id":   None,
            "hitl_action":       None,
            "hitl_note":         None,
            "hitl_reviewer":     None,
            "hitl_form_data":    None,
            "step_outputs": {
                **(state.get("step_outputs") or {}),
                self.step.id: output,
            },
            # next_step is used by the compiler's conditional router
            "_hitl_next_step": next_step,
        }

    @staticmethod
    def _parse_timeout_hours(timeout_str: str) -> float:
        if not timeout_str:
            return 48.0
        s = timeout_str.strip()
        if s.endswith("h"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) / 60
        if s.endswith("d"):
            return float(s[:-1]) * 24
        return 48.0

    @staticmethod
    def _compute_priority(state: WorkflowState) -> str:
        labels = state.get("labels") or {}
        return labels.get("priority", "medium")
