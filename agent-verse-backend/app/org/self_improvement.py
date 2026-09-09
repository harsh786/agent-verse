"""
PART 24 — Self-Improvement Cycle.
PART 25 — Self-Healing Recovery Hierarchy.
PART 26 — Organizational Learning.

Self-Improvement (PART 24):
  OBSERVE → ANALYZE → HYPOTHESIZE → SIMULATE → EVALUATE →
  REVIEW (human gate) → CANARY (5%) → DEPLOY or ROLLBACK →
  MONITOR (30 days) → OBSERVE (repeat)

Self-Healing (PART 25):
  5-level recovery hierarchy: retry → fallback → reassign →
  pause+alert → human escalation

Organizational Learning (PART 26):
  10 learning categories, anti-poisoning via EvalRunner gate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ── PART 25: Recovery Hierarchy ───────────────────────────────────────────────


class RecoveryAction(StrEnum):
    RETRY = "retry"
    MODEL_FALLBACK = "model_fallback"
    REASSIGN = "reassign"
    ESCALATE = "escalate"
    PAUSE_ALERT = "pause_alert"
    HUMAN_REQUIRED = "human_required"


@dataclass
class RecoveryResult:
    action: RecoveryAction
    success: bool
    details: str = ""
    retry_after_seconds: int = 0
    escalated_to: str | None = None


class OrgRecoveryHierarchy:
    """
    PART 25 recovery hierarchy:
      Tool call fails → retry with exponential backoff
      Model error → model fallback cascade
      Agent task fails → retry by same agent → reassign → escalate
      Dept blocked → CEO notified → alternative path → human escalation
      Mission blocked → pause → human notification → options surfaced
      Loop detected → loop breaker → escalate
      Budget runaway → pause + alert user
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS: ClassVar[list[int]] = [1, 5, 30, 300, 1800]  # 1s, 5s, 30s, 5m, 30m

    def get_recovery_action(
        self,
        failure_type: str,
        attempt_count: int,
        context: dict[str, Any],
    ) -> RecoveryResult:
        """Determine recovery action based on failure type and attempt count."""

        if failure_type == "tool_transient":
            if attempt_count < self.MAX_RETRIES:
                delay = self.BASE_BACKOFF_SECONDS[min(attempt_count, 4)]
                return RecoveryResult(
                    action=RecoveryAction.RETRY,
                    success=True,
                    details=f"Retry {attempt_count + 1}/{self.MAX_RETRIES} after {delay}s",
                    retry_after_seconds=delay,
                )
            return RecoveryResult(
                action=RecoveryAction.ESCALATE,
                success=False,
                details=f"Max retries ({self.MAX_RETRIES}) exceeded for tool call",
            )

        if failure_type == "model_error":
            fallback_model = context.get("fallback_model")
            if fallback_model:
                return RecoveryResult(
                    action=RecoveryAction.MODEL_FALLBACK,
                    success=True,
                    details=f"Falling back to {fallback_model}",
                )
            return RecoveryResult(
                action=RecoveryAction.HUMAN_REQUIRED,
                success=False,
                details="All model fallbacks exhausted",
            )

        if failure_type == "agent_task_failed":
            if attempt_count < 2:
                return RecoveryResult(
                    action=RecoveryAction.RETRY,
                    success=True,
                    details=f"Retrying task (attempt {attempt_count + 1})",
                    retry_after_seconds=5,
                )
            if attempt_count < 3:
                return RecoveryResult(
                    action=RecoveryAction.REASSIGN,
                    success=True,
                    details="Reassigning to alternate agent in same department",
                )
            return RecoveryResult(
                action=RecoveryAction.ESCALATE,
                success=False,
                details="Task failed after reassignment — escalating to dept lead",
            )

        if failure_type == "budget_runaway":
            return RecoveryResult(
                action=RecoveryAction.PAUSE_ALERT,
                success=True,
                details="Budget threshold exceeded — pausing and alerting user",
            )

        if failure_type == "loop_detected":
            return RecoveryResult(
                action=RecoveryAction.ESCALATE,
                success=True,
                details="Loop/deadlock detected — breaking and escalating",
            )

        # NEVER auto-heal (PART 25 hard limits)
        if failure_type in (
            "production_infra_change",
            "mass_data_delete",
            "financial_transfer",
            "legal_agreement",
            "press_release",
            "mass_pii_operation",
        ):
            return RecoveryResult(
                action=RecoveryAction.HUMAN_REQUIRED,
                success=False,
                details=f"HARD LIMIT: {failure_type} always requires human. No auto-recovery.",
            )

        # Default
        return RecoveryResult(
            action=RecoveryAction.ESCALATE,
            success=False,
            details=f"Unknown failure type: {failure_type}",
        )


