"""Memory consolidation — compress large episodic memory sets into concise summaries.

Prevents unbounded memory growth for long-running agents. Clusters memories
by keyword overlap and summarises clusters that exceed a size threshold.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

_CLUSTER_THRESHOLD = 3  # consolidate clusters with more than N items
_MIN_CONTENT_LENGTH = 20


def _keywords(text: str) -> frozenset[str]:
    words = re.findall(r"\b\w{4,}\b", text.lower())
    stop = {"that", "with", "this", "have", "from", "they", "been", "were", "when", "then"}
    return frozenset(w for w in words if w not in stop)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ConsolidationResult:
    original_count: int
    consolidated_count: int
    memories: list[dict[str, Any]]
    clusters_merged: int


class MemoryConsolidator:
    """Cluster episodic memories by keyword similarity and summarise large clusters."""

    def __init__(
        self,
        cluster_threshold: int = _CLUSTER_THRESHOLD,
        similarity_cutoff: float = 0.25,
    ) -> None:
        self._threshold = cluster_threshold
        self._cutoff = similarity_cutoff

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consolidate_sync(
        self,
        memories: list[dict[str, Any]],
    ) -> ConsolidationResult:
        """Consolidate without an LLM — join cluster items into a single entry."""
        clusters = self._cluster(memories)
        result: list[dict[str, Any]] = []
        clusters_merged = 0
        for cluster in clusters:
            if len(cluster) >= self._threshold:
                merged = self._merge_cluster(cluster)
                result.append(merged)
                clusters_merged += 1
            else:
                result.extend(cluster)
        return ConsolidationResult(
            original_count=len(memories),
            consolidated_count=len(result),
            memories=result,
            clusters_merged=clusters_merged,
        )

    async def consolidate(
        self,
        memories: list[dict[str, Any]],
        provider: LLMProvider | None = None,
    ) -> ConsolidationResult:
        """Consolidate clusters; use LLM for summaries when provider is available."""
        clusters = self._cluster(memories)
        result: list[dict[str, Any]] = []
        clusters_merged = 0
        for cluster in clusters:
            if len(cluster) >= self._threshold:
                if provider is not None:
                    try:
                        merged = await self._summarise_with_llm(cluster, provider)
                    except Exception:
                        merged = self._merge_cluster(cluster)
                else:
                    merged = self._merge_cluster(cluster)
                result.append(merged)
                clusters_merged += 1
            else:
                result.extend(cluster)
        return ConsolidationResult(
            original_count=len(memories),
            consolidated_count=len(result),
            memories=result,
            clusters_merged=clusters_merged,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cluster(self, memories: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Greedy keyword-similarity clustering."""
        if not memories:
            return []
        keyword_sets = [_keywords(str(m.get("content", ""))) for m in memories]
        assigned = [-1] * len(memories)
        cluster_id = 0
        for i in range(len(memories)):
            if assigned[i] != -1:
                continue
            assigned[i] = cluster_id
            for j in range(i + 1, len(memories)):
                if assigned[j] != -1:
                    continue
                if _jaccard(keyword_sets[i], keyword_sets[j]) >= self._cutoff:
                    assigned[j] = cluster_id
            cluster_id += 1
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for idx, cid in enumerate(assigned):
            groups[cid].append(memories[idx])
        return list(groups.values())

    def _merge_cluster(self, cluster: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge cluster items into one consolidated memory via simple join."""
        combined = " | ".join(str(m.get("content", ""))[:100] for m in cluster)
        base = cluster[0].copy()
        base["content"] = f"[Consolidated {len(cluster)} memories] {combined}"
        base["access_count"] = sum(m.get("access_count", 1) for m in cluster)
        return base

    async def _summarise_with_llm(
        self,
        cluster: list[dict[str, Any]],
        provider: LLMProvider,
    ) -> dict[str, Any]:
        from app.providers.base import CompletionRequest, Message

        contents = "\n".join(f"- {m.get('content', '')[:150]}" for m in cluster)
        prompt = (
            "Summarise the following memory entries into one concise sentence "
            f"(max 150 chars):\n{contents}\n\nSummary:"
        )
        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model="",
            max_tokens=80,
            temperature=0.0,
        )
        resp = await provider.complete(req)
        summary = (resp.content or "").strip()[:200]
        base = cluster[0].copy()
        base["content"] = f"[Summary of {len(cluster)}] {summary}"
        base["access_count"] = sum(m.get("access_count", 1) for m in cluster)
        return base
