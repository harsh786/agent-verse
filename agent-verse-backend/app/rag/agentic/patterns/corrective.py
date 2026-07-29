"""Corrective RAG grading and reformulation primitives."""
from __future__ import annotations

import json
from typing import Any

from app.providers.base import CompletionRequest, Message
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError

MAX_CORRECTIVE_RETRIES = 2
CORRECTIVE_RELEVANCE_THRESHOLD = 0.6


async def grade_evidence(
    *,
    provider: Any,
    model: str,
    query: str,
    results: list[RetrievalResult],
) -> list[float]:
    passages = "\n".join(
        f"[{index}] {result.content[:500]}" for index, result in enumerate(results)
    )
    request = CompletionRequest(
        messages=[
            Message(
                role="system",
                content=(
                    "Grade each passage for relevance to the query from 0.0 to 1.0. "
                    'Return only JSON: {"relevance": [0.8, 0.2]}.'
                ),
            ),
            Message(role="user", content=f"Query: {query}\nPassages:\n{passages}"),
        ],
        model=model,
        max_tokens=200,
    )
    try:
        response = await provider.complete(request)
        payload = json.loads(response.content.strip())
        raw_scores = payload["relevance"]
        scores = [float(score) for score in raw_scores]
    except Exception as exc:
        if isinstance(exc, RetrievalStrategyExecutionError):
            raise
        raise RetrievalStrategyExecutionError("corrective", "evidence grading failed") from exc
    if len(scores) != len(results) or any(score < 0.0 or score > 1.0 for score in scores):
        raise RetrievalStrategyExecutionError("corrective", "invalid evidence grades")
    return scores


async def reformulate_query(
    *,
    provider: Any,
    model: str,
    query: str,
    attempt: int,
) -> str:
    request = CompletionRequest(
        messages=[
            Message(
                role="system",
                content=(
                    "Reformulate the query to improve persisted evidence retrieval. "
                    "Return only the reformulated query."
                ),
            ),
            Message(role="user", content=f"Attempt {attempt}: {query}"),
        ],
        model=model,
        max_tokens=120,
    )
    try:
        response = await provider.complete(request)
        reformulated = str(response.content).strip()
    except Exception as exc:
        if isinstance(exc, RetrievalStrategyExecutionError):
            raise
        raise RetrievalStrategyExecutionError("corrective", "query reformulation failed") from exc
    if not reformulated or reformulated == query:
        raise RetrievalStrategyExecutionError(
            "corrective", "query reformulation produced no change"
        )
    return reformulated


class CorrectiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "corrective_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "CRAG: evaluate retrieval quality score and context gap signals; "
            "automatically fall back to web search when confidence < threshold "
            "or gap phrases detected."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    async def execute(
        self,
        *,
        retriever_tool: Any,
        query: str,
        tenant_ctx: Any,
        collection_ids: list[str] | None = None,
        top_k: int = 5,
        confidence_threshold: float = 0.5,
        **kwargs: Any,
    ) -> Any:
        return await retriever_tool.retrieve_corrective(
            query=query,
            tenant_ctx=tenant_ctx,
            collection_ids=collection_ids,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
        )
