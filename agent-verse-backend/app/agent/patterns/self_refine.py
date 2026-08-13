"""Self-Refine pattern — iterative output improvement via LLM critique.

Graph: _node_execute → _node_refine → _node_verify
The _node_refine is already implemented in graph.py.
This adapter provides standalone execute() for testing and composition.
"""
from __future__ import annotations

from typing import Any

from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.reasoning_evidence import ReasoningEvidence, ReasoningExecution

_SELF_REFINE_SYSTEM = """You are a quality reviewer. Given a task and an output, either:
1. Return the improved output (better quality, more complete, more accurate)
2. Return exactly "NO_CHANGES_NEEDED" if the output is already excellent

Be specific and concrete. Do not add excessive explanations."""


class SelfRefinePattern(AgentPattern):
    """Iterative self-improvement: generate → critique → refine, up to max_iterations."""

    def __init__(self, max_iterations: int = 2) -> None:
        self._max_iterations = max_iterations

    @property
    def pattern_id(self) -> str:
        return "self_refine"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Self-Refine: iteratively improve output by asking the LLM to critique "
            "and refine its own output, up to max_iterations times."
        )

    @property
    def node_name(self) -> str:
        return "_node_refine"

    def is_compatible(self, goal_properties: Any) -> bool:
        # Most useful for writing/analysis tasks, not pure data retrieval
        return True

    async def execute(
        self,
        *,
        last_output: str,
        task: str,
        provider: Any,
        current_iteration: int = 0,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        """Refine `last_output` for `task`. Returns improved text or original if at max."""
        if current_iteration >= self._max_iterations:
            return last_output

        try:
            from app.providers.base import CompletionRequest, Message

            prompt = (
                f"Task: {task}\n\n"
                f"Current output:\n{last_output[:2000]}\n\n"
                "Improve this output. If it is already perfect, respond with exactly: "
                "NO_CHANGES_NEEDED"
            )
            resp = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=_SELF_REFINE_SYSTEM),
                        Message(role="user", content=prompt),
                    ],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            )
            refined = (resp.content or "").strip()
            if refined and not refined.startswith("NO_CHANGES_NEEDED"):
                return refined
        except Exception:
            pass
        return last_output

    async def execute_with_evidence(
        self,
        *,
        last_output: str,
        task: str,
        provider: Any,
        current_iteration: int = 0,
        max_tokens: int = 2000,
        round_limit: int | None = None,
        **kwargs: Any,
    ) -> ReasoningExecution:
        effective_limit = self._max_iterations
        if round_limit is not None:
            effective_limit = min(effective_limit, round_limit)
        if current_iteration >= effective_limit:
            return ReasoningExecution(
                result=last_output,
                evidence=ReasoningEvidence(
                    strategy_id=self.pattern_id,
                    status="exhausted",
                    call_count=0,
                    limit_reason="round_limit",
                    checkpoint_cursor={"round": current_iteration},
                    safe_rationale_summary="refinement round limit reached",
                ),
            )
        result = await self.execute(
            last_output=last_output,
            task=task,
            provider=provider,
            current_iteration=current_iteration,
            max_tokens=max_tokens,
            **kwargs,
        )
        return ReasoningExecution(
            result=result,
            evidence=ReasoningEvidence(
                strategy_id=self.pattern_id,
                status="completed",
                call_count=1,
                checkpoint_cursor={"round": current_iteration + 1},
                safe_rationale_summary=(
                    "output refined" if result != last_output else "no change required"
                ),
            ),
        )