# ── PART 26: Organizational Learning ─────────────────────────────────────────


class LearningCategory(StrEnum):
    TEAM_COMPOSITION = "team_composition"
    MODEL_ROUTING = "model_routing"
    TOOL_RELIABILITY = "tool_reliability"
    WORKFLOW_PATTERNS = "workflow_patterns"
    KNOWLEDGE_QUALITY = "knowledge_quality"
    COLLABORATION = "collaboration"
    COST_PATTERNS = "cost_patterns"
    RISK_INDICATORS = "risk_indicators"
    APPROVAL_PATTERNS = "approval_patterns"
    AGENT_SPECIALIZATION = "agent_specialization"


@dataclass
class OrgLesson:
    """A lesson learned from mission execution."""

    id: str
    category: LearningCategory
    title: str
    content: str
    mission_id: str | None = None
    department_id: str | None = None
    confidence: float = 0.80
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "pending"  # pending | validated | promoted | quarantined
    validated_by: str | None = None


class OrgLearningSystem:
    """
    PART 26: Captures and promotes lessons from mission execution.

    Anti-poisoning:
    - New lessons require confidence > 0.70 to enter dept/org tier
    - Contradictions trigger review (not overwrite)
    - Lessons from failed missions quarantined pending review
    - PII blocked from promotion to shared tiers
    - Low-confidence lessons decay unless reinforced
    """

    MIN_CONFIDENCE = 0.70
    MIN_EVIDENCE_COUNT = 2  # must have at least 2 supporting examples to promote

    def __init__(self) -> None:
        self._lessons: dict[str, OrgLesson] = {}

    def add_lesson(self, lesson: OrgLesson) -> str:
        """Add a lesson for validation. Returns lesson_id."""
        if lesson.confidence < self.MIN_CONFIDENCE:
            raise ValueError(
                f"Lesson confidence {lesson.confidence:.2f} is below minimum "
                f"{self.MIN_CONFIDENCE:.2f}. Rejected."
            )
        # Check for PII patterns
        if self._contains_pii(lesson.content):
            raise ValueError("Lesson content contains potential PII. Rejected.")

        self._lessons[lesson.id] = lesson
        _log.info(
            "learning.lesson_added", category=lesson.category.value, confidence=lesson.confidence
        )
        return lesson.id

    def validate_lesson(self, lesson_id: str, validator: str) -> bool:
        """Validate a lesson for promotion to shared memory tiers."""
        lesson = self._lessons.get(lesson_id)
        if not lesson:
            return False
        if len(lesson.evidence) < self.MIN_EVIDENCE_COUNT:
            lesson.status = "pending"
            _log.info(
                "learning.insufficient_evidence",
                lesson_id=lesson_id,
                evidence_count=len(lesson.evidence),
            )
            return False

        lesson.status = "validated"
        lesson.validated_by = validator
        _log.info("learning.validated", lesson_id=lesson_id, category=lesson.category.value)
        return True

    def quarantine_from_failed_mission(self, mission_id: str) -> int:
        """Quarantine all lessons from a failed mission."""
        count = 0
        for lesson in self._lessons.values():
            if lesson.mission_id == mission_id and lesson.status in ("pending", "validated"):
                lesson.status = "quarantined"
                count += 1
        _log.info("learning.quarantined", mission_id=mission_id, count=count)
        return count

    def get_lessons_by_category(
        self, category: LearningCategory, min_status: str = "validated"
    ) -> list[OrgLesson]:
        status_order = {"pending": 0, "validated": 1, "promoted": 2, "quarantined": -1}
        min_order = status_order.get(min_status, 0)
        return [
            lesson
            for lesson in self._lessons.values()
            if lesson.category == category
            and status_order.get(lesson.status, -1) >= min_order
        ]

    @staticmethod
    def _contains_pii(text: str) -> bool:
        """Basic PII detection to block from shared memory."""
        import re

        patterns = [
            r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
            r"\b4[0-9]{12}(?:[0-9]{3})?\b",  # Visa
        ]
        return any(re.search(p, text) for p in patterns)


