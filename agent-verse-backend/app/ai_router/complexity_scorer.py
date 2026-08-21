"""Query Complexity Scorer — route queries to appropriate model tiers.

Features used for scoring:
- Word count
- Clause count (commas, semicolons, conjunctions)
- Technical term density
- Multi-part question indicator
- Negation count
- Length relative to a baseline
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ComplexityLevel = Literal["simple", "moderate", "complex"]

_TECH_TERMS = re.compile(
    r"\b(algorithm|implement|architecture|optimize|concurren|asynch|distrib|"
    r"latency|throughput|scalab|kubernetes|microservic|neural|transformer|"
    r"vector|embedding|retrieval|gradient|backprop|inference|quantiz)\w*\b",
    re.IGNORECASE,
)
_MULTI_PART = re.compile(
    r"\b(and also|furthermore|moreover|in addition|as well as|"
    r"not only.*but also|on the other hand|however|nevertheless)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(not|never|neither|nor|without|except|unless)\b", re.IGNORECASE)
_CLAUSE_SEP = re.compile(r"[,;:]")


@dataclass
class ComplexityScore:
    level: ComplexityLevel
    score: float  # 0.0 = trivial, 1.0 = maximum complexity
    features: dict[str, float] = field(default_factory=dict)

    def recommended_model_tier(self) -> str:
        """Return a tier name for model routing."""
        return {"simple": "small", "moderate": "medium", "complex": "large"}[self.level]


class QueryComplexityScorer:
    """Score query complexity using lightweight lexical features.

    Thresholds
    ----------
    score < 0.35  → simple   (use smallest / cheapest model)
    score < 0.65  → moderate (use mid-tier model)
    score >= 0.65 → complex  (use largest / most capable model)
    """

    def __init__(
        self,
        simple_threshold: float = 0.35,
        complex_threshold: float = 0.65,
    ) -> None:
        self._simple = simple_threshold
        self._complex = complex_threshold

    def score(self, query: str, context: dict | None = None) -> ComplexityScore:
        """Score *query* and return a :class:`ComplexityScore`."""
        words = query.split()
        word_count = len(words)

        # Feature extraction
        f_length = min(1.0, word_count / 40.0)  # normalised length
        f_clauses = min(1.0, len(_CLAUSE_SEP.findall(query)) / 5.0)
        f_tech = min(1.0, len(_TECH_TERMS.findall(query)) / 3.0)
        f_multi_part = 1.0 if _MULTI_PART.search(query) else 0.0
        f_negation = min(1.0, len(_NEGATION.findall(query)) / 3.0)
        f_questions = min(1.0, query.count("?") / 2.0)

        # Weighted combination
        score = (
            0.20 * f_length
            + 0.15 * f_clauses
            + 0.30 * f_tech
            + 0.15 * f_multi_part
            + 0.10 * f_negation
            + 0.10 * f_questions
        )
        score = round(min(1.0, max(0.0, score)), 4)

        if score < self._simple:
            level: ComplexityLevel = "simple"
        elif score < self._complex:
            level = "moderate"
        else:
            level = "complex"

        return ComplexityScore(
            level=level,
            score=score,
            features={
                "length": round(f_length, 3),
                "clauses": round(f_clauses, 3),
                "technical": round(f_tech, 3),
                "multi_part": f_multi_part,
                "negation": round(f_negation, 3),
                "questions": round(f_questions, 3),
            },
        )
