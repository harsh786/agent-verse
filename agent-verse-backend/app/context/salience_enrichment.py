"""Enrich context chunks with a salience multiplier (Phase 4, T4.3).

Wires :class:`~app.memory.salience.SalienceScorer` into the context budget:
``predict_chunk_value`` reads an optional ``salience`` factor, and this helper
sets it from the scorer's relevance-to-query judgement so ``ContextBudget``'s
``value`` packing keeps the items most salient to the current goal/step under a
token budget — not merely the highest raw retrieval score.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.memory.salience import SalienceScorer

# Map salience in [0,1] to a multiplier in [_MIN, _MAX] so a low-salience chunk
# is dampened (not zeroed — it can still fill leftover budget) and a highly
# salient one is boosted.
_MIN_MULT = 0.5
_MAX_MULT = 1.5


def enrich_with_salience(
    chunks: Sequence[dict[str, Any]],
    query: str,
    *,
    scorer: SalienceScorer | None = None,
) -> list[dict[str, Any]]:
    """Return chunks with a ``salience`` multiplier set from the scorer.

    Pure w.r.t. inputs: returns new dicts (does not mutate the originals) so the
    caller controls whether to adopt the enrichment.
    """
    if not chunks:
        return []
    scr = scorer or SalienceScorer()
    enriched: list[dict[str, Any]] = []
    for chunk in chunks:
        content = str(chunk.get("content", ""))
        score = scr.score(content=content, query=query) if content else 0.0
        multiplier = _MIN_MULT + (_MAX_MULT - _MIN_MULT) * score
        enriched.append({**chunk, "salience": round(multiplier, 4)})
    return enriched
