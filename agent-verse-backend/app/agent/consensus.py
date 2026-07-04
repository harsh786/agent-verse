"""
3-Way Consensus Verification for high-stakes goals.

For goals touching write_high/destructive tools or regulated domains,
run up to 3 verifiers:
  1. Primary verifier (standard)
  2. Cross-model verifier (different provider)
  3. LLM Judge (rubric-scored)

Majority required for success. On disagreement → HITL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain / tool-risk constants
# ---------------------------------------------------------------------------

_REQUIRES_CONSENSUS_DOMAINS = frozenset({
    "legal",
    "gst-tax",
    "banking-fintech",
    "healthcare",
    "pharmaceutical",
    "government-portal",
})

_REGULATED_TOOL_RISK = frozenset({"write_high", "destructive"})


# ---------------------------------------------------------------------------
# Public helper: decide if consensus is needed
# ---------------------------------------------------------------------------


def requires_consensus(
    goal: str | None = None,
    domain: str | None = None,
    tool_risks: list[str] | None = None,
    *,
    # Alternative keyword-only form: pass raw steps + policy_engine + regulated flag
    steps: list[Any] | None = None,
    policy_engine: Any | None = None,
    regulated: bool = False,
) -> bool:
    """Return True when this goal warrants 3-way consensus verification.

    Two calling conventions are supported:

    1. Simple form (user-facing tests)::

        requires_consensus("deploy app", None, ["write_high"])

    2. Step-based form (plan Task 14)::

        requires_consensus(steps=agent_state.steps, regulated=True)
    """
    # --- simple form ---
    if domain and domain.lower() in _REQUIRES_CONSENSUS_DOMAINS:
        return True
    if tool_risks and any(r in _REGULATED_TOOL_RISK for r in tool_risks):
        return True

    # --- step-based form ---
    if regulated:
        return True

    if steps:
        from app.agent.tool_risk import classify_tool_risk
        for step in steps:
            for tc in getattr(step, "tool_calls", []) or []:
                risk = classify_tool_risk(
                    tc.get("tool_name", ""),
                    tc.get("server_name", ""),
                )
                if risk in _REGULATED_TOOL_RISK:
                    return True

    return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VerifierVote:
    success: bool
    reason: str
    verifier_id: str  # "primary" | "cross_model" | "judge"
    confidence: float = 0.8
    # Plan-compatible alias
    source: str = ""
    score: float = 0.0

    def __post_init__(self) -> None:
        # Keep source ↔ verifier_id in sync
        if not self.source:
            self.source = self.verifier_id


@dataclass
class ConsensusResult:
    success: bool
    votes: list[VerifierVote]
    unanimous: bool
    requires_hitl: bool
    majority_reason: str = ""
    # Plan-compatible alias
    agreement: float = field(default=0.0)

    def __post_init__(self) -> None:
        if self.votes and not self.agreement:
            successes = sum(1 for v in self.votes if v.success)
            self.agreement = successes / len(self.votes) if self.votes else 0.0


# ---------------------------------------------------------------------------
# ConsensusVerifier
# ---------------------------------------------------------------------------


class ConsensusVerifier:
    """Runs up to 3-way verification and returns a ConsensusResult.

    Falls back gracefully when secondary verifiers are unavailable.

    Supports two construction styles:

    1. Keyword-only ``primary_verifier``, ``cross_model_verifier``, ``judge_verifier``
       (user-facing tests).

    2. Keyword-only ``primary``, ``cross_model``, ``judge``
       (plan Task 15 style).
    """

    def __init__(
        self,
        primary_verifier: Any | None = None,
        cross_model_verifier: Any | None = None,
        judge_verifier: Any | None = None,
        *,
        # Plan-style aliases
        primary: Any | None = None,
        cross_model: Any | None = None,
        judge: Any | None = None,
        min_agreement_for_auto: float = 1.0,
    ) -> None:
        self._primary = primary_verifier if primary_verifier is not None else primary
        self._cross = cross_model_verifier if cross_model_verifier is not None else cross_model
        self._judge = judge_verifier if judge_verifier is not None else judge
        self._min_agreement_for_auto = min_agreement_for_auto

        if self._primary is None:
            raise ValueError("ConsensusVerifier requires at least a primary verifier")

    async def verify(
        self,
        goal: str,
        summary: str,
        model: str = "",
        *,
        actual_output: str = "",
        tools_called: list[str] | None = None,
        forbidden_tools: list[str] | None = None,
        tenant_id: str = "",
    ) -> ConsensusResult:
        """Run all configured verifiers and compute majority verdict."""
        from app.agent.prompts import VERIFIER_SYSTEM
        from app.agent.schemas import parse_verifier_verdict
        from app.providers.base import CompletionRequest, Message

        user_content = f"Goal: {goal}\nExecuted steps:\n{summary}"

        async def _run_verifier(provider: Any, verifier_id: str) -> VerifierVote:
            try:
                req = CompletionRequest(
                    messages=[
                        Message(role="system", content=VERIFIER_SYSTEM),
                        Message(role="user", content=user_content),
                    ],
                    model=model,
                )
                resp = await provider.complete(req)
                parsed = parse_verifier_verdict(resp.content)
                return VerifierVote(
                    success=parsed["success"],
                    reason=parsed.get("reason", "")[:200],
                    verifier_id=verifier_id,
                    confidence=float(parsed.get("confidence", 0.8)),
                )
            except Exception as exc:
                logger.warning(
                    "consensus_verifier_failed",
                    id=verifier_id,
                    error=str(exc)[:60],
                )
                # Fail-closed: exception counts as success=False vote
                return VerifierVote(
                    success=False,
                    reason=f"verifier error: {exc!s:.60}",
                    verifier_id=verifier_id,
                )

        votes: list[VerifierVote] = []

        # Primary always runs
        votes.append(await _run_verifier(self._primary, "primary"))

        # Cross-model (if available)
        if self._cross is not None:
            votes.append(await _run_verifier(self._cross, "cross_model"))

        # LLM Judge (if available)
        if self._judge is not None:
            votes.append(await _run_judge(self._judge, goal, summary, votes))

        # Majority vote
        successes = sum(1 for v in votes if v.success)
        total = len(votes)
        majority_success = successes > total / 2

        unanimous = all(v.success == votes[0].success for v in votes)
        agreement = successes / total if total else 0.0
        requires_hitl = not unanimous and total >= 2

        majority_reason = next(
            (v.reason for v in votes if v.success == majority_success), ""
        )

        logger.info(
            "consensus_result",
            success=majority_success,
            votes=[v.success for v in votes],
            unanimous=unanimous,
            requires_hitl=requires_hitl,
        )

        return ConsensusResult(
            success=majority_success,
            votes=votes,
            unanimous=unanimous,
            requires_hitl=requires_hitl,
            majority_reason=majority_reason,
            agreement=agreement,
        )


# ---------------------------------------------------------------------------
# Internal judge helper
# ---------------------------------------------------------------------------


async def _run_judge(
    judge_provider: Any,
    goal: str,
    summary: str,
    prior_votes: list[VerifierVote],
) -> VerifierVote:
    """Run the LLM judge with a rubric-scored prompt."""
    from app.agent.prompts import JUDGE_RUBRIC_SYSTEM
    from app.agent.schemas import parse_verifier_verdict
    from app.providers.base import CompletionRequest, Message

    prior_verdicts = "\n".join(
        f"- {v.verifier_id}: {'SUCCESS' if v.success else 'FAIL'} — {v.reason[:100]}"
        for v in prior_votes
    )

    try:
        # Check if this is an LLMJudge with a score() method
        if hasattr(judge_provider, "score"):
            _score_result = await judge_provider.score(
                goal=goal,
                output=summary,
                tools_called=[],
                forbidden_tools=[],
            )
            overall = _score_result.get("overall", 0.0) if isinstance(_score_result, dict) else 0.0
            _success = float(overall) >= 0.7
            return VerifierVote(
                success=_success,
                reason=f"judge score: {overall}",
                verifier_id="judge",
                confidence=float(overall),
                score=float(overall),
            )

        req = CompletionRequest(
            messages=[
                Message(role="system", content=JUDGE_RUBRIC_SYSTEM),
                Message(
                    role="user",
                    content=(
                        f"Goal: {goal}\n\nExecution summary:\n{summary[:1000]}\n\n"
                        f"Prior verifier verdicts:\n{prior_verdicts}\n\n"
                        "Apply the rubric and return JSON: "
                        '{"success": true/false, "reason": "...", "confidence": 0.0-1.0}'
                    ),
                ),
            ],
            model="",
        )
        resp = await judge_provider.complete(req)
        parsed = parse_verifier_verdict(resp.content)
        return VerifierVote(
            success=parsed["success"],
            reason=parsed.get("reason", "")[:200],
            verifier_id="judge",
            confidence=float(parsed.get("confidence", 0.7)),
            score=float(parsed.get("confidence", 0.7)),
        )
    except Exception as exc:
        logger.warning("judge_failed", error=str(exc)[:60])
        return VerifierVote(
            success=False,
            reason="judge error",
            verifier_id="judge",
        )
