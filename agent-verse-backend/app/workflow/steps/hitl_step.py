"""HITLStepNode — human-in-the-loop gate with suspend/resume pattern."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowConfigurationError, WorkflowRunStatus, WorkflowState

_log = get_logger(__name__)

# Decision ids treated as a rejection / an approval when the step does not
# declare its own actions (or declares these ids). Compared case-insensitively.
REJECT_ACTIONS = frozenset({"reject", "rejected", "deny", "denied", "decline", "declined"})
APPROVE_ACTIONS = frozenset({"approve", "approved", "accept", "accepted"})


@dataclass(frozen=True)
class HITLDecision:
    """How a reviewer's decision on an approval step routes the run.

    ``kind``:
      * ``proceed`` — continue to ``next`` (when set) or the step's dependents;
      * ``reject_branch`` — a rejection with a declared branch: route to ``next``;
      * ``stop`` — halt the run (rejection with no branch, or an unrecognised
        decision — fail closed rather than treat it as an approval).
    """

    kind: Literal["proceed", "reject_branch", "stop"]
    next: str = ""
    reason: str = ""


def classify_hitl_decision(step: Any, action: Any) -> HITLDecision:
    """Classify ``action`` taken on approval ``step`` (single source of truth
    for the compiler router, the downstream barrier and the step node)."""
    act = str(action or "").strip()
    norm = act.lower()
    step_id = getattr(step, "id", "")
    declared = {a.id: a for a in (getattr(step, "actions", None) or [])}
    match = declared.get(act)
    if match is None:
        # Case-insensitive match against the declared ids.
        match = next((a for i, a in declared.items() if i.lower() == norm), None)
    if norm in REJECT_ACTIONS:
        if match is not None and match.next:
            return HITLDecision("reject_branch", match.next)
        return HITLDecision("stop", reason=f"Approval step {step_id!r} was rejected ({act!r})")
    if match is not None:
        return HITLDecision("proceed", match.next)
    if norm in APPROVE_ACTIONS:
        return HITLDecision("proceed")
    return HITLDecision(
        "stop",
        reason=f"Approval step {step_id!r} received an unrecognised decision {act!r}",
    )


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
            resolved_context.append(
                {
                    "label": ctx_item.label,
                    "value": self.ctx.resolve(ctx_item.value, state),
                    "display_type": ctx_item.display_type,
                    "threshold_red": ctx_item.threshold_red,
                    "threshold_yellow": ctx_item.threshold_yellow,
                }
            )

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
        elif state.get("is_test_run"):
            # Sandbox/test run — no approval record; use step_id as the request_id
            request_id = self.step.id
        else:
            # Old bug: a real run silently entered "test mode" here, created no
            # approval anyone could act on, and waited forever.
            _log.error("hitl_step_no_gateway", step_id=self.step.id, run_id=state.get("run_id"))
            raise WorkflowConfigurationError(
                f"Approval step {self.step.id!r} cannot run: no approval gateway is "
                "configured for the workflow engine, so nobody could approve it"
            )

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
            "action": action,
            "note": state.get("hitl_note"),
            "reviewer": state.get("hitl_reviewer"),
            "form_data": state.get("hitl_form_data"),
        }

        # Find which step to route to based on chosen action
        decision = classify_hitl_decision(self.step, action)
        next_step = decision.next or None
        stopped = decision.kind == "stop"
        if stopped:
            _log.info(
                "hitl_step_rejected_run_stopped",
                step_id=self.step.id,
                run_id=state.get("run_id"),
                action=action,
            )

        return {
            # A rejection with no declared reject branch (or an unrecognised
            # decision) ends the run: nothing downstream of the gate may run.
            "status": WorkflowRunStatus.FAILED if stopped else WorkflowRunStatus.RUNNING,
            **({"error": decision.reason, "error_step_id": self.step.id} if stopped else {}),
            "hitl_request_id": None,
            "hitl_action": None,
            "hitl_note": None,
            "hitl_reviewer": None,
            "hitl_form_data": None,
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
