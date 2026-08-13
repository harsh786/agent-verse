"""LLMQueryTransformer — rewrites/expands queries using an LLM for better RAG retrieval.

Strategies
----------
step_back   : Generate a more abstract question to retrieve broader context.
decompose   : Break the query into 2-4 simpler sub-questions.
rewrite     : Fix ambiguities and improve clarity while preserving intent.
transform   : Apply all three strategies and deduplicate the output set.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

TransformStrategy = Literal["step_back", "decompose", "rewrite", "transform"]

_STEP_BACK_PROMPT = (
    "You are a query rewriter for a retrieval-augmented system. "
    "Given the user question below, produce ONE broader, more general question that "
    "would help retrieve the background knowledge needed to answer it. "
    "Return ONLY the rewritten question, no explanation.\n\nUser question: {query}"
)

_DECOMPOSE_PROMPT = (
    "You are a query decomposer for a retrieval-augmented system. "
    "Break the following question into 2–4 simpler, independent sub-questions. "
    "Return ONLY the sub-questions, one per line, no numbering or bullet points.\n\n"
    "Question: {query}"
)

_REWRITE_PROMPT = (
    "You are a query rewriter for a retrieval-augmented system. "
    "Rewrite the question below to be clearer and more specific, "
    "fixing any ambiguities while preserving the original intent. "
    "Return ONLY the rewritten question.\n\nQuestion: {query}"
)


class LLMQueryTransformer:
    """Transforms queries using an LLM to improve retrieval recall and precision."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call(self, prompt: str) -> str:
        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model="",  # provider resolves the model; empty string selects the default
            max_tokens=256,
            temperature=0.0,
        )
        resp = await self._provider.complete(req)
        return (resp.content or "").strip()

    def _parse_lines(self, text: str) -> list[str]:
        """Extract non-empty, non-boilerplate lines from LLM output."""
        lines = [l.strip() for l in text.splitlines()]
        cleaned: list[str] = []
        for line in lines:
            # Strip leading list markers: "1.", "-", "*", "–"
            line = re.sub(r"^[\d]+\.\s*|^[-*–]\s*", "", line).strip()
            if line and len(line) > 5:
                cleaned.append(line)
        return cleaned

    # ------------------------------------------------------------------
    # Public strategy methods
    # ------------------------------------------------------------------

    async def step_back(self, query: str) -> list[str]:
        """Return [original_query, step_back_query]."""
        try:
            stepped = await self._call(_STEP_BACK_PROMPT.format(query=query))
            if stepped and stepped.lower() != query.lower():
                return [query, stepped]
        except Exception:
            pass
        return [query]

    async def decompose(self, query: str) -> list[str]:
        """Return [original_query, sub_q1, sub_q2, …] (de-duplicated)."""
        try:
            raw = await self._call(_DECOMPOSE_PROMPT.format(query=query))
            sub_questions = self._parse_lines(raw)
            seen = {query.lower()}
            result = [query]
            for q in sub_questions:
                if q.lower() not in seen:
                    seen.add(q.lower())
                    result.append(q)
            return result
        except Exception:
            return [query]

    async def rewrite(self, query: str) -> list[str]:
        """Return [rewritten_query] or [original] on failure."""
        try:
            rewritten = await self._call(_REWRITE_PROMPT.format(query=query))
            if rewritten and rewritten.lower() != query.lower():
                return [rewritten]
        except Exception:
            pass
        return [query]

    async def transform(self, query: str) -> list[str]:
        """Apply all strategies and return a de-duplicated union of queries."""
        queries: list[str] = []
        seen: set[str] = set()

        async def _add(qs: list[str]) -> None:
            for q in qs:
                key = q.lower().strip()
                if key not in seen:
                    seen.add(key)
                    queries.append(q)

        await _add([query])
        try:
            await _add(await self.step_back(query))
        except Exception:
            pass
        try:
            await _add(await self.decompose(query))
        except Exception:
            pass
        try:
            await _add(await self.rewrite(query))
        except Exception:
            pass

        return queries or [query]
