"""Memory salience scoring and exponential decay.

SalienceScorer computes a relevance-weighted importance score for each
LongTermMemory entry so that recall can prioritise the most useful memories.

Score components
----------------
recency     : exponential decay of age since last access
relevance   : cosine-like TF-IDF overlap between query and memory content
access_freq : logarithmic boost for memories accessed many times
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "it",
        "in",
        "on",
        "at",
        "to",
        "of",
        "and",
        "or",
        "for",
        "with",
        "that",
        "this",
        "was",
        "are",
        "be",
        "by",
        "as",
        "from",
    }
)

# Half-life for recency decay in hours (memories lose half relevance every N hours)
_RECENCY_HALF_LIFE_HOURS: float = 168.0  # 7 days


def _tokenise(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"\b\w{3,}\b", text) if w.lower() not in _STOP_WORDS]


def _tf_idf_overlap(query_tokens: list[str], content_tokens: list[str]) -> float:
    if not query_tokens or not content_tokens:
        return 0.0
    q_set = set(query_tokens)
    c_counter = Counter(content_tokens)
    overlap = sum(c_counter[t] for t in q_set if t in c_counter)
    norm = math.sqrt(len(query_tokens) * len(content_tokens))
    return overlap / max(norm, 1.0)


class SalienceScorer:
    """Compute an importance score in [0, 1] for a memory entry.

    Higher scores surface memories that are:
    - Recently accessed (recency component)
    - Semantically relevant to the current query (relevance component)
    - Frequently recalled (frequency component)
    """

    def __init__(
        self,
        recency_weight: float = 0.4,
        relevance_weight: float = 0.45,
        frequency_weight: float = 0.15,
        half_life_hours: float = _RECENCY_HALF_LIFE_HOURS,
    ) -> None:
        self._rw = recency_weight
        self._rv = relevance_weight
        self._fw = frequency_weight
        self._half_life = half_life_hours

    def score(
        self,
        content: str,
        query: str,
        access_count: int = 1,
        last_accessed_at: datetime | None = None,
    ) -> float:
        """Return a salience score in [0, 1]."""
        recency = self._recency_score(last_accessed_at)
        relevance = self._relevance_score(query, content)
        frequency = self._frequency_score(access_count)
        raw = self._rw * recency + self._rv * relevance + self._fw * frequency
        return round(min(1.0, max(0.0, raw)), 4)

    def _recency_score(self, last_accessed_at: datetime | None) -> float:
        if last_accessed_at is None:
            return 0.5  # unknown access time → neutral
        now = datetime.now(UTC)
        hours_ago = (now - last_accessed_at).total_seconds() / 3600.0
        # Exponential decay: score = 0.5 ^ (hours / half_life)
        return math.pow(0.5, hours_ago / self._half_life)

    def _relevance_score(self, query: str, content: str) -> float:
        return _tf_idf_overlap(_tokenise(query), _tokenise(content))

    def _frequency_score(self, access_count: int) -> float:
        # log scale: 1 access → 0.0, 10 → 0.5, 100 → 1.0
        return min(1.0, math.log10(max(1, access_count)) / 2.0)

    def apply_decay(
        self,
        current_score: float,
        hours_since_last_use: float,
    ) -> float:
        """Reduce *current_score* by an exponential decay factor."""
        decay = math.pow(0.5, hours_since_last_use / self._half_life)
        return round(current_score * decay, 4)


def rank_memories(
    memories: list[dict],  # dicts with keys: content, access_count, last_accessed_at
    query: str,
    scorer: SalienceScorer | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Return *memories* sorted by salience score, highest first."""
    s = scorer or SalienceScorer()
    scored = []
    for mem in memories:
        sc = s.score(
            content=str(mem.get("content", "")),
            query=query,
            access_count=int(mem.get("access_count", 1)),
            last_accessed_at=mem.get("last_accessed_at"),
        )
        scored.append({**mem, "_salience": sc})
    scored.sort(key=lambda m: m["_salience"], reverse=True)
    return scored[:top_k]
