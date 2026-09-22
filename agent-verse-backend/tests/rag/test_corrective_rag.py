"""Corrective retrieval delegates to the canonical gateway strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.governance.policies import PolicyResult
from app.providers.base import CompletionRequest, CompletionResponse
from app.rag.agentic.patterns.base import RAGPatternState
from app.rag.agentic.patterns.corrective import (
    CORRECTIVE_RELEVANCE_THRESHOLD,
    CorrectiveRAGPattern,
    grade_evidence,
    reformulate_query,
)
from app.rag.agentic.patterns.web_augmented import WebEvidence, WebSearchRequest
from app.rag.agentic.retriever_tool import RetrieverTool
from app.rag.contracts import RAGExecutionRequest, RAGExecutionResult, RAGStrategy
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError
from app.rag.gateway import (
    ResolvedLLM,
    RetrievalExecutionContext,
    RetrievalRuntimeDependencies,
    core_strategy_capabilities,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("t1", PlanTier.PROFESSIONAL, "k1")


@pytest.mark.asyncio
async def test_corrective_retrieval_has_no_local_web_fallback() -> None:
    calls: list[dict[str, Any]] = []

    class Gateway:
        async def execute(
            self, tenant_ctx: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            calls.append(kwargs)
            return RAGExecutionResult(
                requested_strategy_id="corrective",
                resolved_strategy_id=RAGStrategy.CORRECTIVE,
            )

    result = await RetrieverTool(retrieval_gateway=Gateway()).retrieve_corrective(
        "policy",
        tenant_ctx=TenantContext("t1", PlanTier.PROFESSIONAL, "k1"),
        collection_ids=["collection-1"],
    )

    assert result.strategy_used == "corrective"
    assert calls[0]["strategy_id"] is RAGStrategy.CORRECTIVE
    assert not result.fallback_used


# ---------------------------------------------------------------------------
# End-to-end correction branch: these tests route through the real production
# path (CorrectiveRAGPattern -> RetrieverTool -> gateway.execute) into the
# actual certified CORRECTIVE adapter, so the low-confidence -> reformulate ->
# retry -> web-fallback branch in app/rag/gateway.py genuinely runs instead of
# being pre-empted by a hand-rolled fake gateway.
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> str | None:
        return str(self._rows[0][0]) if self._rows else None


class _AuthorizedSession:
    async def execute(self, statement: object, params: object = None) -> _Rows:
        return _Rows([("collection-1",)])


class _Embedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.calls += 1
        return EmbedResponse(embeddings=[[float(self.calls), 0.1]], model="embed-model")


class _AllowWebPolicy:
    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        assert tool_name == "web_search"
        return PolicyResult.ALLOW

    def web_allowed_domains(self, tenant_ctx: TenantContext) -> tuple[str, ...]:
        return ("8.8.8.8",)


class _WebCapability:
    configured = True

    def __init__(self) -> None:
        self.requests: list[WebSearchRequest] = []

    async def search(self, request: WebSearchRequest) -> list[WebEvidence]:
        self.requests.append(request)
        return [
            WebEvidence(
                title="Corrected external evidence",
                url="https://8.8.8.8/answer",
                content="Corrected evidence sourced from the web.",
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                source="safe-search",
                domain="8.8.8.8",
                freshness_seconds=0.0,
            )
        ]


class _AdapterBridgeGateway:
    """Routes RetrieverTool.execute() calls into the real CORRECTIVE adapter.

    RetrieverTool only requires a duck-typed `execute(tenant_ctx, **kwargs)`
    coroutine, so this builds the same RAGExecutionRequest /
    RetrievalExecutionContext that RetrievalGateway.execute() would build,
    and hands them to the certified adapter directly. That means the
    reformulate -> re-retrieve -> web-fallback branch owned by
    app/rag/gateway.py actually executes end to end.
    """

    def __init__(
        self,
        *,
        embedder: Any,
        provider: Any,
        web: Any = None,
        policies: tuple[Any, ...] = (),
    ) -> None:
        self._embedder = embedder
        self._provider = provider
        self._web = web
        self._policies = policies

    async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        request = RAGExecutionRequest(
            tenant_id=tenant_ctx.tenant_id,
            query=kwargs["query"],
            requested_strategy_id=str(kwargs["strategy_id"]),
            collection_id=kwargs["collection_id"],
            top_k=kwargs["top_k"],
            filters=kwargs.get("filters") or {},
        )

        async def runner(operation: Any) -> Any:
            return await operation(_AuthorizedSession())

        context = RetrievalExecutionContext(
            tenant_context=tenant_ctx,
            strategy=RAGStrategy.CORRECTIVE,
            filters=kwargs.get("filters") or {},
            dependencies=RetrievalRuntimeDependencies(
                embedder=self._embedder,
                llm=ResolvedLLM(provider=self._provider, model="tenant-rag-model", provider_type="test"),
                graph_capability=None,
                search_capability=self._web,
                policy_services=self._policies,
                available_strategies=(),
                long_term_memory=None,
            ),
            _db_operation_runner=runner,
        )
        adapter = core_strategy_capabilities()[RAGStrategy.CORRECTIVE].adapter
        result = await adapter.execute(request, context)
        assert isinstance(result, RAGExecutionResult)
        return result


class _CorrectiveThenWebProvider:
    """Always grades persisted evidence below threshold; reformulates deterministically."""

    def __init__(self) -> None:
        self.reformulations = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.messages[0].content.startswith("Reformulate"):
            self.reformulations += 1
            return CompletionResponse(
                content=f"reformulated query {self.reformulations}", model=request.model
            )
        return CompletionResponse(content='{"relevance": [0.1]}', model=request.model)


@pytest.mark.asyncio
async def test_corrective_pattern_drives_correction_end_to_end_via_web_fallback() -> None:
    """Low-confidence retrieval must trigger reformulate/retry then web
    correction, and the corrected (web) evidence -- not the stale persisted
    evidence -- must be what the pattern ultimately returns."""
    provider = _CorrectiveThenWebProvider()
    embedder = _Embedder()
    web = _WebCapability()
    persisted_calls: list[str] = []

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        persisted_calls.append(kwargs["query"])
        return [
            RetrievalResult(
                f"stale-{len(persisted_calls)}",
                "Stale low-confidence evidence.",
                0.2,
                {"source": "stale.pdf"},
                ["vector"],
            )
        ]

    gateway = _AdapterBridgeGateway(
        embedder=embedder, provider=provider, web=web, policies=(_AllowWebPolicy(),)
    )
    tool = RetrieverTool(retrieval_gateway=gateway)
    pattern = CorrectiveRAGPattern()

    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await pattern.execute(
            retriever_tool=tool,
            query="what is the retention window",
            tenant_ctx=TENANT,
            collection_ids=["collection-1"],
            top_k=1,
        )

    # Bounded retries were exhausted (initial attempt + 2 reformulated retries).
    assert persisted_calls == [
        "what is the retention window",
        "reformulated query 1",
        "reformulated query 2",
    ]
    assert provider.reformulations == 2
    assert len(web.requests) == 1

    # The final result is the corrected web evidence, not the stale persisted evidence.
    assert [chunk["chunk_id"] for chunk in result.chunks] != []
    assert all(chunk_id.startswith("web:") for chunk_id in (c["chunk_id"] for c in result.chunks))
    assert all("stale" not in chunk["chunk_id"] for chunk in result.chunks)

    stop_traces = [t for t in result.strategy_trace if t["action"] == "corrective_stop"]
    assert stop_traces[-1]["detail"]["stop_reason"] == "web_fallback_complete"
    assert sum(1 for t in result.strategy_trace if t["action"] == "evidence_grade") == 3
    assert sum(1 for t in result.strategy_trace if t["action"] == "query_reformulation") == 2


@pytest.mark.asyncio
async def test_corrective_pattern_correction_failure_returns_empty_not_error() -> None:
    """When persisted evidence never clears the relevance bar and web
    correction is unavailable, the pattern must return an empty result
    (not raise, and not silently surface the stale low-confidence evidence)."""
    provider = _CorrectiveThenWebProvider()
    embedder = _Embedder()

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        return [RetrievalResult("stale-1", "Stale low-confidence evidence.", 0.2, {}, ["vector"])]

    gateway = _AdapterBridgeGateway(embedder=embedder, provider=provider, web=None, policies=())
    tool = RetrieverTool(retrieval_gateway=gateway)
    pattern = CorrectiveRAGPattern()

    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await pattern.execute(
            retriever_tool=tool,
            query="what is the retention window",
            tenant_ctx=TENANT,
            collection_ids=["collection-1"],
            top_k=1,
        )

    assert result.chunks == []
    assert result.citations == []
    stop_traces = [t for t in result.strategy_trace if t["action"] == "corrective_stop"]
    assert stop_traces[-1]["detail"]["stop_reason"] == "web_capability_unavailable"


@pytest.mark.asyncio
async def test_corrective_threshold_boundary_score_exactly_at_cutoff_is_retained() -> None:
    """A relevance grade exactly equal to CORRECTIVE_RELEVANCE_THRESHOLD must
    be retained: the gateway's filter is inclusive (`score >= threshold`)."""

    class _BoundaryProvider:
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            return CompletionResponse(
                content=f'{{"relevance": [{CORRECTIVE_RELEVANCE_THRESHOLD}]}}',
                model=request.model,
            )

    provider = _BoundaryProvider()
    embedder = _Embedder()
    calls: list[str] = []

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        calls.append(kwargs["query"])
        # Raw retrieval score is comfortably above RetrieverTool's own
        # min_confidence filter so only the gateway's grade-threshold
        # boundary behaviour is under test here.
        return [RetrievalResult("boundary-1", "Right at the line.", 0.9, {}, ["vector"])]

    gateway = _AdapterBridgeGateway(embedder=embedder, provider=provider)
    tool = RetrieverTool(retrieval_gateway=gateway)
    pattern = CorrectiveRAGPattern()

    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await pattern.execute(
            retriever_tool=tool,
            query="policy",
            tenant_ctx=TENANT,
            collection_ids=["collection-1"],
            top_k=1,
        )

    assert calls == ["policy"]  # no retries needed: exact-threshold evidence was sufficient
    assert [chunk["chunk_id"] for chunk in result.chunks] == ["boundary-1"]


