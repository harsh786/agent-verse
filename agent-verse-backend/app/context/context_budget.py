"""ContextBudget — enforces token limits on retrieved context.

Two packing strategies:

- ``first_fit`` (default): pack chunks in the order given, skipping any that
  would overflow the token budget while continuing to scan later chunks (so an
  oversized chunk never discards later fitting ones). Backwards-compatible.
- ``value``: attach a predicted value per chunk (retrieval score x source-trust
  x recency x historical-usefulness) and greedily pack by value-per-token
  density to maximise total value under the same budget (fractional-relaxation
  knapsack heuristic).

Token counts come from the real tokenizer (:func:`app.agent.tokenizer.count_tokens`,
tiktoken with a byte-length fallback), not a ``len // 4`` character heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.agent.tokenizer import count_tokens
from app.context.rerank_policy import predict_chunk_value

Strategy = Literal["first_fit", "value"]


@dataclass
class BudgetResult:
    included_chunks: list[dict[str, Any]]
    excluded_count: int
    total_tokens: int


class ContextBudget:
    def __init__(
        self,
        max_tokens: int = 6000,
        max_chunks: int = 20,
        max_per_source: int = 0,
    ) -> None:
        # ``max_per_source == 0`` means unlimited (preserves prior behaviour where
        # per-source diversity was handled upstream in RerankPolicy).
        self._max_tokens = max_tokens
        self._max_chunks = max_chunks
        self._max_per_source = max_per_source

    def apply(
        self,
        chunks: list[dict[str, Any]],
        *,
        strategy: Strategy = "first_fit",
        max_tokens: int | None = None,
        max_chunks: int | None = None,
        max_per_source: int | None = None,
    ) -> BudgetResult:
        """Pack ``chunks`` under the (optionally per-call overridden) limits.

        Per-call ``max_tokens`` / ``max_chunks`` / ``max_per_source`` override the
        constructor defaults for this call only, so a future learned source-mix
        can vary the limits without mutating the shared instance (task 2.3).
        """
        mt = self._max_tokens if max_tokens is None else max_tokens
        mc = self._max_chunks if max_chunks is None else max_chunks
        mps = self._max_per_source if max_per_source is None else max_per_source

        # TODO(context-pipeline): ContextPipeline.run (app/context/context_pipeline.py)
        # currently always calls ``self._budget.apply(reranked)`` with the default
        # first-fit strategy. Once value signals are wired through the pipeline,
        # pass ``strategy="value"`` here and enrich each chunk with the value
        # signals predict_chunk_value() reads (score, source_type/source_trust,
        # age_days/recency, historical_usefulness) so packing maximises value.
        if strategy == "value":
            ordered = self._order_by_value(chunks)
        else:
            ordered = [(c, count_tokens(str(c.get("content", "")))) for c in chunks]

        return self._pack(ordered, mt, mc, mps, total=len(chunks))

    def _order_by_value(
        self, chunks: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], int]]:
        """Order chunks by value-per-token density (descending) for knapsack packing."""
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for chunk in chunks:
            tokens = max(1, count_tokens(str(chunk.get("content", ""))))
            density = predict_chunk_value(chunk) / tokens
            scored.append((density, tokens, chunk))
        # Stable sort: preserves input order among equal densities.
        scored.sort(key=lambda t: t[0], reverse=True)
        return [(chunk, tokens) for _density, tokens, chunk in scored]

    def _pack(
        self,
        ordered: list[tuple[dict[str, Any], int]],
        max_tokens: int,
        max_chunks: int,
        max_per_source: int,
        *,
        total: int,
    ) -> BudgetResult:
        included: list[dict[str, Any]] = []
        token_count = 0
        source_counts: dict[str, int] = {}

        for chunk, chunk_tokens in ordered:
            chunk_tokens = max(1, chunk_tokens)

            if len(included) >= max_chunks:
                break
            if max_per_source > 0:
                src = str(chunk.get("source_url", "_"))
                if source_counts.get(src, 0) >= max_per_source:
                    continue
            # Skip an oversized chunk but keep scanning later (fitting) chunks.
            # Always admit at least one chunk even if it alone exceeds the budget.
            if token_count + chunk_tokens > max_tokens and included:
                continue

            included.append(chunk)
            token_count += chunk_tokens
            if max_per_source > 0:
                source_counts[str(chunk.get("source_url", "_"))] = (
                    source_counts.get(str(chunk.get("source_url", "_")), 0) + 1
                )

        return BudgetResult(
            included_chunks=included,
            excluded_count=total - len(included),
            total_tokens=token_count,
        )
