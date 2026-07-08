"""Agentic Chunking pattern — LLM-driven proposition extraction as atomic chunks.

Instead of splitting documents by character/token count, this pattern uses an LLM
to extract discrete, self-contained propositions from each chunk. Each proposition
becomes an independently searchable unit with higher precision.

Based on: Chen et al. 2023 'Dense X Retrieval'
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

_PROPOSITION_SYSTEM = """Extract self-contained factual propositions from this text.
Each proposition must:
- Be a complete, standalone sentence
- Contain exactly one fact or claim
- Need no external context to be understood

Respond with one proposition per line. No numbering, no bullets."""


class AgenticChunkingPattern(RAGPattern):
    """Agentic Chunking: LLM-driven proposition extraction (Dense X Retrieval)."""

    def __init__(self, max_propositions: int = 10) -> None:
        self._max_props = max_propositions

    @property
    def pattern_id(self) -> str:
        return "agentic_chunking"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Agentic Chunking: use an LLM to extract atomic propositions from each chunk "
            "rather than splitting by character count. Each proposition is a standalone "
            "searchable unit with higher precision retrieval (Chen et al. 2023)."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        # Best for knowledge-intensive goals requiring high precision
        return True

    async def extract_propositions(
        self,
        chunk_content: str,
        provider: Any,
        max_tokens: int = 400,
    ) -> list[str]:
        """Extract atomic propositions from a single chunk."""
        try:
            from app.providers.base import CompletionRequest, Message
            resp = await provider.complete(CompletionRequest(
                messages=[
                    Message(role="system", content=_PROPOSITION_SYSTEM),
                    Message(role="user", content=chunk_content[:2000]),
                ],
                model="",
                max_tokens=max_tokens,
                temperature=0.0,
            ))
            raw = (resp.content or "").strip()
            props = [
                line.strip()
                for line in raw.split("\n")
                if line.strip() and len(line.strip()) > 10
            ]
            return props[: self._max_props]
        except Exception:
            # Fallback: split into sentences
            import re
            sentences = re.split(r'[.!?]', chunk_content)
            return [s.strip() for s in sentences if len(s.strip()) > 15][: self._max_props]

    async def execute(
        self,
        *,
        chunks: list[dict[str, Any]],
        provider: Any,
        query: str = "",
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Extract propositions from all chunks. Returns proposition-level chunk dicts."""
        if not chunks or provider is None:
            return chunks

        async def _process_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
            content = chunk.get("content", "")
            if not content:
                return [chunk]
            propositions = await self.extract_propositions(content, provider)
            if not propositions:
                return [chunk]
            return [
                {
                    **{k: v for k, v in chunk.items() if k != "content"},
                    "content": prop,
                    "chunk_id": f"{chunk.get('chunk_id', 'c0')}_prop_{i}",
                    "source_chunk_id": chunk.get("chunk_id"),
                    "proposition_index": i,
                }
                for i, prop in enumerate(propositions)
            ]

        all_prop_lists = await asyncio.gather(
            *[_process_chunk(c) for c in chunks]
        )
        all_props = [prop for prop_list in all_prop_lists for prop in prop_list]
        return all_props[:top_k * self._max_props]
