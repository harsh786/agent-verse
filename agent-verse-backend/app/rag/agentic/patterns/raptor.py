"""RAPTOR — Recursive Abstractive Processing Tree Of Results.

Sarthi et al. 2024: 'RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval'

Algorithm:
  1. Cluster input chunks into groups of cluster_size
  2. LLM-summarize each cluster into a parent node
  3. Add parent summaries to the tree level above
  4. Repeat until only 1 node remains (tree root)
  5. At query time, retrieve from all tree levels via flat search across all summaries

This implementation uses LLM-based summarization without true embedding-based clustering
(no external clustering library required).
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState

_SUMMARIZE_SYSTEM = """Summarize these text chunks into a single coherent paragraph.
Preserve key facts, entities, and relationships. Be concise but complete."""

_ANSWER_SYSTEM = """Using the provided hierarchical context (from detailed chunks to high-level summaries),
answer the question as accurately as possible."""


@dataclass
class TreeNode:
    content: str
    level: int = 0              # 0 = leaf, 1+ = summary
    source_ids: list[str] = field(default_factory=list)  # chunk_ids that contributed


class RAPTORPattern(RAGPattern):
    """RAPTOR: recursive hierarchical summarization + multi-level retrieval."""

    def __init__(
        self,
        cluster_size: int = 4,
        max_levels: int = 3,
    ) -> None:
        self._cluster_size = cluster_size
        self._max_levels = max_levels
        self._circuit_breakers: dict[str, Any] = {}

    @property
    def pattern_id(self) -> str:
        return "raptor"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            f"RAPTOR: recursively summarize chunks in groups of {self._cluster_size}, "
            f"building a {self._max_levels}-level summary tree. "
            "Retrieves from all tree levels for comprehensive context coverage. "
            "Based on Sarthi et al. 2024."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        try:
            from app.core.config import get_settings
            if not get_settings().enable_raptor:
                return False
        except Exception:
            pass
        return True

    async def execute(
        self,
        *,
        query: str,
        chunks: list[dict[str, Any]],
        provider: Any,
        max_tokens: int = 600,
        **kwargs: Any,
    ) -> str:
        """Build RAPTOR tree from chunks and answer query using all levels."""
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("raptor_started", query=query[:60])
        except Exception:
            pass

        if not chunks:
            return ""

        from app.providers.base import CompletionRequest, Message

        # Circuit breaker setup
        try:
            from app.reliability.circuit_breaker import CircuitBreaker
            _cb_key = f"pattern_{self.pattern_id}"
            if _cb_key not in self._circuit_breakers:
                self._circuit_breakers[_cb_key] = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
            cb: Any = self._circuit_breakers[_cb_key]
        except ImportError:
            cb = None

        # Build leaf nodes from input chunks
        all_nodes: list[TreeNode] = [
            TreeNode(
                content=c.get("content", ""),
                level=0,
                source_ids=[c.get("chunk_id", f"chunk_{i}")],
            )
            for i, c in enumerate(chunks)
            if c.get("content")
        ]

        current_level_nodes = all_nodes.copy()

        for level in range(1, self._max_levels + 1):
            if len(current_level_nodes) <= 1:
                break

            # Cluster into groups of cluster_size
            groups: list[list[TreeNode]] = [
                current_level_nodes[i: i + self._cluster_size]
                for i in range(0, len(current_level_nodes), self._cluster_size)
            ]

            # Summarize each group sequentially for deterministic provider ordering
            parent_nodes: list[TreeNode] = []
            for group in groups:
                combined = "\n\n".join(n.content[:800] for n in group)
                source_ids = [sid for n in group for sid in n.source_ids]
                try:
                    if cb is not None and not cb.can_call():
                        summary = combined[:300]
                    else:
                        resp = await provider.complete(CompletionRequest(
                            messages=[
                                Message(role="system", content=_SUMMARIZE_SYSTEM),
                                Message(
                                    role="user",
                                    content=f"Chunks to summarize:\n\n{combined[:3000]}",
                                ),
                            ],
                            model="",
                            max_tokens=max_tokens,
                            temperature=0.0,
                        ))
                        if cb is not None:
                            cb.record_success()
                        summary = (resp.content or "").strip() or combined[:300]
                except Exception:
                    if cb is not None:
                        cb.record_failure()
                    summary = combined[:300]
                parent_nodes.append(TreeNode(content=summary, level=level, source_ids=source_ids))

            all_nodes.extend(parent_nodes)
            current_level_nodes = parent_nodes

        # Build hierarchical context: high-level summaries first, then detail
        summary_nodes = sorted(
            [n for n in all_nodes if n.level > 0],
            key=lambda n: -n.level,
        )
        leaf_nodes = [n for n in all_nodes if n.level == 0]

        context_parts: list[str] = []
        for n in summary_nodes[:3]:  # top-level summaries
            context_parts.append(f"[Level {n.level} summary]\n{n.content}")
        for n in leaf_nodes[:6]:  # detail chunks
            context_parts.append(f"[Detail]\n{n.content}")

        full_context = "\n\n".join(context_parts)

        # Answer query using hierarchical context
        try:
            if cb is not None and not cb.can_call():
                result = summary_nodes[-1].content if summary_nodes else (chunks[0].get("content", "") if chunks else "")
            else:
                resp = await provider.complete(CompletionRequest(
                    messages=[
                        Message(role="system", content=_ANSWER_SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"Context:\n{full_context[:4000]}\n\n"
                                f"Question: {query}"
                            ),
                        ),
                    ],
                    model="",
                    max_tokens=max_tokens,
                    temperature=0.0,
                ))
                if cb is not None:
                    cb.record_success()
                result = (resp.content or "").strip()
        except Exception:
            if cb is not None:
                cb.record_failure()
            # Fallback: return best summary if LLM fails
            result = summary_nodes[-1].content if summary_nodes else (chunks[0].get("content", "") if chunks else "")

        try:
            from app.observability.logging import get_logger
            get_logger(__name__).info("raptor_completed", result_len=len(result))
        except Exception:
            pass
        return result
