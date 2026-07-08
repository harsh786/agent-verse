"""Self-Consistency pattern — sample N reasoning paths, return majority-vote answer.

Based on Wang et al. 2022: 'Self-Consistency Improves Chain of Thought Reasoning'
Steps:
  1. Run the same prompt N times with temperature > 0
  2. Collect all responses
  3. Return the response that appears most often (or best-representative cluster)
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from app.agent.patterns.base import AgentPattern, PatternState


def _normalize(text: str) -> str:
    """Normalize response for comparison (lowercase, strip, first 200 chars)."""
    return text.lower().strip()[:200]


def _most_common(responses: list[str]) -> str:
    """Return most common response using normalized comparison."""
    if not responses:
        return ""
    if len(responses) == 1:
        return responses[0]
    normalized = [_normalize(r) for r in responses]
    counts = Counter(normalized)
    best_norm = counts.most_common(1)[0][0]
    # Return the original (un-normalized) version of the most common
    for r in responses:
        if _normalize(r) == best_norm:
            return r
    return responses[0]


class SelfConsistencyPattern(AgentPattern):
    """Sample N completions and return the majority-vote answer."""

    def __init__(self, n_samples: int = 3, temperature: float = 0.7) -> None:
        self._n = n_samples
        self._temperature = temperature

    @property
    def pattern_id(self) -> str:
        return "self_consistency"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            f"Self-Consistency: sample {self._n} reasoning paths at temperature "
            f"{self._temperature}, return majority-vote answer. "
            "Improves accuracy on complex reasoning tasks."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        # Most useful for reasoning/analytical goals, not latency-critical tasks
        return True

    async def execute(
        self,
        *,
        prompt: str,
        provider: Any,
        system_prompt: str = "You are a helpful assistant. Think step by step.",
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> str:
        """Run prompt N times, return majority-vote answer."""
        from app.providers.base import CompletionRequest, Message

        async def _one_sample() -> str:
            try:
                resp = await provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(role="system", content=system_prompt),
                            Message(role="user", content=prompt),
                        ],
                        model="",
                        max_tokens=max_tokens,
                        temperature=self._temperature,
                    )
                )
                return (resp.content or "").strip()
            except Exception:
                return ""

        # Run all N samples in parallel
        responses = await asyncio.gather(*[_one_sample() for _ in range(self._n)])
        valid = [r for r in responses if r]
        if not valid:
            return ""
        return _most_common(valid)

    def vote(self, responses: list[str]) -> str:
        """Public method to vote on externally-collected responses."""
        return _most_common(responses)
