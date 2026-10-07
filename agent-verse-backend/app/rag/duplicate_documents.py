"""Never serve the same document passage twice from one knowledge collection.

A collection fed by two Sources that read the same upstream data (registered
before ``POST /sources`` refused duplicates, see
``app.ingestion.source_identity``) holds every document twice: two chunk rows
per passage, the same ``source_url``, different ``source_id``. Search then
returned the same postmortem twice and pushed other evidence out of top-k.

Retrieval collapses such hits by canonical document identity — the canonical
``source_url`` (scheme/host case, host order, default port, user-info and
trailing slash normalised) plus the passage text (whitespace-normalised hash) —
keeping the best-ranked hit. Different passages of one document are different
identities and are all kept; hits without a ``source_url`` are never collapsed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.ingestion.source_identity import canonical_source_url

__all__ = ["collapse_duplicate_documents", "document_identity"]


def document_identity(metadata: Mapping[str, Any] | None, content: str) -> tuple[str, str] | None:
    """``(canonical source_url, passage hash)`` or ``None`` when the hit names no URL."""
    url = canonical_source_url((metadata or {}).get("source_url"))
    if not url:
        return None
    passage = " ".join(str(content or "").split())
    return url, hashlib.sha256(passage.encode()).hexdigest()


def collapse_duplicate_documents[T](
    items: Iterable[T], identity: Callable[[T], tuple[str, str] | None]
) -> tuple[list[T], int]:
    """Keep the first (best-ranked) item per document identity, preserving order.

    Returns ``(kept, dropped_count)``. Callers pass items best-first.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[T] = []
    dropped = 0
    for item in items:
        key = identity(item)
        if key is not None:
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
        kept.append(item)
    return kept, dropped
