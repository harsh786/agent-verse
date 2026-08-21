"""Toxicity classifier — pattern-based first pass + optional LLM second pass.

Categories
----------
hate_speech     : derogatory language targeting identity groups
threat          : direct threats of physical harm
sexual          : explicit sexual content
self_harm       : content promoting self-injury or suicide
violence        : explicit descriptions of graphic violence
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

ToxicityCategory = Literal["hate_speech", "threat", "sexual", "self_harm", "violence"]

# Heuristic pattern sets per category
_PATTERNS: dict[str, list[str]] = {
    "hate_speech": [
        r"\b(slur_placeholder|hate.*group|kill all \w+)\b",  # placeholder — replace with curated list  # noqa: E501
        r"\b(racial|ethnic)\s+(inferior|subhuman)\b",
    ],
    "threat": [
        r"\bi('ll| will| am going to) (kill|hurt|harm|attack|murder) (you|him|her|them)\b",
        r"\byour (address|location|home).*find\b",
        r"\bpay for this\b.*\b(hurt|harm|kill)\b",
    ],
    "sexual": [
        r"\b(explicit_sexual_placeholder)\b",  # placeholder — wire real content moderation
    ],
    "self_harm": [
        r"\b(how to|ways to|best way to) (commit suicide|harm yourself|end your life)\b",
        r"\b(kill yourself|kys)\b",
    ],
    "violence": [
        r"\b(step.by.step|instructions?|guide|tutorial) (to|for) (making|building) (bomb|explosive|weapon)\b",  # noqa: E501
        r"\bhow to (make|build|create) (nerve agent|poison gas|bioweapon)\b",
    ],
}

_COMPILED: dict[str, list[re.Pattern[str]]] = {
    cat: [re.compile(p, re.IGNORECASE) for p in pats] for cat, pats in _PATTERNS.items()
}

# Ambiguous zone where we invoke the LLM for a second opinion
_AMBIGUOUS_SCORE_MIN = 0.3
_AMBIGUOUS_SCORE_MAX = 0.7


@dataclass
class ToxicityResult:
    score: float  # 0.0 = clean, 1.0 = clearly toxic
    is_toxic: bool
    categories: list[str] = field(default_factory=list)
    method: str = "pattern"  # "pattern" | "llm" | "combined"


class ToxicityClassifier:
    """Two-pass toxicity classifier.

    Pass 1: fast regex patterns → definitive for clearly toxic / clean content.
    Pass 2: LLM judge for ambiguous cases (score 0.3–0.7).
    """

    def __init__(
        self,
        toxicity_threshold: float = 0.6,
        use_llm_for_ambiguous: bool = True,
    ) -> None:
        self._threshold = toxicity_threshold
        self._use_llm = use_llm_for_ambiguous

    def classify_sync(self, text: str) -> ToxicityResult:
        """Pattern-only classification (no LLM, always sync-safe)."""
        return self._pattern_check(text)

    async def classify(
        self,
        text: str,
        provider: LLMProvider | None = None,
    ) -> ToxicityResult:
        """Full two-pass classification."""
        pattern_result = self._pattern_check(text)

        if not self._use_llm or provider is None:
            return pattern_result

        # LLM second pass only for ambiguous cases
        if _AMBIGUOUS_SCORE_MIN <= pattern_result.score <= _AMBIGUOUS_SCORE_MAX:
            llm_result = await self._llm_check(text, provider)
            combined_score = (pattern_result.score + llm_result.score) / 2.0
            combined_cats = list(set(pattern_result.categories + llm_result.categories))
            return ToxicityResult(
                score=round(combined_score, 3),
                is_toxic=combined_score >= self._threshold,
                categories=combined_cats,
                method="combined",
            )

        return pattern_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pattern_check(self, text: str) -> ToxicityResult:
        matched_categories: list[str] = []
        for cat, patterns in _COMPILED.items():
            for pat in patterns:
                if pat.search(text):
                    matched_categories.append(cat)
                    break

        score = min(1.0, len(matched_categories) * 0.4) if matched_categories else 0.0
        return ToxicityResult(
            score=round(score, 3),
            is_toxic=score >= self._threshold,
            categories=matched_categories,
            method="pattern",
        )

    async def _llm_check(
        self,
        text: str,
        provider: LLMProvider,
    ) -> ToxicityResult:
        try:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Is the following text toxic? Rate it 0.0 (clean) to 1.0 (highly toxic). "  # noqa: E501
                            "List any categories from: hate_speech, threat, sexual, self_harm, violence. "  # noqa: E501
                            'Respond as JSON: {"score": 0.0, "categories": []}.\n\n'
                            f"Text: {text[:300]}"
                        ),
                    )
                ],
                model="",
                max_tokens=80,
                temperature=0.0,
            )
            resp = await provider.complete(req)
            import json as _json

            data = _json.loads((resp.content or "{}").strip())
            score = float(data.get("score", 0.5))
            cats = [c for c in data.get("categories", []) if isinstance(c, str)]
            return ToxicityResult(
                score=round(min(1.0, max(0.0, score)), 3),
                is_toxic=score >= self._threshold,
                categories=cats,
                method="llm",
            )
        except Exception:
            return ToxicityResult(score=0.5, is_toxic=False, categories=[], method="llm")
