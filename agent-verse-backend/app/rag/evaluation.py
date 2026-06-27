"""RAGAS-inspired retrieval evaluation for AgentVerse knowledge collections.

Metrics computed:
  - Precision@K: fraction of retrieved chunks that are relevant.
  - Recall@K:    fraction of expected chunks that were retrieved.
  - MRR:         Mean Reciprocal Rank of the first relevant result.
  - Overall collection quality score (weighted average).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.rag.store import KnowledgeStore

logger = get_logger(__name__)


@dataclass
class QueryEvalResult:
    query: str
    retrieved_chunk_ids: list[str]
    expected_chunk_ids: list[str]
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    retrieved_texts: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    collection_id: str
    num_queries: int
    mean_precision: float
    mean_recall: float
    mean_mrr: float
    overall_score: float
    query_results: list[QueryEvalResult]
    low_quality_chunks: list[str]      # chunk_ids with lowest relevance
    recommendations: list[str]


class RetrievalEvaluator:
    """Evaluate retrieval quality of a knowledge collection.

    Usage::

        evaluator = RetrievalEvaluator(store)
        report = await evaluator.evaluate_collection(
            collection_id="col-123",
            test_queries=["What is the refund policy?"],
            expected_chunks=[["chunk-1", "chunk-5"]],
            k=5,
        )
        print(f"Overall score: {report.overall_score:.2f}")
    """

    def __init__(self, knowledge_store: KnowledgeStore) -> None:
        self._store = knowledge_store

    async def evaluate_collection(
        self,
        collection_id: str,
        test_queries: list[str],
        expected_chunks: list[list[str]],  # per-query expected chunk IDs
        k: int = 5,
    ) -> EvalReport:
        """Run evaluation on a collection.

        Args:
            collection_id: Target knowledge collection ID.
            test_queries: List of natural-language test queries.
            expected_chunks: Parallel list of expected chunk IDs per query.
            k: Number of results to retrieve per query.

        Returns:
            EvalReport with detailed per-query breakdown.
        """
        if len(test_queries) != len(expected_chunks):
            raise ValueError(
                f"test_queries ({len(test_queries)}) and expected_chunks ({len(expected_chunks)}) "
                "must have the same length."
            )

        query_results: list[QueryEvalResult] = []
        chunk_relevance_hits: dict[str, int] = {}

        for query, expected in zip(test_queries, expected_chunks):
            result = await self._evaluate_query(collection_id, query, expected, k)
            query_results.append(result)
            # Track which expected chunks were never found (for low-quality detection)
            for chunk_id in expected:
                if chunk_id not in result.retrieved_chunk_ids:
                    chunk_relevance_hits[chunk_id] = chunk_relevance_hits.get(chunk_id, 0) + 1

        precisions = [r.precision_at_k for r in query_results]
        recalls = [r.recall_at_k for r in query_results]
        mrrs = [r.reciprocal_rank for r in query_results]

        mean_precision = statistics.mean(precisions) if precisions else 0.0
        mean_recall = statistics.mean(recalls) if recalls else 0.0
        mean_mrr = statistics.mean(mrrs) if mrrs else 0.0

        # Weighted overall score: 40% precision, 40% recall, 20% MRR
        overall_score = 0.4 * mean_precision + 0.4 * mean_recall + 0.2 * mean_mrr

        # Identify chunks that were expected but never retrieved (low quality)
        low_quality_chunks = [
            chunk_id
            for chunk_id, miss_count in chunk_relevance_hits.items()
            if miss_count >= len(test_queries) // 2  # missed in >= 50% of queries
        ]

        recommendations = self._generate_recommendations(
            overall_score, mean_precision, mean_recall, mean_mrr, low_quality_chunks
        )

        return EvalReport(
            collection_id=collection_id,
            num_queries=len(test_queries),
            mean_precision=round(mean_precision, 4),
            mean_recall=round(mean_recall, 4),
            mean_mrr=round(mean_mrr, 4),
            overall_score=round(overall_score, 4),
            query_results=query_results,
            low_quality_chunks=low_quality_chunks,
            recommendations=recommendations,
        )

    async def _evaluate_query(
        self,
        collection_id: str,
        query: str,
        expected_chunk_ids: list[str],
        k: int,
    ) -> QueryEvalResult:
        """Evaluate a single query against the collection."""
        try:
            search_results = await self._store.search(
                collection_id=collection_id,
                query=query,
                top_k=k,
            )
        except Exception as exc:
            logger.warning("Search failed for query=%r: %s", query, exc)
            search_results = []

        retrieved_ids = [r.get("chunk_id", r.get("id", "")) for r in search_results]
        retrieved_texts = [r.get("text", r.get("content", "")) for r in search_results]
        expected_set = set(expected_chunk_ids)
        retrieved_set = set(retrieved_ids)

        # Precision@K: how many retrieved are relevant
        relevant_retrieved = len(expected_set & retrieved_set)
        precision = relevant_retrieved / k if k > 0 else 0.0

        # Recall@K: how many expected were retrieved
        recall = relevant_retrieved / len(expected_set) if expected_set else 1.0

        # MRR: rank of first relevant result
        reciprocal_rank = 0.0
        for rank, chunk_id in enumerate(retrieved_ids, start=1):
            if chunk_id in expected_set:
                reciprocal_rank = 1.0 / rank
                break

        return QueryEvalResult(
            query=query,
            retrieved_chunk_ids=retrieved_ids,
            expected_chunk_ids=expected_chunk_ids,
            precision_at_k=round(precision, 4),
            recall_at_k=round(recall, 4),
            reciprocal_rank=round(reciprocal_rank, 4),
            retrieved_texts=retrieved_texts,
        )

    def _generate_recommendations(
        self,
        overall: float,
        precision: float,
        recall: float,
        mrr: float,
        low_quality_chunks: list[str],
    ) -> list[str]:
        """Generate human-readable improvement recommendations."""
        recs: list[str] = []

        if overall < 0.5:
            recs.append("Overall quality is low. Consider re-embedding with a higher-quality model.")
        if precision < 0.4:
            recs.append(
                "Low precision: retrieval returns many irrelevant chunks. "
                "Try reducing chunk size or increasing embedding dimensions."
            )
        if recall < 0.5:
            recs.append(
                "Low recall: expected chunks are not being retrieved. "
                "Try increasing top_k, using hybrid search, or re-chunking with more overlap."
            )
        if mrr < 0.3:
            recs.append(
                "Low MRR: relevant chunks appear low in ranking. "
                "Consider re-ranking with a cross-encoder model."
            )
        if low_quality_chunks:
            recs.append(
                f"{len(low_quality_chunks)} chunks were consistently missed. "
                "Review and re-chunk these source segments."
            )
        if not recs:
            recs.append(
                "Retrieval quality is good. Continue monitoring with regular evaluations."
            )
        return recs