# ── PART 24: Self-Improvement Cycle ──────────────────────────────────────────


class ImprovementCyclePhase(StrEnum):
    OBSERVE = "observe"
    ANALYZE = "analyze"
    HYPOTHESIZE = "hypothesize"
    SIMULATE = "simulate"
    EVALUATE = "evaluate"
    REVIEW = "review"
    CANARY = "canary"
    DEPLOY = "deploy"
    MONITOR = "monitor"


@dataclass
class ImprovementProposal:
    """An improvement proposal from the self-optimization cycle."""

    id: str
    area: str  # model_routing | team_composition | workflow_patterns | etc.
    hypothesis: str
    expected_improvement_pct: float
    confidence: float
    evidence: list[str] = field(default_factory=list)
    phase: ImprovementCyclePhase = ImprovementCyclePhase.OBSERVE
    canary_pct: float = 0.05  # 5% traffic for canary
    approved_by: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    deployed_at: str | None = None
    rollback_at: str | None = None


class OrgSelfImprovementEngine:
    """
    PART 24: Drives the continuous improvement cycle for the org.

    Improvements that can be made:
    - model_routing: which model for which role/task type
    - team_composition: which role combos work for which missions
    - workflow_patterns: which shapes work for which goals
    - context_selection: what context helps for what tasks
    - tool_selection: which tools are reliable for each domain
    - knowledge_quality: which sources are authoritative
    - cost_optimization: model + token selection
    """

    IMPROVABLE_AREAS: ClassVar[list[str]] = [
        "model_routing",
        "team_composition",
        "workflow_patterns",
        "context_selection",
        "tool_selection",
        "knowledge_quality",
        "cost_optimization",
    ]

    def __init__(self) -> None:
        self._proposals: dict[str, ImprovementProposal] = {}

    def create_proposal(
        self,
        area: str,
        hypothesis: str,
        expected_improvement_pct: float,
        confidence: float,
        evidence: list[str],
    ) -> ImprovementProposal:
        """Create an improvement proposal from observation data."""
        if area not in self.IMPROVABLE_AREAS:
            raise ValueError(f"Unknown improvement area: {area}. Valid: {self.IMPROVABLE_AREAS}")

        import uuid

        proposal = ImprovementProposal(
            id=f"imp_{uuid.uuid4().hex[:12]}",
            area=area,
            hypothesis=hypothesis,
            expected_improvement_pct=expected_improvement_pct,
            confidence=confidence,
            evidence=evidence,
            phase=ImprovementCyclePhase.OBSERVE,
        )
        self._proposals[proposal.id] = proposal
        _log.info("improvement.proposal_created", area=area, confidence=confidence)
        return proposal

    def advance_phase(self, proposal_id: str, approver: str | None = None) -> str:
        """Advance proposal to next phase in cycle."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        phase_order = list(ImprovementCyclePhase)
        current_idx = phase_order.index(proposal.phase)

        # REVIEW phase requires human approval
        if proposal.phase == ImprovementCyclePhase.EVALUATE:
            if not approver:
                _log.info("improvement.awaiting_approval", proposal_id=proposal_id)
                return proposal.phase.value
            proposal.approved_by = approver

        if current_idx < len(phase_order) - 1:
            proposal.phase = phase_order[current_idx + 1]
            if proposal.phase == ImprovementCyclePhase.DEPLOY:
                proposal.deployed_at = datetime.now(UTC).isoformat()
            _log.info(
                "improvement.phase_advanced", proposal_id=proposal_id, phase=proposal.phase.value
            )

        return proposal.phase.value

    def rollback(self, proposal_id: str, reason: str) -> None:
        """Rollback a deployed improvement."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            return
        proposal.phase = ImprovementCyclePhase.OBSERVE  # back to start
        proposal.rollback_at = datetime.now(UTC).isoformat()
        _log.info("improvement.rolled_back", proposal_id=proposal_id, reason=reason)

    def list_by_phase(self, phase: ImprovementCyclePhase) -> list[ImprovementProposal]:
        return [p for p in self._proposals.values() if p.phase == phase]
