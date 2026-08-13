"""Tree of Thoughts (ToT) pattern — deliberate search over reasoning tree.

Based on Yao et al. 2023: 'Tree of Thoughts: Deliberate Problem Solving with LLMs'

Algorithm (BFS variant):
  1. Generate N thoughts (candidate next steps) via N parallel LLM calls
  2. Evaluate each thought: score 0-1, mark promising/pruned
  3. Keep top-K promising thoughts
  4. Expand best thought to next level
  5. Repeat for max_depth levels
  6. Return the thought path with highest cumulative score
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.reasoning_evidence import (
    ReasoningEvidence,
    ReasoningExecution,
    opaque_evidence_id,
)

_GENERATE_SYSTEM = (
    "Generate a distinct reasoning approach for the given problem. "
    "Provide one clear, concrete thought or approach as a single paragraph."
)

_EVALUATE_SYSTEM = (
    "Evaluate this reasoning thought for solving the given problem. "
    'Respond with JSON containing "score", "promising", and a brief "reason". '
    "Score 0.9+ for excellent systematic approaches. Score below 0.5 for vague or wrong directions."
)

_EXPAND_SYSTEM = (
    "Continue this reasoning chain to reach a final answer. "
    "Be specific and concrete. Provide the complete solution."
)


@dataclass
class ThoughtNode:
    content: str
    score: float = 0.5
    promising: bool = True
    reason: str = ""
    depth: int = 0
    children: list[ThoughtNode] = field(default_factory=list)

    def cumulative_score(self) -> float:
        return self.score


class TreeOfThoughtsPattern(AgentPattern):
    """Tree of Thoughts: BFS over reasoning space, return best path."""

    def __init__(
        self,
        n_thoughts: int = 3,
        max_depth: int = 2,
        beam_width: int = 2,
    ) -> None:
        self._n = n_thoughts
        self._max_depth = max_depth
        self._beam = beam_width
        self._last_evidence = ReasoningEvidence(
            strategy_id="tree_of_thoughts",
            status="degraded",
            call_count=0,
            safe_rationale_summary="not executed",
        )

    @property
    def pattern_id(self) -> str:
        return "tree_of_thoughts"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            f"Tree of Thoughts: generate {self._n} thoughts per level, "
            f"evaluate+prune to top-{self._beam}, expand for {self._max_depth} levels. "
            "Best for complex multi-step reasoning problems."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings
            if not get_settings().enable_tree_of_thoughts:
                return False
        except Exception:
            pass
        complexity = getattr(goal_properties, "complexity", None)
        if complexity is not None:
            return str(complexity).lower() in ("complex", "expert", "moderate")
        return True

    async def execute(
        self,
        *,
        problem: str,
        provider: Any,
        max_tokens: int = 500,
        **kwargs: Any,
    ) -> str:
        """Run Tree of Thoughts BFS on `problem`. Returns best reasoning chain + answer."""
        # Generate initial thoughts via N parallel calls
        thoughts = await self._generate_thoughts(problem, provider, max_tokens)
        if not thoughts:
            self._last_evidence = ReasoningEvidence(
                strategy_id=self.pattern_id,
                status="degraded",
                call_count=self._n + 1,
                invalid_samples=self._n,
                limit_reason="no_valid_candidates",
                safe_rationale_summary="direct answer fallback after empty frontier",
            )
            return await self._direct_answer(problem, provider, max_tokens)

        # Evaluate and prune in parallel
        evaluated = await self._evaluate_thoughts(problem, thoughts, provider)
        promising = sorted(
            [t for t in evaluated if t.promising],
            key=lambda t: t.score,
            reverse=True,
        )[: self._beam]

        if not promising:
            promising = sorted(evaluated, key=lambda t: t.score, reverse=True)[:1]

        # Expand best thought(s) up to max_depth
        best_thought = promising[0] if promising else (evaluated[0] if evaluated else None)
        if best_thought is None:
            self._last_evidence = ReasoningEvidence(
                strategy_id=self.pattern_id,
                status="degraded",
                call_count=self._n + len(thoughts) + 1,
                valid_samples=len(thoughts),
                limit_reason="empty_frontier",
                safe_rationale_summary="direct answer fallback after pruning",
            )
            return await self._direct_answer(problem, provider, max_tokens)

        for _ in range(self._max_depth - 1):
            expanded = await self._expand_thought(
                problem, best_thought.content, provider, max_tokens
            )
            if expanded:
                best_thought = ThoughtNode(
                    content=expanded, score=0.9, depth=best_thought.depth + 1
                )

        selected = tuple(opaque_evidence_id(item.content) for item in promising)
        selected_set = set(selected)
        pruned = tuple(
            opaque_evidence_id(item.content)
            for item in evaluated
            if opaque_evidence_id(item.content) not in selected_set
        )
        call_count = self._n + len(thoughts) + self._max_depth
        self._last_evidence = ReasoningEvidence(
            strategy_id=self.pattern_id,
            status="completed",
            call_count=call_count,
            valid_samples=len(thoughts),
            invalid_samples=self._n - len(thoughts),
            selected_ids=selected,
            pruned_ids=pruned,
            scores=tuple(item.score for item in promising),
            checkpoint_cursor={
                "depth": self._max_depth,
                "frontier_size": len(promising),
                "nodes": len(evaluated),
            },
            safe_rationale_summary="bounded beam search selected highest scored candidates",
        )

        # Final answer is separate from private frontier evidence.
        return await self._expand_thought(
            problem, best_thought.content, provider, max_tokens
        )

    async def execute_with_evidence(self, **kwargs: Any) -> ReasoningExecution:
        result = await self.execute(**kwargs)
        return ReasoningExecution(result=result, evidence=self._last_evidence)

    async def _generate_thoughts(
        self, problem: str, provider: Any, max_tokens: int
    ) -> list[ThoughtNode]:
        """Generate N independent thoughts via N parallel LLM calls."""
        from app.providers.base import CompletionRequest, Message

        async def _one_thought(index: int) -> ThoughtNode | None:
            try:
                resp = await provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(role="system", content=_GENERATE_SYSTEM),
                            Message(
                                role="user",
                                content=(
                                    f"Problem: {problem[:500]}\n"
                                    f"Generate reasoning thought #{index + 1}:"
                                ),
                            ),
                        ],
                        model="",
                        max_tokens=max_tokens,
                        temperature=0.8,
                    )
                )
                content = (resp.content or "").strip()
                if content:
                    return ThoughtNode(content=content)
            except Exception:
                pass
            return None

        results = await asyncio.gather(*[_one_thought(i) for i in range(self._n)])
        return [t for t in results if t is not None]

    async def _evaluate_thoughts(
        self, problem: str, thoughts: list[ThoughtNode], provider: Any
    ) -> list[ThoughtNode]:
        from app.providers.base import CompletionRequest, Message

        async def _eval_one(thought: ThoughtNode) -> ThoughtNode:
            try:
                resp = await provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(role="system", content=_EVALUATE_SYSTEM),
                            Message(
                                role="user",
                                content=(
                                    f"Problem: {problem[:300]}\n"
                                    f"Thought: {thought.content[:300]}"
                                ),
                            ),
                        ],
                        model="",
                        max_tokens=150,
                        temperature=0.0,
                        response_schema={
                            "type": "object",
                            "properties": {
                                "score": {"type": "number"},
                                "promising": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                        },
                    )
                )
                raw = (resp.content or "").strip()
                try:
                    d = json.loads(raw)
                    thought.score = max(0.0, min(1.0, float(d.get("score", 0.5))))
                    thought.promising = bool(
                        d.get("promising", thought.score >= 0.6)
                    )
                    thought.reason = str(d.get("reason", ""))
                except Exception:
                    pass
            except Exception:
                pass
            return thought

        return list(await asyncio.gather(*[_eval_one(t) for t in thoughts]))

    async def _expand_thought(
        self, problem: str, thought: str, provider: Any, max_tokens: int
    ) -> str:
        from app.providers.base import CompletionRequest, Message

        try:
            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_EXPAND_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Problem: {problem[:300]}\n"
                                f"Reasoning so far: {thought[:500]}\n"
                                "Continue to final answer:"
                            ),
                        ),
                    ],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
            )
            return (resp.content or "").strip()
        except Exception:
            return thought

    async def _direct_answer(self, problem: str, provider: Any, max_tokens: int) -> str:
        from app.providers.base import CompletionRequest, Message

        try:
            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=f"Solve: {problem[:500]}")],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            )
            return (resp.content or "").strip()
        except Exception:
            return ""
