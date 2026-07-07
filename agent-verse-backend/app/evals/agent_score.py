"""AgentScorer — per-agent execution quality: tool success, grounding, citation."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState


class AgentScorer:
    def score_tool_success_rate(self, state: "AgentState") -> float:
        all_calls = []
        for step in state.steps:
            all_calls.extend(getattr(step, "tool_calls", None) or [])
        if not all_calls:
            return 0.7
        failed = sum(1 for tc in all_calls
                     if isinstance(tc, dict) and not tc.get("success", True))
        return round(max(0.0, 1.0 - failed / len(all_calls)), 3)

    def score_grounding(self, state: "AgentState") -> float:
        ungrounded = len(getattr(state, "ungrounded_claims", []) or [])
        if ungrounded == 0:
            return 1.0
        return round(max(0.0, 1.0 - ungrounded * 0.2), 3)

    def score_citation_quality(self, state: "AgentState") -> float:
        cited_answer = getattr(state, "cited_answer", "") or ""
        provenance = getattr(state, "provenance", []) or []
        if not cited_answer and not provenance:
            return 0.5
        if provenance:
            avg = sum(p.get("confidence", 0.5) if isinstance(p, dict) else 0.5
                      for p in provenance) / len(provenance)
            return round(avg, 3)
        has_citations = "[" in cited_answer and "]" in cited_answer
        return 0.8 if has_citations else 0.4
