"""
PART 13 — Org-specific Workflow Step Types.

New step types that extend the existing workflow engine:
  DepartmentHandoffStep      — hand off artifact dept→dept with approval
  CrossTeamReviewStep        — send work to reviewer in different team
  OrgDecisionStep            — record decision with options/evidence
  ParallelDepartmentStep     — run multiple departments in parallel

These are additive to app/workflow/steps/ — zero breaking changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class WorkflowStepBase:
    """Base for all org workflow steps."""
    step_id: str
    name: str
    step_type: str
    description: str = ""
    timeout_minutes: float = 60.0
    on_failure: str = "fail"   # fail | skip | retry | escalate


@dataclass
class DepartmentHandoffStep(WorkflowStepBase):
    """
    PART 13: Hand off an artifact from one department to another.
    Used when work crosses a departmental boundary and may need approval.

    Example: Engineering hands off deployed artifact to QA for sign-off.
    """
    step_type: str = "department_handoff"
    source_department: str = ""
    target_department: str = ""
    artifact_id: str | None = None
    artifact_type: str = ""       # document | code | report | plan
    requires_approval: bool = False
    approval_roles: list[str] = field(default_factory=list)  # e.g. ["qa_lead"]
    handoff_message: str = ""
    sla_hours: float = 4.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":            self.step_id,
            "step_type":          self.step_type,
            "source_department":  self.source_department,
            "target_department":  self.target_department,
            "artifact_id":        self.artifact_id,
            "artifact_type":      self.artifact_type,
            "requires_approval":  self.requires_approval,
            "approval_roles":     self.approval_roles,
            "handoff_message":    self.handoff_message,
            "sla_hours":          self.sla_hours,
        }


@dataclass
class CrossTeamReviewStep(WorkflowStepBase):
    """
    PART 13: Send work to a reviewer agent in a different team.
    Used for cross-functional review before promotion to artifact.
    """
    step_type: str = "cross_team_review"
    reviewer_role: str = ""            # e.g. "legal_specialist"
    reviewer_department: str = ""      # e.g. "legal"
    work_artifact_id: str | None = None
    review_criteria: list[str] = field(default_factory=list)
    min_quality_score: float = 0.75
    review_timeout_hours: float = 8.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":              self.step_id,
            "step_type":            self.step_type,
            "reviewer_role":        self.reviewer_role,
            "reviewer_department":  self.reviewer_department,
            "work_artifact_id":     self.work_artifact_id,
            "review_criteria":      self.review_criteria,
            "min_quality_score":    self.min_quality_score,
            "review_timeout_hours": self.review_timeout_hours,
        }


@dataclass
class OrgDecisionStep(WorkflowStepBase):
    """
    PART 13: Record a structured decision with options, evidence, and escalation.
    Feeds into DecisionIntelligenceEngine for tracking actual vs predicted.
    """
    step_type: str = "org_decision"
    decision_owner: str = ""           # agent_id or role
    decision_question: str = ""        # "Which market to enter first?"
    options: list[dict[str, Any]] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    escalation_path: str = ""          # agent_id to escalate to
    decision_timeout_hours: float = 2.0
    requires_human_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":                  self.step_id,
            "step_type":                self.step_type,
            "decision_owner":           self.decision_owner,
            "decision_question":        self.decision_question,
            "options":                  self.options,
            "evidence_sources":         self.evidence_sources,
            "escalation_path":          self.escalation_path,
            "decision_timeout_hours":   self.decision_timeout_hours,
            "requires_human_approval":  self.requires_human_approval,
        }


@dataclass
class ParallelDepartmentStep(WorkflowStepBase):
    """
    PART 13: Run multiple departments in parallel, wait for all (or majority).
    Used for wide-scope missions that need simultaneous dept work.

    merge_strategy:
      "all_required" — wait for all to complete
      "majority"     — wait for >50% to complete
      "fastest"      — proceed when first completes
    """
    step_type: str = "parallel_department"
    departments: list[str] = field(default_factory=list)
    sub_goals: dict[str, str] = field(default_factory=dict)  # dept → goal
    merge_strategy: str = "all_required"
    timeout_hours: float = 24.0
    min_success_count: int | None = None  # for "majority" strategy

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":           self.step_id,
            "step_type":         self.step_type,
            "departments":       self.departments,
            "sub_goals":         self.sub_goals,
            "merge_strategy":    self.merge_strategy,
            "timeout_hours":     self.timeout_hours,
            "min_success_count": self.min_success_count,
        }


# ── Step factory ──────────────────────────────────────────────────────────────

ORG_STEP_REGISTRY: dict[str, type] = {
    "department_handoff":    DepartmentHandoffStep,
    "cross_team_review":     CrossTeamReviewStep,
    "org_decision":          OrgDecisionStep,
    "parallel_department":   ParallelDepartmentStep,
}


def create_org_step(step_type: str, **kwargs: Any) -> WorkflowStepBase:
    """Create an org workflow step from a type string."""
    cls = ORG_STEP_REGISTRY.get(step_type)
    if not cls:
        raise ValueError(f"Unknown org step type: {step_type!r}. "
                         f"Valid types: {list(ORG_STEP_REGISTRY)}")
    return cls(**kwargs)
