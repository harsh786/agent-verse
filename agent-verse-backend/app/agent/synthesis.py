"""
Citation-Carrying Answer Synthesis
====================================
After a goal succeeds, synthesize a final answer that cites the specific
steps and tool outputs that support each factual claim.

Runs in a new `_node_synthesize` graph node added after success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Citation:
    """A single citation linking a claim to its evidence source."""

    text: str  # the cited text snippet (≤200 chars)
    source: str  # "step_N_tool_name" or "step_N"
    step_index: int
    tool_name: str = ""
    confidence: float = 1.0


@dataclass
class CitedAnswer:
    """Final synthesized answer with citations."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    grounding_score: float = 1.0  # 0-1, proportion of claims grounded


class AnswerSynthesizer:
    """
    Synthesizes a citation-carrying final answer from completed agent steps.
    """

    def __init__(
        self,
        llm_provider: Any | None = None,
        max_output_tokens: int = 2000,
        *,
        enforce_citation_gate: bool = True,
    ) -> None:
        self._llm = llm_provider
        self._max_output_tokens = max_output_tokens
        self._enforce_gate = enforce_citation_gate

    def _apply_citation_gate(self, answer: CitedAnswer, steps: list[Any]) -> CitedAnswer:
        """T6: strip claims not supported by a cited step (anti-hallucination gate).

        Best-effort: if gating would empty the answer, keep the original rather
        than return nothing. Adjusts grounding_score to the kept/total ratio.
        """
        if not self._enforce_gate or not answer.answer.strip():
            return answer
        try:
            from app.agent.citation_gate import enforce_citations

            step_outputs = {
                i + 1: str(getattr(s, "output", "") or "") for i, s in enumerate(steps)
            }
            gated = enforce_citations(answer.answer, step_outputs)
            if gated.ok:
                return answer
            logger.info(
                "citation_gate_stripped",
                dropped=gated.dropped_sentences,
                violations=len(gated.violations),
            )
            if not gated.gated_answer.strip():
                return answer  # avoid an empty answer; leave original + low score
            total = gated.kept_sentences + gated.dropped_sentences
            score = (gated.kept_sentences / total) if total else answer.grounding_score
            return CitedAnswer(
                answer=gated.gated_answer,
                citations=answer.citations,
                grounding_score=round(score, 4),
            )
        except Exception as exc:
            logger.debug("citation_gate_failed", error=str(exc)[:60])
            return answer

    async def synthesize(
        self,
        goal: str,
        steps: list[Any],
        *,
        tenant_id: str = "",
    ) -> CitedAnswer:
        """
        Synthesize a final cited answer from the completed steps.
        If LLM provider is available, use it for synthesis.
        Falls back to a deterministic step summary.
        """
        if not steps:
            return CitedAnswer(answer="No steps were executed.", citations=[])

        # Build provenance from steps
        provenance: list[dict[str, Any]] = []
        for i, step in enumerate(steps):
            tool_name = ""
            output = ""
            if hasattr(step, "output") and step.output:
                output = str(step.output)[:500]
            if hasattr(step, "tool_calls") and step.tool_calls:
                first_tc = step.tool_calls[0]
                tool_name = getattr(first_tc, "tool_name", "")
                if not tool_name and isinstance(first_tc, dict):
                    tool_name = first_tc.get("tool_name", "")
            provenance.append(
                {
                    "step_index": i,
                    "step_description": getattr(
                        step, "step", getattr(step, "description", f"Step {i + 1}")
                    ),
                    "tool_name": tool_name,
                    "output_excerpt": output[:200],
                }
            )

        result: CitedAnswer | None = None
        if self._llm is not None:
            try:
                result = await self._synthesize_with_llm(goal, provenance, steps)
            except Exception as exc:
                logger.warning("synthesis_llm_failed", error=str(exc)[:60])
        if result is None:
            result = self._synthesize_deterministic(goal, provenance, steps)
        return self._apply_citation_gate(result, steps)

    async def _synthesize_with_llm(
        self, goal: str, provenance: list[dict[str, Any]], steps: list[Any]
    ) -> CitedAnswer:
        import re

        from app.agent.prompts import SYNTHESIS_SYSTEM
        from app.providers.base import CompletionRequest, Message

        step_summaries = "\n".join(
            f"Step {p['step_index'] + 1} [{p['tool_name'] or 'llm'}]: {p['output_excerpt']}"
            for p in provenance
        )

        req = CompletionRequest(
            messages=[
                Message(role="system", content=SYNTHESIS_SYSTEM),
                Message(
                    role="user",
                    content=(
                        f"Goal: {goal}\n\n"
                        f"Completed steps with outputs:\n{step_summaries}\n\n"
                        "Synthesize a cited final answer. For each factual claim, "
                        "cite the step number that produced it like [Step N]."
                    ),
                ),
            ],
            model="",
        )
        llm = self._llm
        assert llm is not None
        resp = await llm.complete(req)

        # Extract citations from [Step N] patterns
        citations = []
        for m in re.finditer(r"\[Step (\d+)\]", resp.content):
            step_idx = int(m.group(1)) - 1
            if 0 <= step_idx < len(provenance):
                p = provenance[step_idx]
                citations.append(
                    Citation(
                        text=p["output_excerpt"][:150],
                        source=f"step_{step_idx + 1}_{p['tool_name'] or 'llm'}",
                        step_index=step_idx,
                        tool_name=p["tool_name"],
                    )
                )

        return CitedAnswer(
            answer=resp.content,
            citations=citations,
            grounding_score=len(citations) / max(len(provenance), 1),
        )

    def _synthesize_deterministic(
        self, goal: str, provenance: list[dict[str, Any]], steps: list[Any]
    ) -> CitedAnswer:
        """Deterministic synthesis without LLM."""
        parts = [f"Completed goal: {goal}\n"]
        citations = []

        for p in provenance:
            step_num = p["step_index"] + 1
            tool = p["tool_name"] or "reasoning"
            output = p["output_excerpt"]
            if output:
                parts.append(f"Step {step_num} [{tool}]: {output}")
                citations.append(
                    Citation(
                        text=output[:150],
                        source=f"step_{step_num}_{tool}",
                        step_index=p["step_index"],
                        tool_name=p["tool_name"],
                    )
                )

        return CitedAnswer(
            answer="\n".join(parts),
            citations=citations,
            grounding_score=1.0,
        )
