"""Corrective RAG grading and reformulation primitives."""

from __future__ import annotations

import json
import re
from typing import Any

from app.providers.base import CompletionRequest, Message
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError

MAX_CORRECTIVE_RETRIES = 2
CORRECTIVE_RELEVANCE_THRESHOLD = 0.6
# One passage graded at or above this is sufficient on its own (CRAG "Correct").
CORRECTIVE_CONFIDENT_THRESHOLD = 0.85


async def grade_evidence(
    *,
    provider: Any,
    model: str,
    query: str,
    results: list[RetrievalResult],
) -> list[float]:
    count = len(results)
    passages = "\n".join(
        f"[{index + 1}] {result.content[:500]}" for index, result in enumerate(results)
    )
    # The grade list must match the passages one-to-one. With a free-form
    # prompt, no schema, no stated count and 200 tokens, a model grading the
    # widened (20-passage) candidate set returned a short list, and the whole
    # strategy failed with "invalid evidence grades".
    schema = {
        "type": "object",
        "properties": {
            "relevance": {
                "type": "array",
                "items": {"type": "number", "minimum": 0, "maximum": 1},
                "minItems": count,
                "maxItems": count,
            }
        },
        "required": ["relevance"],
        "additionalProperties": False,
    }
    request = CompletionRequest(
        messages=[
            Message(
                role="system",
                content=(
                    f"Grade each of the {count} passages for relevance to the query from "
                    "0.0 to 1.0, in passage order. Return only JSON with exactly "
                    f'{count} numbers, e.g. {{"relevance": [0.8, 0.2, ...]}}.'
                ),
            ),
            Message(role="user", content=f"Query: {query}\nPassages:\n{passages}"),
        ],
        model=model,
        max_tokens=max(1024, 16 * count + 256),
        response_schema=schema,
    )
    try:
        response = await provider.complete(request)
        payload = json.loads(
            re.sub(r"<think>.*?</think>", "", str(response.content), flags=re.DOTALL).strip()
        )
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
        confidence_threshold: float = CORRECTIVE_RELEVANCE_THRESHOLD,
        **kwargs: Any,
    ) -> Any:
        return await retriever_tool.retrieve_corrective(
            query=query,
            tenant_ctx=tenant_ctx,
            collection_ids=collection_ids,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
        )
