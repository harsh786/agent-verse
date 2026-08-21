"""Peer Review pattern — independent LLM review of agent output quality.

A separate 'reviewer' LLM evaluates the output for:
  - Accuracy: is the answer factually correct?
  - Completeness: does it fully address the goal?
  - Quality score: 0.0 (terrible) to 1.0 (excellent)
  - Approved: whether the output meets a minimum quality bar
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.reasoning_evidence import (
    ReasoningEvidence,
    ReasoningExecution,
    critique_categories,
)

_PEER_REVIEW_SYSTEM = """You are a rigorous quality reviewer for an AI agent's output.
Evaluate the output against the goal and respond with a JSON object:
{
  "quality_score": <float 0.0-1.0>,
  "critique": "<what is good or bad about this output>",
  "suggestions": ["<improvement 1>", "<improvement 2>"],
  "approved": <true if quality_score >= 0.7, else false>
}
Be objective and specific. If the output is excellent, score 0.9+.
If incomplete or wrong, score below 0.5."""

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "quality_score": {"type": "number"},
        "critique": {"type": "string"},
        "suggestions": {"type": "array"},
        "approved": {"type": "boolean"},
    },
}


@dataclass
class PeerReviewResult:
    quality_score: float
    critique: str
    suggestions: list[str] = field(default_factory=list)
    approved: bool = False
    raw_response: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], raw: str = "") -> PeerReviewResult:
        score = float(d.get("quality_score", 0.5))
        return cls(
            quality_score=max(0.0, min(1.0, score)),
            critique=str(d.get("critique", "")),
            suggestions=[str(s) for s in d.get("suggestions", [])],
            approved=bool(d.get("approved", score >= 0.7)),
            raw_response=raw,
        )

    @classmethod
    def from_raw(cls, raw: str) -> PeerReviewResult:
        """Parse from possibly-non-JSON response."""
        import json

        try:
            d = json.loads(raw)
            return cls.from_dict(d, raw)
        except Exception:
            pass
        # Try to extract score from text
        score_match = re.search(r"(\d+\.?\d*)\s*/\s*10", raw)
        if score_match:
            score = float(score_match.group(1)) / 10.0
        elif any(w in raw.lower() for w in ("excellent", "perfect", "great")):
            score = 0.85
        elif any(w in raw.lower() for w in ("poor", "incomplete", "wrong")):
            score = 0.3
        else:
            score = 0.6
        return cls(
            quality_score=score,
            critique=raw[:500],
            suggestions=[],
            approved=score >= 0.7,
            raw_response=raw,
        )


class PeerReviewPattern(AgentPattern):
    """Peer Review: independent LLM reviewer evaluates output quality."""

    def __init__(self, quality_threshold: float = 0.7) -> None:
        self._threshold = quality_threshold

    @property
    def pattern_id(self) -> str:
        return "peer_review"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Peer Review: a separate reviewer LLM scores output quality (0-1), "
            "provides critique and improvement suggestions, and approves/rejects. "
            f"Approval threshold: {self._threshold}."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings

            if not get_settings().enable_peer_review:
                return False
        except Exception:
            pass
        return True

    async def execute(
        self,
        *,
        output: str,
        goal: str,
        provider: Any,
        max_tokens: int = 600,
        **kwargs: Any,
    ) -> PeerReviewResult:
        """Review `output` against `goal`. Returns PeerReviewResult."""
        try:
            from app.providers.base import CompletionRequest, Message

            prompt = (
                f"Goal: {goal[:200]}\n\n"
                f"Output to review:\n{output[:1500]}\n\n"
                "Evaluate this output and respond with the JSON schema."
            )
            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_PEER_REVIEW_SYSTEM),
                        Message(role="user", content=prompt),
                    ],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.0,
                    response_schema=_REVIEW_SCHEMA,
                )
            )
            raw = (resp.content or "").strip()
            return PeerReviewResult.from_raw(raw)
        except Exception:
            return PeerReviewResult(
                quality_score=0.5,
                critique="review unavailable",
                approved=False,
            )

    async def execute_with_evidence(
        self,
        *,
        output: str,
        goal: str,
        provider: Any,
        producer_identity: str,
        reviewer_identity: str,
        max_tokens: int = 600,
        **kwargs: Any,
    ) -> ReasoningExecution:
        if not reviewer_identity or reviewer_identity == producer_identity:
            return ReasoningExecution(
                result=PeerReviewResult(
                    quality_score=0.0,
                    critique="independent reviewer unavailable",
                    approved=False,
                ),
                evidence=ReasoningEvidence(
                    strategy_id=self.pattern_id,
                    status="rejected",
                    call_count=0,
                    approved=False,
                    limit_reason="reviewer_not_independent",
                    safe_rationale_summary="independent reviewer identity is required",
                ),
            )
        review = await self.execute(
            output=output,
            goal=goal,
            provider=provider,
            max_tokens=max_tokens,
            **kwargs,
        )
        categories = critique_categories(review.critique)
        return ReasoningExecution(
            result=review,
            evidence=ReasoningEvidence(
                strategy_id=self.pattern_id,
                status="completed" if review.approved else "degraded",
                call_count=1,
                scores=(review.quality_score,),
                critique_categories=categories,
                approved=review.approved,
                safe_rationale_summary=("independent review categories: " + ", ".join(categories)),
            ),
        )
