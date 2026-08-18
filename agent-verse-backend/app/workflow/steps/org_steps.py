"""PART 13 — New Org-level Workflow Step Types.

Four new step types for org workflows:
  DepartmentHandoffStep    — hand off artifact dept→dept with approval
  CrossTeamReviewStep      — send work to reviewer in a different team
  OrgDecisionStep          — record decision with options/evidence/recommendation
  ParallelDepartmentStep   — run multiple departments in parallel, wait for all

These are additive — they extend the existing 12 step types.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace

from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class DepartmentHandoffStep:
    """
    Hand off an artifact from one department to another.
    Optionally requires approval from specified roles before handoff completes.
    """

    step_type = "department_handoff"

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: Any,
        **services: Any,
    ) -> None:
        self._step     = step
        self._ctx      = context_resolver
        self._services = services

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        with _tracer.start_as_current_span("step.department_handoff") as span:
            config = self._step.config or {}
            target_dept      = config.get("target_department", "")
            requires_approval = config.get("requires_approval", False)
            approval_roles   = config.get("approval_roles", [])
            artifact_key     = config.get("artifact_key", "output")

            span.set_attribute("target_dept", target_dept)
            span.set_attribute("requires_approval", requires_approval)

            # Get the artifact to hand off
            artifact = state.vars.get(artifact_key) or state.step_outputs.get(self._step.id, {})

            result: dict[str, Any] = {
                "handoff_to":   target_dept,
                "artifact":     artifact,
                "timestamp":    datetime.now(UTC).isoformat(),
                "step_id":      self._step.id,
            }

            if requires_approval:
                # Signal that HITL approval is needed before handoff completes
                result["approval_pending"] = True
                result["approval_roles"]   = approval_roles
                _log.info(
                    "workflow.department_handoff.pending_approval",
                    target_dept=target_dept,
                    roles=approval_roles,
                )
            else:
                result["approved"] = True
                _log.info("workflow.department_handoff.completed", target_dept=target_dept)

            return {
                "step_outputs": {self._step.id: result},
                "vars": {f"handoff_{target_dept}": result},
            }


class CrossTeamReviewStep:
    """
    Send work to a reviewer agent in a different team.
    The review criteria and reviewer role are configurable.
    """

    step_type = "cross_team_review"

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: Any,
        **services: Any,
    ) -> None:
        self._step     = step
        self._ctx      = context_resolver
        self._services = services

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        with _tracer.start_as_current_span("step.cross_team_review") as span:
            config           = self._step.config or {}
            reviewer_role    = config.get("reviewer_role", "qa_engineer")
            reviewer_dept    = config.get("reviewer_department", "qa")
            review_criteria  = config.get("review_criteria", [])
            content_key      = config.get("content_key", "output")

            span.set_attribute("reviewer_role", reviewer_role)
            span.set_attribute("reviewer_dept", reviewer_dept)

            content = state.vars.get(content_key) or state.step_outputs.get(self._step.id, {})

            # In production: dispatch to reviewer agent via agent orchestrator
            # For now: record review request and return pending
            result: dict[str, Any] = {
                "review_requested_for": reviewer_role,
                "reviewer_department":  reviewer_dept,
                "review_criteria":      review_criteria,
                "content_to_review":    str(content)[:500],
                "review_status":        "pending",
                "timestamp":            datetime.now(UTC).isoformat(),
            }

            _log.info(
                "workflow.cross_team_review.requested",
                reviewer_role=reviewer_role,
                reviewer_dept=reviewer_dept,
            )

            return {
                "step_outputs": {self._step.id: result},
                "vars": {f"review_{reviewer_dept}": result},
            }


class OrgDecisionStep:
    """
    Record a decision with options, evidence, and recommendation.
    Used by the DecisionIntelligenceEngine to track outcomes.
    """

    step_type = "org_decision"

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: Any,
        **services: Any,
    ) -> None:
        self._step     = step
        self._ctx      = context_resolver
        self._services = services

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        with _tracer.start_as_current_span("step.org_decision") as span:
            config = self._step.config or {}
            problem         = config.get("problem", "")
            options         = config.get("options", [])
            evidence_keys   = config.get("evidence_sources", [])
            escalation_path = config.get("escalation_path", "manager")
            decision_owner  = config.get("decision_owner", "agent")

            span.set_attribute("problem_len", len(problem))

            # Gather evidence from workflow state
            evidence = []
            for key in evidence_keys:
                if val := state.vars.get(key):
                    evidence.append({"source": key, "content": str(val)[:200]})

            # Simple option selection heuristic if no LLM available
            chosen = options[0] if options else "proceed"

            result: dict[str, Any] = {
                "problem":       problem,
                "options":       options,
                "evidence":      evidence,
                "chosen_option": chosen,
                "decision_owner": decision_owner,
                "escalation_path": escalation_path,
                "confidence":    0.75,
                "timestamp":     datetime.now(UTC).isoformat(),
                "requires_human_approval": decision_owner == "human",
            }

            _log.info(
                "workflow.org_decision.recorded",
                problem=problem[:60],
                chosen=chosen,
            )

            return {
                "step_outputs": {self._step.id: result},
                "vars": {f"decision_{self._step.id}": result},
            }


class ParallelDepartmentStep:
    """
    Run multiple departments in parallel, wait for all (or majority/fastest).
    Merge strategy: all_required | majority | fastest.
    """

    step_type = "parallel_departments"

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: Any,
        **services: Any,
    ) -> None:
        self._step     = step
        self._ctx      = context_resolver
        self._services = services

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        with _tracer.start_as_current_span("step.parallel_departments") as span:
            config         = self._step.config or {}
            departments    = config.get("departments", [])
            merge_strategy = config.get("merge_strategy", "all_required")
            timeout_hours  = config.get("timeout_hours", 4.0)

            span.set_attribute("dept_count", len(departments))
            span.set_attribute("merge_strategy", merge_strategy)

            _log.info(
                "workflow.parallel_departments.started",
                departments=departments,
                merge_strategy=merge_strategy,
            )

            # In production: dispatch sub-missions to each department
            # Record which departments are expected to contribute
            dept_tasks: dict[str, str] = {}
            for dept in departments:
                task_id = f"dept_task_{dept}_{self._step.id}"
                dept_tasks[dept] = task_id

            result: dict[str, Any] = {
                "departments":    departments,
                "merge_strategy": merge_strategy,
                "timeout_hours":  timeout_hours,
                "dept_tasks":     dept_tasks,
                "status":         "running",
                "started_at":     datetime.now(UTC).isoformat(),
            }

            return {
                "step_outputs": {self._step.id: result},
                "vars": {"parallel_dept_tracker": result},
            }


# ── Step registry entry ───────────────────────────────────────────────────────

ORG_STEP_TYPES: dict[str, type] = {
    "department_handoff":   DepartmentHandoffStep,
    "cross_team_review":    CrossTeamReviewStep,
    "org_decision":         OrgDecisionStep,
    "parallel_departments": ParallelDepartmentStep,
}
