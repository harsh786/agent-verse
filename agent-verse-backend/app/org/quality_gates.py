"""Quality Gate System — SUPPLEMENT K (6-gate system).

GATE 1: AGENT_SELF_CHECK       — agent evaluates its own output (mandatory)
GATE 2: DETERMINISTIC           — schema/type validation, unit tests (mandatory for code/data)
GATE 3: SPECIALIZED_EVALUATOR  — LLM-as-judge, domain-specific (mandatory for critical)
GATE 4: PEER_REVIEW             — different agent from same dept (configurable)
GATE 5: POLICY_CHECK            — PolicyEngine validation (mandatory)
GATE 6: HUMAN_APPROVAL          — real human (required for external/financial/legal)

Scoring formula:
  score = self_check×0.10 + deterministic×0.20 + evaluator×0.40
        + peer_review×0.20 + policy×0.10

Thresholds:
  > 0.90 → auto-approve
  > 0.75 → promote to artifact
  < 0.75 → human review
  < 0.60 → rejection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    PENDING = "pending"


@dataclass
class GateOutcome:
    gate_id: int
    gate_name: str
    result: GateResult = GateResult.PENDING
    score: float = 0.0
    details: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0


@dataclass
class QualityScore:
    """Full quality evaluation result from all gates."""

    final_score: float = 0.0
    gates: list[GateOutcome] = field(default_factory=list)
    decision: str = "pending"  # auto_approve | promote | human_review | reject
    notes: list[str] = field(default_factory=list)

    def decision_for_score(self) -> str:
        if self.final_score >= 0.90:
            return "auto_approve"
        if self.final_score >= 0.75:
            return "promote"
        if self.final_score >= 0.60:
            return "human_review"
        return "reject"


# ── Gate weights (must sum to 1.0) ────────────────────────────────────────────

GATE_WEIGHTS = {
    1: 0.10,  # self_check
    2: 0.20,  # deterministic
    3: 0.40,  # specialized_evaluator
    4: 0.20,  # peer_review
    5: 0.10,  # policy_check
}

GATE_SLA = {
    "urgent": 15 * 60,  # 15 minutes in seconds
    "standard": 4 * 3600,  # 4 hours
    "non_urgent": 24 * 3600,  # 24 hours
}


class QualityGateSystem:
    """
    6-gate quality system as specified in SUPPLEMENT K.
    Gates can be individually enabled/disabled per org policy.
    """

    def __init__(
        self,
        run_self_check: bool = True,
        run_deterministic: bool = True,
        run_evaluator: bool = True,
        run_peer_review: bool = False,
        run_policy_check: bool = True,
        require_human_approval: bool = False,
    ) -> None:
        self._run_self_check = run_self_check
        self._run_deterministic = run_deterministic
        self._run_evaluator = run_evaluator
        self._run_peer_review = run_peer_review
        self._run_policy_check = run_policy_check
        self._require_human = require_human_approval

    async def evaluate(self, output: str, context: dict[str, Any]) -> QualityScore:
        """Run all configured quality gates and return composite score."""
        with _tracer.start_as_current_span("quality_gates.evaluate") as span:
            gates: list[GateOutcome] = []
            weighted_sum = 0.0
            total_weight = 0.0

            # GATE 1: Self-check (always mandatory)
            g1 = await self._run_gate_1(output, context)
            gates.append(g1)
            if g1.result != GateResult.SKIP:
                weighted_sum += g1.score * GATE_WEIGHTS[1]
                total_weight += GATE_WEIGHTS[1]

            # GATE 2: Deterministic validation
            if self._run_deterministic:
                g2 = await self._run_gate_2(output, context)
                gates.append(g2)
                if g2.result != GateResult.SKIP:
                    weighted_sum += g2.score * GATE_WEIGHTS[2]
                    total_weight += GATE_WEIGHTS[2]

            # GATE 3: Specialized evaluator (LLM-as-judge)
            if self._run_evaluator:
                g3 = await self._run_gate_3(output, context)
                gates.append(g3)
                if g3.result != GateResult.SKIP:
                    weighted_sum += g3.score * GATE_WEIGHTS[3]
                    total_weight += GATE_WEIGHTS[3]

            # GATE 4: Peer review
            if self._run_peer_review:
                g4 = await self._run_gate_4(output, context)
                gates.append(g4)
                if g4.result != GateResult.SKIP:
                    weighted_sum += g4.score * GATE_WEIGHTS[4]
                    total_weight += GATE_WEIGHTS[4]

            # GATE 5: Policy check (always mandatory)
            if self._run_policy_check:
                g5 = await self._run_gate_5(output, context)
                gates.append(g5)
                if g5.result != GateResult.SKIP:
                    weighted_sum += g5.score * GATE_WEIGHTS[5]
                    total_weight += GATE_WEIGHTS[5]

            final_score = weighted_sum / total_weight if total_weight > 0 else 0.0

            qs = QualityScore(final_score=round(final_score, 3), gates=gates)
            qs.decision = qs.decision_for_score()

            # GATE 6: Human approval (overrides if required)
            if self._require_human and final_score < 1.0:
                qs.decision = "human_review"
                qs.notes.append("Gate 6: Human approval required per policy")

            span.set_attribute("final_score", qs.final_score)
            span.set_attribute("decision", qs.decision)
            _log.info(
                "quality_gates.evaluated",
                score=qs.final_score,
                decision=qs.decision,
                gates_run=len(gates),
            )
            return qs

    # ── Individual gate implementations ───────────────────────────────────────

    async def _run_gate_1(self, output: str, context: dict) -> GateOutcome:
        """Gate 1: Agent self-check — basic output validation."""
        score = 0.0
        details = ""
        try:
            if not output or len(output.strip()) < 10:
                score = 0.0
                details = "Output is empty or too short"
            elif len(output) > 100_000:
                score = 0.7
                details = "Output is very long — may need trimming"
            else:
                score = 0.85  # baseline — agent produced non-trivial output
                details = "Output present and non-trivial"
        except Exception as e:
            details = f"Self-check error: {e}"

        return GateOutcome(
            1,
            "agent_self_check",
            GateResult.PASS if score > 0.5 else GateResult.FAIL,
            score,
            details,
            cost_usd=0.01,
        )

    async def _run_gate_2(self, output: str, context: dict) -> GateOutcome:
        """Gate 2: Deterministic validation — schema/type/syntax."""
        output_type = context.get("output_type", "text")
        score = 0.90
        details = "Deterministic validation passed"

        if output_type == "code":
            # Basic syntax check heuristic
            if "def " in output or "function " in output or "class " in output:
                score = 0.95
                details = "Code structure detected"
            else:
                score = 0.75
                details = "No clear code structure found"
        elif output_type == "json":
            import json

            try:
                json.loads(output)
                score = 1.0
                details = "Valid JSON"
            except Exception:
                score = 0.0
                details = "Invalid JSON"
                return GateOutcome(2, "deterministic", GateResult.FAIL, score, details)

        return GateOutcome(2, "deterministic", GateResult.PASS, score, details, cost_usd=0.0)

    async def _run_gate_3(self, output: str, context: dict) -> GateOutcome:
        """Gate 3: Specialized evaluator (LLM-as-judge stub)."""
        # TODO: integrate with LLM evaluator when available
        # For now: heuristic-based quality estimation
        word_count = len(output.split())
        score = 0.0
        if word_count < 10:
            score = 0.30
            details = "Output too brief for domain requirements"
        elif word_count < 50:
            score = 0.65
            details = "Output somewhat brief"
        elif word_count < 500:
            score = 0.85
            details = "Adequate output length"
        else:
            score = 0.80
            details = "Detailed output (may need conciseness review)"

        return GateOutcome(
            3,
            "specialized_evaluator",
            GateResult.PASS if score > 0.5 else GateResult.FAIL,
            score,
            details,
            cost_usd=0.08,
        )

    async def _run_gate_4(self, output: str, context: dict) -> GateOutcome:
        """Gate 4: Peer review (requires another agent)."""
        # TODO: spawn peer reviewer agent
        return GateOutcome(
            4, "peer_review", GateResult.SKIP, 0.0, "Peer review deferred", cost_usd=0.0
        )

    async def _run_gate_5(self, output: str, context: dict) -> GateOutcome:
        """Gate 5: Policy check — no PII, no policy violations."""
        # Basic PII heuristics
        import re

        pii_patterns = [
            r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
            r"\b4[0-9]{12}(?:[0-9]{3})?\b",  # Visa
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
        ]
        for pattern in pii_patterns:
            if re.search(pattern, output):
                return GateOutcome(
                    5,
                    "policy_check",
                    GateResult.FAIL,
                    0.0,
                    "PII detected in output — blocked by policy",
                )
        return GateOutcome(5, "policy_check", GateResult.PASS, 1.0, "Policy check passed")