@pytest.mark.asyncio
async def test_corrective_threshold_boundary_score_just_below_cutoff_is_dropped() -> None:
    """A relevance grade just under CORRECTIVE_RELEVANCE_THRESHOLD must be
    dropped, forcing a reformulation attempt."""

    class _JustBelowProvider:
        def __init__(self) -> None:
            self.reformulations = 0

        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            if request.messages[0].content.startswith("Reformulate"):
                self.reformulations += 1
                return CompletionResponse(
                    content=f"reformulated policy query {self.reformulations}",
                    model=request.model,
                )
            return CompletionResponse(
                content=f'{{"relevance": [{CORRECTIVE_RELEVANCE_THRESHOLD - 0.01}]}}',
                model=request.model,
            )

    provider = _JustBelowProvider()
    embedder = _Embedder()
    calls: list[str] = []

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        calls.append(kwargs["query"])
        return [RetrievalResult(f"c{len(calls)}", "Almost relevant.", 0.9, {}, ["vector"])]

    gateway = _AdapterBridgeGateway(embedder=embedder, provider=provider)
    tool = RetrieverTool(retrieval_gateway=gateway)
    pattern = CorrectiveRAGPattern()

    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await pattern.execute(
            retriever_tool=tool,
            query="policy",
            tenant_ctx=TENANT,
            collection_ids=["collection-1"],
            top_k=1,
        )

    assert len(calls) > 1  # a reformulated retry was attempted
    assert provider.reformulations > 0
    assert result.chunks == []  # below-threshold evidence was never retained


