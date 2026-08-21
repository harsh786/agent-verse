from __future__ import annotations

from app.tool_runtime.tool_score import ToolScorer


class ToolRanker:
    def __init__(self, scorer: ToolScorer) -> None:
        self._scorer = scorer

    def rank(self, tool_names: list[str], goal_context: str = "") -> list[str]:
        scored = []
        for name in tool_names:
            profile = self._scorer.score(name)
            relevance = self._semantic_relevance(name, goal_context)
            combined = 0.6 * profile.trust_score + 0.4 * relevance
            scored.append((name, combined))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored]

    @staticmethod
    def _semantic_relevance(tool_name: str, context: str) -> float:
        if not context:
            return 0.5
        tokens = set(tool_name.lower().replace("_", " ").split())
        ctx_tokens = set(context.lower().split())
        overlap = len(tokens & ctx_tokens)
        return min(1.0, overlap / max(1, len(tokens)))
