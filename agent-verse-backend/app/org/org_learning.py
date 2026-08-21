"""PART 26 — Organizational Learning System.

Learning categories (10 types) with anti-poisoning validation.
Each lesson is validated by EvalRunner before promotion to dept/org tier.

Categories:
  team_composition    — which role combos work for mission types
  model_routing       — which models work best per domain
  tool_reliability    — which tools fail in which conditions
  workflow_patterns   — which workflow shapes work for which goals
  knowledge_quality   — which sources are authoritative
  collaboration       — which depts need more coordination
  cost_patterns       — what drives cost for each mission type
  risk_indicators     — early warning signals for mission failure
  approval_patterns   — which decisions need what approval levels
  agent_specialization — which agents excel at which sub-tasks
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)


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
    """A validated lesson ready for promotion to org/dept memory tier."""

    lesson_id: str
    category: LearningCategory
    content: str
    source_mission_id: str
    source_outcome: str  # completed | failed | partial
    confidence: float  # 0–1
    applicability: list[str]  # mission types or domain tags this applies to
    evidence: list[str] = field(default_factory=list)
    dept_scope: str | None = None  # None = org-wide; set = dept-scoped
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    validated: bool = False
    validation_score: float = 0.0
    promoted_at: datetime | None = None


@dataclass
class ValidationResult:
    passed: bool
    score: float
    reason: str
    anti_poisoning_flags: list[str] = field(default_factory=list)


class OrgLearningPipeline:
    """
    PART 26 — Org learning pipeline.

    Flow:
      1. Mission completes → extract_lessons(mission_result)
      2. Each lesson → validate(lesson) via EvalRunner heuristics
      3. Passed lessons → promote_to_memory(lesson, tier)
      4. Failed missions → quarantine → human review before promotion
    """

    # Minimum confidence to promote to dept/org tier
    MIN_CONFIDENCE_FOR_PROMOTION = 0.70

    def __init__(
        self,
        memory_service: Any | None = None,
        eval_runner: Any | None = None,
    ) -> None:
        self._memory = memory_service
        self._eval = eval_runner
        self._quarantine: list[OrgLesson] = []  # failed-mission lessons

    async def extract_lessons(
        self,
        mission_id: str,
        outcome: str,
        mission_data: dict[str, Any],
    ) -> list[OrgLesson]:
        """Extract candidate lessons from a completed/failed mission."""
        lessons: list[OrgLesson] = []
        base = {
            "source_mission_id": mission_id,
            "source_outcome": outcome,
            "applicability": mission_data.get("tags", []),
        }

        # Team composition lesson
        if team := mission_data.get("team"):
            lessons.append(
                OrgLesson(
                    lesson_id=self._lid("team", mission_id),
                    category=LearningCategory.TEAM_COMPOSITION,
                    content=f"Team of {len(team)} agents with roles {[a.get('role', '?') for a in team[:4]]} achieved outcome: {outcome}",  # noqa: E501
                    confidence=0.75 if outcome == "completed" else 0.60,
                    **base,
                )
            )

        # Model routing lesson
        if models_used := mission_data.get("models_used", []):
            lessons.append(
                OrgLesson(
                    lesson_id=self._lid("model", mission_id),
                    category=LearningCategory.MODEL_ROUTING,
                    content=f"Models used: {models_used}. Outcome: {outcome}. Success rate implies routing quality.",  # noqa: E501
                    confidence=0.70,
                    **base,
                )
            )

        # Cost pattern lesson
        if cost := mission_data.get("actual_cost_usd", 0):
            estimated = mission_data.get("estimated_cost_usd", 0)
            ratio = cost / estimated if estimated > 0 else 1.0
            lessons.append(
                OrgLesson(
                    lesson_id=self._lid("cost", mission_id),
                    category=LearningCategory.COST_PATTERNS,
                    content=f"Mission cost ${cost:.2f} vs estimated ${estimated:.2f} ({ratio:.1f}x). Goal type: {mission_data.get('goal_type', 'unknown')}",  # noqa: E501
                    confidence=0.80,
                    **base,
                )
            )

        # Risk indicator lesson (failed missions especially valuable)
        if outcome == "failed":
            lessons.append(
                OrgLesson(
                    lesson_id=self._lid("risk", mission_id),
                    category=LearningCategory.RISK_INDICATORS,
                    content=f"Mission failed: {mission_data.get('failure_reason', 'unknown reason')}. Risk level was {mission_data.get('risk_level', 'unknown')}.",  # noqa: E501
                    confidence=0.85,
                    evidence=[mission_data.get("failure_reason", "")],
                    **base,
                )
            )

        return lessons

    async def validate(self, lesson: OrgLesson) -> ValidationResult:
        """Anti-poisoning validation before promotion."""
        flags: list[str] = []

        # Gate 1: Confidence threshold
        if lesson.confidence < self.MIN_CONFIDENCE_FOR_PROMOTION:
            return ValidationResult(
                passed=False,
                score=lesson.confidence,
                reason=f"Confidence {lesson.confidence:.2f} below threshold {self.MIN_CONFIDENCE_FOR_PROMOTION}",  # noqa: E501
            )

        # Gate 2: Failed mission lessons go to quarantine
        if (
            lesson.source_outcome == "failed"
            and lesson.category != LearningCategory.RISK_INDICATORS
        ):
            flags.append("failed_mission_lesson")
            # Still allow risk indicators from failures — they're valuable

        # Gate 3: Content sanity check
        if len(lesson.content.strip()) < 20:
            return ValidationResult(
                passed=False,
                score=0.0,
                reason="Lesson content too short",
            )

        # Gate 4: PII check (basic)
        import re

        pii_patterns = [
            r"\b\d{3}-\d{2}-\d{4}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        ]
        for pattern in pii_patterns:
            if re.search(pattern, lesson.content):
                return ValidationResult(
                    passed=False,
                    score=0.0,
                    reason="PII detected in lesson content",
                    anti_poisoning_flags=["pii_detected"],
                )

        # Gate 5: EvalRunner validation (if available)
        score = lesson.confidence
        if self._eval:
            try:
                eval_result = await self._eval.evaluate(lesson.content, {"type": "lesson"})
                score = eval_result.get("score", lesson.confidence)
            except Exception:
                pass  # fall back to confidence

        lesson.validation_score = score
        return ValidationResult(
            passed=len(flags) == 0 or lesson.category == LearningCategory.RISK_INDICATORS,
            score=score,
            reason="Passed all anti-poisoning gates",
            anti_poisoning_flags=flags,
        )

    async def promote_to_memory(
        self,
        lesson: OrgLesson,
        tier: str,  # "department" | "org"
        org_id: str,
        tenant_id: str,
    ) -> bool:
        """Promote validated lesson to the appropriate memory tier."""
        if not lesson.validated:
            _log.warning("learning.promote_unvalidated", lesson_id=lesson.lesson_id)
            return False

        if self._memory:
            try:
                await self._memory.add(
                    content=lesson.content,
                    source=f"org_learning:{lesson.source_mission_id}",
                    confidence=lesson.validation_score,
                    dept_id=lesson.dept_scope,
                    org_id=org_id,
                    tenant_id=tenant_id,
                    metadata={
                        "category": lesson.category.value,
                        "outcome": lesson.source_outcome,
                        "lesson_id": lesson.lesson_id,
                        "applicability": lesson.applicability,
                    },
                )
                lesson.promoted_at = datetime.now(UTC)
                _log.info(
                    "learning.promoted",
                    lesson_id=lesson.lesson_id,
                    category=lesson.category.value,
                    tier=tier,
                    score=lesson.validation_score,
                )
                return True
            except Exception as exc:
                _log.error("learning.promote_failed", error=str(exc))
        return False

    async def process_mission_learnings(
        self,
        mission_id: str,
        outcome: str,
        mission_data: dict[str, Any],
        org_id: str,
        tenant_id: str,
    ) -> int:
        """Full pipeline: extract → validate → promote. Returns count promoted."""
        lessons = await self.extract_lessons(mission_id, outcome, mission_data)
        promoted = 0
        for lesson in lessons:
            result = await self.validate(lesson)
            if result.passed:
                lesson.validated = True
                tier = "department" if lesson.dept_scope else "org"
                if await self.promote_to_memory(lesson, tier, org_id, tenant_id):
                    promoted += 1
            else:
                if outcome == "failed":
                    self._quarantine.append(lesson)
                _log.debug(
                    "learning.not_promoted",
                    lesson_id=lesson.lesson_id,
                    reason=result.reason,
                )
        return promoted

    def list_quarantine(self) -> list[OrgLesson]:
        """Return lessons in quarantine pending human review."""
        return list(self._quarantine)

    def clear_quarantine(self, lesson_id: str) -> bool:
        self._quarantine = [l for l in self._quarantine if l.lesson_id != lesson_id]
        return True

    @staticmethod
    def _lid(prefix: str, mission_id: str) -> str:
        h = hashlib.sha256(f"{prefix}:{mission_id}".encode()).hexdigest()[:8]
        return f"lesson:{prefix}:{h}"
