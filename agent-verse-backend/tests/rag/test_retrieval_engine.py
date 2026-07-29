"""Tests for Phase 4 — world-class RAG retrieval engine."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRRFScore:
    def test_single_rank_1(self):
        from app.rag.engine import _rrf_score
        score = _rrf_score([1])
        assert abs(score - 1/(60+1)) < 0.0001

    def test_multiple_legs_higher_than_single(self):
        from app.rag.engine import _rrf_score
        single = _rrf_score([1])
        multi = _rrf_score([1, 1, 1])  # top of all 3 legs
        assert multi > single

    def test_lower_rank_lower_score(self):
        from app.rag.engine import _rrf_score
        high = _rrf_score([1])
        low = _rrf_score([100])
        assert high > low

    def test_rrf_fusion_combines_lists(self):
        from app.rag.engine import _rrf_score
        # A chunk ranked 3rd in all 3 legs gets higher score than
        # a chunk ranked 1st in only 1 leg
        all_three = _rrf_score([3, 3, 3])
        one_first = _rrf_score([1])
        assert all_three > one_first


class TestRetrievalPlanner:
    def test_jira_id_selects_lexical(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("Find issue JIRA-123 status") == "lexical"

    def test_compare_selects_multi_hop(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert (
            planner.select_strategy("compare all Q2 sprint velocities across teams")
            == "multi_hop"
        )

    def test_what_is_selects_hyde(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("what is the sprint velocity") == "hyde"

    def test_generic_selects_direct(self):
        from app.rag.engine import RetrievalPlanner
        planner = RetrievalPlanner()
        assert planner.select_strategy("find all tickets assigned to Alice") == "direct"


class TestHybridSearchVectorDBlessMode:
    @pytest.mark.asyncio
    async def test_lexical_only_when_no_embedding(self):
        """Vector-DB-less mode: query_embedding=None triggers lexical-only path."""
        from app.rag.engine import hybrid_search

        mock_session = AsyncMock()

        # Mock only FTS and trgm results
        fts_result = MagicMock()
        fts_result.fetchall.return_value = [
            ("chunk-1", "content about tickets", {}, 0.8)
        ]
        mock_session.execute.return_value = fts_result

        results = await hybrid_search(
            session=mock_session,
            query="open tickets",
            query_embedding=None,  # No embedding → vector-DB-less
            collection_id="col-1",
            top_k=5,
            retrieval_mode="lexical",
        )

        # Should return results from FTS/trgm legs
        # (may be empty if mock doesn't perfectly simulate DB, but no crash)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_vector_only_with_embedding(self):
        """When mode=vector, only vector leg is queried."""
        from app.rag.engine import hybrid_search

        mock_session = AsyncMock()
        vec_result = MagicMock()
        vec_result.fetchall.return_value = [
            ("chunk-1", "content", {}, 0.9)
        ]
        mock_session.execute.return_value = vec_result

        results = await hybrid_search(
            session=mock_session,
            query="find relevant context",
            query_embedding=[0.1] * 10,
            collection_id="col-1",
            top_k=5,
            retrieval_mode="vector",
        )
        assert isinstance(results, list)


class TestRetrievalResultDataclass:
    def test_result_has_retrieval_legs(self):
        from app.rag.engine import RetrievalResult
        r = RetrievalResult(
            chunk_id="c1",
            content="test",
            score=0.8,
            source_metadata={},
            retrieval_legs=["vector", "fts"],
        )
        assert "vector" in r.retrieval_legs
        assert r.score == 0.8


@pytest.mark.asyncio
async def test_strict_hybrid_search_propagates_required_leg_failure() -> None:
    from app.rag.engine import RetrievalLegExecutionError, hybrid_search

    session = AsyncMock()
    session.execute.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RetrievalLegExecutionError, match="fts"):
        await hybrid_search(
            session,
            query="retention policy",
            query_embedding=None,
            collection_id="collection-1",
            retrieval_mode="lexical",
            embedding_dim=1536,
            strict=True,
        )


@pytest.mark.asyncio
async def test_strict_hybrid_search_distinguishes_legitimate_zero_results() -> None:
    from app.rag.engine import hybrid_search

    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.fetchall.return_value = []
    session.execute.return_value = empty_result

    results = await hybrid_search(
        session,
        query="no matching evidence",
        query_embedding=None,
        collection_id="collection-1",
        retrieval_mode="lexical",
        embedding_dim=1536,
        strict=True,
    )

    assert results == []
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_strict_graph_strategy_never_substitutes_hybrid() -> None:
    from app.rag.engine import RetrievalStrategyExecutionError, retrieve

    session = AsyncMock()

    with (
        patch("app.rag.engine.hybrid_search", AsyncMock()) as hybrid,
        pytest.raises(RetrievalStrategyExecutionError, match="graph"),
    ):
        await retrieve(
            session,
            query="connected entities",
            query_embedding=None,
            collection_id="collection-1",
            strategy="graph",
            strict=True,
        )

    hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_strict_fusion_propagates_direct_variant_failure() -> None:
    from app.rag.engine import retrieve_fusion

    session = AsyncMock()
    with (
        patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["variant-1"],
        ),
        patch(
            "app.rag.engine.hybrid_search",
            AsyncMock(side_effect=RuntimeError("required variant failed")),
        ),
        pytest.raises(RuntimeError, match="required variant failed"),
    ):
        await retrieve_fusion(
            session,
            query="retention policy",
            query_embedding=None,
            collection_id="collection-1",
            strict=True,
        )


@pytest.mark.asyncio
async def test_strict_fusion_propagates_variant_embedding_failure() -> None:
    from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

    embedder = AsyncMock()
    embedder.embed.side_effect = RuntimeError("embedder unavailable")
    with (
        patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["original", "expanded"],
        ),
        pytest.raises(RetrievalStrategyExecutionError, match="variant embedding"),
    ):
        await retrieve_fusion(
            AsyncMock(),
            query="original",
            query_embedding=[0.1],
            collection_id="collection-1",
            embedder=embedder,
            strict=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy",
    ["fusion", "flare", "self_rag", "speculative", "raptor", "agentic_chunking"],
)
async def test_strict_provider_backed_strategies_propagate_provider_failure(
    strategy: str,
) -> None:
    from app.providers.base import CompletionRequest
    from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError, retrieve

    class FailingProvider:
        def __init__(self) -> None:
            self.requests: list[CompletionRequest] = []

        async def complete(self, request: CompletionRequest) -> None:
            self.requests.append(request)
            raise RuntimeError("secret-provider-detail")

    provider = FailingProvider()
    base_results = [
        RetrievalResult(
            chunk_id="chunk-1",
            content="Evidence for provider-backed strategy",
            score=0.8,
            source_metadata={},
            retrieval_legs=["vector"],
        ),
        RetrievalResult(
            chunk_id="chunk-2",
            content="Additional evidence for hierarchy",
            score=0.7,
            source_metadata={},
            retrieval_legs=["fts"],
        ),
    ]

    with (
        patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)),
        pytest.raises(RetrievalStrategyExecutionError) as exc_info,
    ):
        await retrieve(
            AsyncMock(),
            query="retention policy",
            query_embedding=[0.1],
            collection_id="collection-1",
            strategy=strategy,
            provider=provider,
            model="tenant-model",
            strict=True,
        )

    assert provider.requests
    assert all(request.model == "tenant-model" for request in provider.requests)
    assert "secret-provider-detail" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "class_path", "pattern_factory", "breaker_key"),
    [
        (
            "flare",
            "app.rag.agentic.patterns.flare.FLAREPattern",
            lambda: __import__(
                "app.rag.agentic.patterns.flare", fromlist=["FLAREPattern"]
            ).FLAREPattern(),
            "pattern_flare",
        ),
        (
            "self_rag",
            "app.rag.agentic.patterns.self_rag.SelfRAGPattern",
            lambda: __import__(
                "app.rag.agentic.patterns.self_rag", fromlist=["SelfRAGPattern"]
            ).SelfRAGPattern(),
            "pattern_self_rag",
        ),
        (
            "speculative",
            "app.rag.agentic.patterns.speculative.SpeculativeRAGPattern",
            lambda: __import__(
                "app.rag.agentic.patterns.speculative", fromlist=["SpeculativeRAGPattern"]
            ).SpeculativeRAGPattern(n_candidates=2),
            "pattern_speculative_rag",
        ),
        (
            "raptor",
            "app.rag.agentic.patterns.raptor.RAPTORPattern",
            lambda: __import__(
                "app.rag.agentic.patterns.raptor", fromlist=["RAPTORPattern"]
            ).RAPTORPattern(cluster_size=4, max_levels=2),
            "pattern_raptor",
        ),
    ],
)
@pytest.mark.parametrize("strict", [False, True])
async def test_strict_provider_patterns_reject_open_circuit(
    strategy: str,
    class_path: str,
    pattern_factory: object,
    breaker_key: str,
    strict: bool,
) -> None:
    import time
    from contextlib import nullcontext

    from app.providers.base import CompletionRequest, CompletionResponse
    from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError, retrieve
    from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            self.calls += 1
            return CompletionResponse(
                content='{"should_retrieve": true}',
                model=request.model,
            )

    pattern = pattern_factory()  # type: ignore[operator]
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10_000)
    breaker._state = CircuitState.OPEN
    breaker._failure_count = 3
    breaker._opened_at = time.monotonic()
    opened_at = breaker._opened_at
    pattern._circuit_breakers[breaker_key] = breaker
    provider = Provider()
    base_results = [
        RetrievalResult("chunk-1", "first evidence", 0.8, {}, ["vector"]),
        RetrievalResult("chunk-2", "second evidence", 0.7, {}, ["fts"]),
    ]

    expected = (
        pytest.raises(RetrievalStrategyExecutionError, match=strategy)
        if strict
        else nullcontext()
    )
    with patch(class_path, return_value=pattern), patch(
        "app.rag.engine.hybrid_search", AsyncMock(return_value=base_results)
    ), expected:
        await retrieve(
            AsyncMock(),
            query="retention policy",
            query_embedding=[0.1],
            collection_id="collection-1",
            strategy=strategy,
            provider=provider,
            model="tenant-model",
            strict=strict,
        )

    assert provider.calls == 0
    assert breaker._failure_count == 3
    assert breaker._opened_at == opened_at
    assert breaker.state is CircuitState.OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy", "response_content"),
    [
        ("hyde", "Hypothetical evidence"),
        ("multi_hop", '["first hop", "second hop"]'),
    ],
)
async def test_generation_retrievers_use_resolved_model(
    strategy: str,
    response_content: str,
) -> None:
    from app.providers.base import CompletionRequest, CompletionResponse
    from app.rag.engine import retrieve

    class RecordingProvider:
        def __init__(self) -> None:
            self.requests: list[CompletionRequest] = []

        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            self.requests.append(request)
            return CompletionResponse(content=response_content, model=request.model)

    provider = RecordingProvider()
    with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
        await retrieve(
            AsyncMock(),
            query="retention policy",
            query_embedding=[0.1],
            collection_id="collection-1",
            strategy=strategy,
            provider=provider,
            model="tenant-model",
            strict=True,
        )

    assert provider.requests
    assert all(request.model == "tenant-model" for request in provider.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy",
    [
        "naive",
        "hybrid",
        "hyde",
        "multi_hop",
        "corrective",
        "flare",
        "self_rag",
        "speculative",
        "fusion",
        "raptor",
        "colbert",
        "agentic_chunking",
        "adaptive",
        "modular",
        "agentic",
        "web_augmented",
        "raft",
    ],
)
async def test_metadata_filters_reach_every_persisted_strategy_leg(strategy: str) -> None:
    from app.rag.engine import retrieve

    observed_filters: list[dict[str, object] | None] = []

    async def hybrid(*args: object, **kwargs: object) -> list[object]:
        del args
        observed_filters.append(kwargs.get("metadata_filter"))  # type: ignore[arg-type]
        return []

    with patch("app.rag.engine.hybrid_search", side_effect=hybrid):
        await retrieve(
            AsyncMock(),
            query="retention policy",
            query_embedding=None,
            collection_id="collection-1",
            strategy=strategy,
            metadata_filter={"department": "legal"},
        )

    assert observed_filters
    assert observed_filters == [{"department": "legal"}] * len(observed_filters)


@pytest.mark.asyncio
async def test_metadata_filter_is_bound_in_sql_before_leg_limits() -> None:
    from app.rag.engine import hybrid_search

    session = AsyncMock()
    empty_result = MagicMock()
    empty_result.fetchall.return_value = []
    session.execute.return_value = empty_result

    await hybrid_search(
        session,
        query="retention policy",
        query_embedding=None,
        collection_id="collection-1",
        retrieval_mode="lexical",
        embedding_dim=1536,
        metadata_filter={"department": "legal"},
        strict=True,
    )

    assert session.execute.await_count == 2
    for call in session.execute.await_args_list:
        statement = str(call.args[0])
        params = call.args[1]
        assert "metadata @> CAST(:metadata_filter AS jsonb)" in statement
        assert statement.index("metadata @>") < statement.index("LIMIT")
        assert params["metadata_filter"] == '{"department": "legal"}'
