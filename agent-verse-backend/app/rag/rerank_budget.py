"""Rerank limits: the time budget, the candidate cap and the retrieval deadline.

Reranking is an optional refinement of an already-correct retrieval: it must
never be the reason a search misses its deadline. Every rerank (the async
default-path stage and the synchronous ``RerankPolicy`` path) takes its time
budget from :func:`rerank_budget_seconds`:

    min(RAG_RERANK_BUDGET_MS, time left before the retrieval deadline - reserve)

The retrieval deadline is published by the RAG gateway around each strategy
execution (:func:`retrieval_deadline`), so a rerank started late in a slow
retrieval gets only what is left, and none at all when nothing is.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

# Time kept back from the retrieval deadline for the work after the rerank
# (citations, answer assembly).
DEADLINE_RESERVE_SECONDS = 0.5

_DEFAULT_BUDGET_MS = 2500
_DEFAULT_MAX_QUEUE_DEPTH = 4
_DEFAULT_MAX_CANDIDATES = 30
_DEFAULT_MAX_LENGTH = 256
_DEFAULT_TORCH_INTEROP_THREADS = 1

_retrieval_deadline: ContextVar[float | None] = ContextVar(
    "rag_retrieval_deadline", default=None
)


@dataclass(frozen=True)
class RerankLimits:
    """The rerank settings, with the documented defaults for a partial settings object."""

    budget_seconds: float = _DEFAULT_BUDGET_MS / 1000
    max_queue_depth: int = _DEFAULT_MAX_QUEUE_DEPTH
    max_candidates: int = _DEFAULT_MAX_CANDIDATES
    max_length: int = _DEFAULT_MAX_LENGTH
    torch_threads: int = 0  # 0 = auto
    torch_interop_threads: int = _DEFAULT_TORCH_INTEROP_THREADS


def _int_setting(settings: Any, name: str, default: int) -> int:
    try:
        return max(0, int(getattr(settings, name, default)))
    except (TypeError, ValueError):
        return default


def rerank_limits(settings: Any = None) -> RerankLimits:
    """The rerank limits from ``settings`` (default: the process settings).

    Tolerates hand-built settings objects (tests use ``SimpleNamespace``): a
    missing attribute takes its documented default.
    """
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:  # pragma: no cover - settings always load in the app
            return RerankLimits()
    return RerankLimits(
        budget_seconds=_int_setting(settings, "rag_rerank_budget_ms", _DEFAULT_BUDGET_MS) / 1000,
        max_queue_depth=_int_setting(
            settings, "rag_rerank_max_queue_depth", _DEFAULT_MAX_QUEUE_DEPTH
        ),
        max_candidates=_int_setting(
            settings, "rag_rerank_max_candidates", _DEFAULT_MAX_CANDIDATES
        ),
        max_length=max(32, _int_setting(settings, "rag_rerank_max_length", _DEFAULT_MAX_LENGTH)),
        torch_threads=_int_setting(settings, "rag_rerank_torch_threads", 0),
        torch_interop_threads=_int_setting(
            settings, "rag_rerank_torch_interop_threads", _DEFAULT_TORCH_INTEROP_THREADS
        ),
    )


@contextmanager
def retrieval_deadline(seconds: float) -> Iterator[None]:
    """Publish the retrieval deadline (``seconds`` from now) to reranks run inside.

    Nested deadlines keep the earliest one.
    """
    deadline = time.monotonic() + max(seconds, 0.0)
    outer = _retrieval_deadline.get()
    token = _retrieval_deadline.set(deadline if outer is None else min(outer, deadline))
    try:
        yield
    finally:
        _retrieval_deadline.reset(token)


def seconds_until_retrieval_deadline() -> float | None:
    """Seconds left before the published retrieval deadline (None when none is set)."""
    deadline = _retrieval_deadline.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()


def rerank_budget_seconds(settings: Any = None) -> float:
    """Time a rerank may take from now: the budget, capped by the retrieval deadline."""
    budget = rerank_limits(settings).budget_seconds
    remaining = seconds_until_retrieval_deadline()
    if remaining is not None:
        budget = min(budget, remaining - DEADLINE_RESERVE_SECONDS)
    return max(budget, 0.0)


__all__ = [
    "DEADLINE_RESERVE_SECONDS",
    "RerankLimits",
    "rerank_budget_seconds",
    "rerank_limits",
    "retrieval_deadline",
    "seconds_until_retrieval_deadline",
]