# ---------------------------------------------------------------------------
# Unit-level coverage of grade_evidence / reformulate_query / pattern metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grade_evidence_wraps_provider_exceptions() -> None:
    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("provider unavailable")

    with pytest.raises(RetrievalStrategyExecutionError, match="evidence grading failed"):
        await grade_evidence(
            provider=provider,
            model="m",
            query="policy",
            results=[RetrievalResult("c1", "text", 0.5, {}, ["vector"])],
        )


@pytest.mark.asyncio
async def test_grade_evidence_wraps_malformed_json_response() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="not json at all", model="m")

    with pytest.raises(RetrievalStrategyExecutionError, match="evidence grading failed"):
        await grade_evidence(
            provider=provider,
            model="m",
            query="policy",
            results=[RetrievalResult("c1", "text", 0.5, {}, ["vector"])],
        )


@pytest.mark.asyncio
async def test_grade_evidence_rejects_score_count_mismatch() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content='{"relevance": [0.5, 0.5]}', model="m"
    )

    with pytest.raises(RetrievalStrategyExecutionError, match="invalid evidence grades"):
        await grade_evidence(
            provider=provider,
            model="m",
            query="policy",
            # Only one result, but the provider graded two passages.
            results=[RetrievalResult("c1", "text", 0.5, {}, ["vector"])],
        )


@pytest.mark.asyncio
async def test_grade_evidence_rejects_out_of_range_score() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content='{"relevance": [1.5]}', model="m")

    with pytest.raises(RetrievalStrategyExecutionError, match="invalid evidence grades"):
        await grade_evidence(
            provider=provider,
            model="m",
            query="policy",
            results=[RetrievalResult("c1", "text", 0.5, {}, ["vector"])],
        )


@pytest.mark.asyncio
async def test_reformulate_query_wraps_provider_exceptions() -> None:
    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("provider unavailable")

    with pytest.raises(RetrievalStrategyExecutionError, match="query reformulation failed"):
        await reformulate_query(provider=provider, model="m", query="policy", attempt=1)


@pytest.mark.asyncio
async def test_reformulate_query_rejects_unchanged_query() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="policy", model="m")

    with pytest.raises(RetrievalStrategyExecutionError, match="no change"):
        await reformulate_query(provider=provider, model="m", query="policy", attempt=1)


@pytest.mark.asyncio
async def test_reformulate_query_rejects_empty_response() -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="   ", model="m")

    with pytest.raises(RetrievalStrategyExecutionError, match="no change"):
        await reformulate_query(provider=provider, model="m", query="policy", attempt=1)


def test_corrective_pattern_metadata() -> None:
    pattern = CorrectiveRAGPattern()

    assert pattern.pattern_id == "corrective_rag"
    assert pattern.state is RAGPatternState.IMPLEMENTED
    assert "confidence" in pattern.description.lower()
    assert pattern.is_compatible(goal_properties=object()) is True
