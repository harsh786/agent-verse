"""Tests for Phase 4 — world-class RAG retrieval engine."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_legacy_colbert_dispatch_awaits_async_reranker_without_blocking_loop():
    from app.rag.engine import RetrievalResult, retrieve

    candidates = [
        RetrievalResult(
            chunk_id="c1",
            content="policy",
            score=0.8,
            source_metadata={},
        )
    ]

    async def rerank_async(*args, **kwargs):
        del args, kwargs
        await asyncio.sleep(0.03)
        return [
            {
                "chunk_id": "c1",
                "content": "policy",
                "score": 0.9,
                "colbert_score": 0.9,
                "source_metadata": {},
            }
        ]

    heartbeat_ran = False

    async def heartbeat():
        nonlocal heartbeat_ran
        await asyncio.sleep(0.005)
        heartbeat_ran = True

    with (
        patch("app.rag.engine.hybrid_search", new=AsyncMock(return_value=candidates)),
        patch(
            "app.rag.agentic.patterns.colbert.ColBERTPattern.rerank",
            side_effect=AssertionError("sync rerank called on event loop"),
        ),
        patch(
            "app.rag.agentic.patterns.colbert.ColBERTPattern.rerank_async",
            side_effect=rerank_async,
        ) as async_rerank,
    ):
        results, _ = await asyncio.gather(
            retrieve(
                MagicMock(),
                query="policy",
                query_embedding=[1.0],
                collection_id="collection-1",
                top_k=1,
                strategy="colbert",
                strict=True,
            ),
            heartbeat(),
        )

    assert heartbeat_ran
    assert async_rerank.await_count == 1
    assert results[0].chunk_id == "c1"


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
            return_value=["variant-1", "variant-2"],
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
            query_embedding=[0.1],
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
    ["fusion", "flare", "self_rag", "speculative"],
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

        async def embed(self, request: object) -> object:
            from app.providers.base import EmbedResponse

            return EmbedResponse(embeddings=[[0.2]])

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
            embedder=provider,
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
    assert all(
        observed_filter is not None
        and observed_filter.get("department") == "legal"
        for observed_filter in observed_filters
    )


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


@pytest.mark.asyncio
async def test_self_rag_provider_helper_opens_at_failure_threshold() -> None:
    from app.providers.base import CompletionRequest, Message
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("provider failed")
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    request = CompletionRequest(
        messages=[Message(role="user", content="private prompt")],
        model="tenant-model",
    )
    pattern = SelfRAGPattern()

    assert await pattern._complete_with_breaker(
        provider=provider,
        request=request,
        breaker=breaker,
        strict=False,
    ) is None
    assert breaker._failure_count == 1
    assert breaker.state is CircuitState.CLOSED

    assert await pattern._complete_with_breaker(
        provider=provider,
        request=request,
        breaker=breaker,
        strict=False,
    ) is None
    assert breaker._failure_count == 2
    assert breaker.state is CircuitState.OPEN
    assert breaker._opened_at > 0
    assert provider.complete.await_count == 2


@pytest.mark.asyncio
async def test_self_rag_provider_helper_success_resets_breaker() -> None:
    from app.providers.base import CompletionRequest, CompletionResponse, Message
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(content="ok", model="tenant-model")
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    breaker._failure_count = 2
    request = CompletionRequest(
        messages=[Message(role="user", content="private prompt")],
        model="tenant-model",
    )

    response = await SelfRAGPattern()._complete_with_breaker(
        provider=provider,
        request=request,
        breaker=breaker,
        strict=True,
    )

    assert response is not None and response.content == "ok"
    assert breaker._failure_count == 0
    assert breaker._opened_at == 0.0
    assert breaker.state is CircuitState.CLOSED
    provider.complete.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_self_rag_provider_helper_blocked_call_is_inert(strict: bool) -> None:
    import time
    from contextlib import nullcontext

    from app.providers.base import CompletionRequest, Message
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

    provider = AsyncMock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10_000)
    breaker._state = CircuitState.OPEN
    breaker._failure_count = 2
    breaker._opened_at = time.monotonic()
    opened_at = breaker._opened_at
    request = CompletionRequest(
        messages=[Message(role="user", content="private prompt")],
        model="tenant-model",
    )
    expected = pytest.raises(RuntimeError, match="circuit is open") if strict else nullcontext()

    with expected:
        result = await SelfRAGPattern()._complete_with_breaker(
            provider=provider,
            request=request,
            breaker=breaker,
            strict=strict,
        )
        assert result is None

    provider.complete.assert_not_awaited()
    assert breaker._failure_count == 2
    assert breaker._opened_at == opened_at
    assert breaker.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_self_rag_provider_helper_strict_failure_is_sanitized() -> None:
    from app.providers.base import CompletionRequest, Message
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
    from app.reliability.circuit_breaker import CircuitBreaker

    provider = AsyncMock()
    provider.complete.side_effect = RuntimeError("private prompt and secret-value")
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    request = CompletionRequest(
        messages=[Message(role="user", content="private prompt")],
        model="tenant-model",
    )

    with pytest.raises(RuntimeError) as exc_info:
        await SelfRAGPattern()._complete_with_breaker(
            provider=provider,
            request=request,
            breaker=breaker,
            strict=True,
        )

    assert str(exc_info.value) == "Self-RAG provider call failed"
    assert "private prompt" not in str(exc_info.value)
    assert "secret-value" not in str(exc_info.value)
    assert breaker._failure_count == 1
    provider.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_self_rag_routes_decision_generation_and_critique_through_helper() -> None:
    from app.providers.base import CompletionResponse
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

    provider = AsyncMock()
    provider.complete.side_effect = [
        CompletionResponse(
            content='{"should_retrieve": true}',
            model="tenant-model",
        ),
        CompletionResponse(content="grounded answer", model="tenant-model"),
        CompletionResponse(
            content=(
                '{"is_relevant": true, "is_supported": true, '
                '"is_useful": true, "confidence": 0.9}'
            ),
            model="tenant-model",
        ),
    ]
    pattern = SelfRAGPattern()

    with patch.object(
        pattern,
        "_complete_with_breaker",
        wraps=pattern._complete_with_breaker,
    ) as complete:
        result = await pattern.execute_with_critique(
            query="retention policy",
            provider=provider,
            retrieve_fn=AsyncMock(return_value="tenant evidence"),
            model="tenant-model",
            strict=True,
        )

    assert result.answer == "grounded answer"
    assert complete.await_count == 3
    assert provider.complete.await_count == 3


# ---------------------------------------------------------------------------
# Additional coverage: real hybrid-search fusion, agentic helpers, HyDE /
# multi-hop / fusion failure modes, reranking, and strategy dispatch branches
# that weren't previously exercised. A `ScriptedSession` fake dispatches to
# canned rows by matching the distinguishing SQL fragment of each of the four
# retrieval legs, so the fusion and RRF math run against real (if small)
# multi-leg overlap data instead of a single mocked `execute` return value.
# ---------------------------------------------------------------------------


class _FakeCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class ScriptedSession:
    """Routes `session.execute` calls to canned rows by SQL shape.

    Mirrors the four retrieval legs plus the collection metadata lookup and
    the app-side BM25 two-pass paging, so a single fake session can drive a
    full `hybrid_search` call across vector + FTS + trigram + BM25.
    """

    def __init__(
        self,
        *,
        embedding_dim_row=None,
        vector_rows=None,
        fts_rows=None,
        trgm_rows=None,
        bm25_corpus_count=0,
        bm25_pass1_page=None,
        bm25_pass2_page=None,
        raise_on="",
    ):
        self.embedding_dim_row = embedding_dim_row
        self.vector_rows = vector_rows or []
        self.fts_rows = fts_rows or []
        self.trgm_rows = trgm_rows or []
        self.bm25_corpus_count = bm25_corpus_count
        self.bm25_pass1_page = bm25_pass1_page or []
        self.bm25_pass2_page = bm25_pass2_page or []
        self.raise_on = raise_on
        self.calls: list[str] = []
        self._bm25_pass1_served = False
        self._bm25_pass2_served = False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append(sql)
        if self.raise_on and self.raise_on in sql:
            raise RuntimeError(f"boom:{self.raise_on}")
        if "embedding_dim FROM knowledge_collections" in sql:
            return _FakeCursorResult([self.embedding_dim_row] if self.embedding_dim_row else [])
        if "set_config" in sql:
            return _FakeCursorResult([])
        if "<=>" in sql:
            return _FakeCursorResult(self.vector_rows)
        if "ts_rank_cd" in sql:
            return _FakeCursorResult(self.fts_rows)
        if "similarity(content" in sql:
            return _FakeCursorResult(self.trgm_rows)
        if "count(*) FROM (SELECT 1 FROM" in sql:
            return _FakeCursorResult([(self.bm25_corpus_count,)])
        # Remaining calls are BM25's paginated leg. The columns list distinguishes
        # pass 1 (id, content) from pass 2 (id, content, metadata).
        if "metadata" in sql.split("FROM")[0]:
            if self._bm25_pass2_served:
                return _FakeCursorResult([])
            self._bm25_pass2_served = True
            return _FakeCursorResult(self.bm25_pass2_page)
        if self._bm25_pass1_served:
            return _FakeCursorResult([])
        self._bm25_pass1_served = True
        return _FakeCursorResult(self.bm25_pass1_page)


class TestAgenticParentCitations:
    @pytest.mark.asyncio
    async def test_unsupported_embedding_dim_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, load_agentic_parent_citations

        with pytest.raises(RetrievalStrategyExecutionError, match="agentic_chunking"):
            await load_agentic_parent_citations(
                AsyncMock(),
                child_chunk_ids=["c1"],
                collection_id="col-1",
                tenant_id="tenant-1",
                embedding_dim=999,
            )

    @pytest.mark.asyncio
    async def test_loads_parent_rows_keyed_by_child_id(self) -> None:
        from app.rag.engine import load_agentic_parent_citations

        session = AsyncMock()
        row_result = MagicMock()
        row_result.fetchall.return_value = [("child-1", "parent-1", "parent content here")]
        session.execute.return_value = row_result

        parents = await load_agentic_parent_citations(
            session,
            child_chunk_ids=["child-1"],
            collection_id="col-1",
            tenant_id="tenant-1",
            embedding_dim=1536,
        )

        assert parents["child-1"].parent_chunk_id == "parent-1"
        assert parents["child-1"].parent_content == "parent content here"


class TestExpandAgenticParentResults:
    def test_groups_propositions_under_shared_parent_and_dedupes(self) -> None:
        from app.rag.engine import (
            ParentWindowCitation,
            RetrievalResult,
            expand_agentic_parent_results,
        )

        results = [
            RetrievalResult(chunk_id="prop-1", content="fact one", score=0.9, source_metadata={}),
            RetrievalResult(chunk_id="prop-2", content="fact two", score=0.7, source_metadata={}),
        ]
        parents = {
            "prop-1": ParentWindowCitation("parent-1", "full parent window"),
            "prop-2": ParentWindowCitation("parent-1", "full parent window"),
        }

        expanded = expand_agentic_parent_results(results, parents, top_k=5)

        assert len(expanded) == 1
        merged = expanded[0]
        assert merged.chunk_id == "parent-1"
        assert merged.content == "full parent window"
        assert merged.score == 0.9
        assert len(merged.source_metadata["propositions"]) == 2

    def test_missing_parent_citation_raises(self) -> None:
        from app.rag.engine import (
            RetrievalResult,
            RetrievalStrategyExecutionError,
            expand_agentic_parent_results,
        )

        results = [
            RetrievalResult(chunk_id="prop-orphan", content="fact", score=0.5, source_metadata={})
        ]

        with pytest.raises(RetrievalStrategyExecutionError, match="no parent"):
            expand_agentic_parent_results(results, {}, top_k=5)


class TestMergeGroundingResults:
    def test_merges_duplicate_chunk_and_tracks_provenance(self) -> None:
        from app.rag.engine import RetrievalResult, merge_grounding_results

        group_a = [
            RetrievalResult(
                chunk_id="c1",
                content="shared evidence",
                score=0.6,
                source_metadata={"source": "vector"},
                retrieval_legs=["vector"],
            )
        ]
        group_b = [
            RetrievalResult(
                chunk_id="c1",
                content="shared evidence",
                score=0.8,
                source_metadata={"source": "web"},
                retrieval_legs=["web"],
            ),
            RetrievalResult(
                chunk_id="c2",
                content="unique evidence",
                score=0.4,
                source_metadata={"source": "web"},
                retrieval_legs=["web"],
            ),
        ]

        merged = merge_grounding_results([group_a, group_b], top_k=5)

        assert {r.chunk_id for r in merged} == {"c1", "c2"}
        top = next(r for r in merged if r.chunk_id == "c1")
        assert top.score == 0.8  # max() across duplicates
        assert set(top.retrieval_legs) == {"vector", "web"}
        assert len(top.source_metadata["merged_provenance"]) == 2

    def test_top_k_truncates_merged_results(self) -> None:
        from app.rag.engine import RetrievalResult, merge_grounding_results

        group = [
            RetrievalResult(chunk_id=f"c{i}", content="x", score=float(i), source_metadata={})
            for i in range(5)
        ]

        merged = merge_grounding_results([group], top_k=2)

        assert len(merged) == 2
        assert merged[0].score == 4.0


class TestBM25HeapEntryOrdering:
    def test_ties_break_on_chunk_id_descending(self) -> None:
        from app.rag.engine import _BM25HeapEntry

        low_id = _BM25HeapEntry(score=1.0, chunk_id="a", content="", source_metadata={})
        high_id = _BM25HeapEntry(score=1.0, chunk_id="b", content="", source_metadata={})
        # Equal score: heap wants the *smaller* chunk_id to sort "less" so that
        # heapq (a min-heap) evicts higher chunk_ids first among ties.
        assert (low_id < high_id) is False
        assert (high_id < low_id) is True

    def test_lower_score_sorts_lower(self) -> None:
        from app.rag.engine import _BM25HeapEntry

        assert _BM25HeapEntry(0.1, "a", "", {}) < _BM25HeapEntry(0.9, "b", "", {})


class TestFirstTaskGroupException:
    def test_unwraps_nested_exception_groups(self) -> None:
        from app.rag.engine import first_task_group_exception

        leaf = ValueError("root cause")
        inner_group = BaseExceptionGroup("inner", [leaf])
        outer_group = BaseExceptionGroup("outer", [inner_group])

        assert first_task_group_exception(outer_group) is leaf


class TestRunParallelSearches:
    @pytest.mark.asyncio
    async def test_preserves_request_order_regardless_of_completion_order(self) -> None:
        from app.rag.engine import RetrievalResult, _run_parallel_searches

        async def operation(query, embedding):
            del embedding
            if query == "slow":
                await asyncio.sleep(0.02)
            return [RetrievalResult(chunk_id=query, content=query, score=1.0, source_metadata={})]

        results = await _run_parallel_searches(
            [("slow", None), ("fast", None)],
            operation,
        )

        assert [r[0].chunk_id for r in results] == ["slow", "fast"]

    @pytest.mark.asyncio
    async def test_sibling_failure_propagates_first_concrete_exception(self) -> None:
        from app.rag.engine import _run_parallel_searches

        async def operation(query, embedding):
            del embedding
            if query == "bad":
                raise ValueError("leg failed")
            await asyncio.sleep(0.05)
            return []

        with pytest.raises(ValueError, match="leg failed"):
            await _run_parallel_searches(
                [("bad", None), ("good", None)],
                operation,
            )


class TestHybridSearchFullFusion:
    @pytest.mark.asyncio
    async def test_fuses_overlapping_and_unique_chunks_across_all_four_legs(self) -> None:
        """A chunk found by multiple legs should outrank one found by only one,
        even when that one leg gave it the single highest raw component score."""
        from app.rag.engine import hybrid_search

        session = ScriptedSession(
            vector_rows=[("c-multi", "multi-leg content", {"k": "v"}, 0.95)],
            fts_rows=[("c-multi", "multi-leg content", {"k": "v"}, 0.5)],
            trgm_rows=[("c-multi", "multi-leg content", {"k": "v"}, 0.5)],
            bm25_corpus_count=2,
            bm25_pass1_page=[("c-multi", "multi-leg content"), ("c-single", "single leg content")],
            bm25_pass2_page=[
                ("c-multi", "multi-leg content", {"k": "v"}),
                ("c-single", "single leg content", {}),
            ],
        )

        evidence: list[dict] = []
        results = await hybrid_search(
            session,
            query="multi leg content",
            query_embedding=[0.1, 0.2],
            collection_id="col-1",
            top_k=5,
            embedding_dim=1536,
            evidence=evidence,
        )

        assert results[0].chunk_id == "c-multi"
        assert set(results[0].retrieval_legs) == {"vector", "fts", "trigram", "bm25"}
        assert results[0].component_scores["vector"] == pytest.approx(0.95)
        # Recorded per-leg evidence for observability/debugging.
        components = {entry["component"] for entry in evidence}
        assert components == {"vector", "fts", "trigram", "bm25"}

    @pytest.mark.asyncio
    async def test_bm25_leg_skipped_for_large_corpus_and_evidence_says_so(self) -> None:
        from app.rag.engine import _BM25_MAX_CORPUS, hybrid_search

        session = ScriptedSession(bm25_corpus_count=_BM25_MAX_CORPUS + 1)
        evidence: list[dict] = []

        results = await hybrid_search(
            session,
            query="anything",
            query_embedding=None,
            collection_id="col-1",
            top_k=5,
            embedding_dim=1536,
            evidence=evidence,
        )

        assert results == []
        bm25_evidence = next(e for e in evidence if e["component"] == "bm25")
        assert bm25_evidence["skipped"] is True
        assert bm25_evidence["scoring_mode"] == "skipped_large_corpus"

    @pytest.mark.asyncio
    async def test_vector_leg_failure_is_swallowed_non_strict_other_legs_still_run(self) -> None:
        from app.rag.engine import hybrid_search

        session = ScriptedSession(
            raise_on="<=>",
            fts_rows=[("c1", "fts hit", {}, 0.6)],
        )

        results = await hybrid_search(
            session,
            query="policy",
            query_embedding=[0.1],
            collection_id="col-1",
            top_k=5,
            embedding_dim=1536,
        )

        assert [r.chunk_id for r in results] == ["c1"]
        assert results[0].retrieval_legs == ["fts"]

    @pytest.mark.asyncio
    async def test_embedding_dim_lookup_failure_defaults_non_strict(self) -> None:
        from app.rag.engine import hybrid_search

        session = AsyncMock()
        session.execute.side_effect = RuntimeError("collection metadata unavailable")

        results = await hybrid_search(
            session,
            query="policy",
            query_embedding=None,
            collection_id="col-1",
            retrieval_mode="lexical",
            top_k=5,
            # embedding_dim omitted -> triggers the metadata lookup, which raises.
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_embedding_dim_lookup_failure_raises_strict(self) -> None:
        from app.rag.engine import RetrievalLegExecutionError, hybrid_search

        session = AsyncMock()
        session.execute.side_effect = RuntimeError("collection metadata unavailable")

        with pytest.raises(RetrievalLegExecutionError, match="collection_metadata"):
            await hybrid_search(
                session,
                query="policy",
                query_embedding=None,
                collection_id="col-1",
                retrieval_mode="lexical",
                top_k=5,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_unsupported_embedding_dimension_short_circuits(self) -> None:
        from app.rag.engine import hybrid_search

        results = await hybrid_search(
            AsyncMock(),
            query="policy",
            query_embedding=[0.1],
            collection_id="col-1",
            embedding_dim=42,
            top_k=5,
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_strict_requires_embedding_for_hybrid_mode(self) -> None:
        from app.rag.engine import RetrievalLegExecutionError, hybrid_search

        with pytest.raises(RetrievalLegExecutionError, match="vector"):
            await hybrid_search(
                AsyncMock(),
                query="policy",
                query_embedding=None,
                collection_id="col-1",
                embedding_dim=1536,
                retrieval_mode="hybrid",
                strict=True,
            )


class TestBinaryPrefilterShortlist:
    @pytest.mark.asyncio
    async def test_disabled_by_default_skips_shortlist_query(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        session = AsyncMock()
        shortlist = await _binary_prefilter_shortlist(
            session,
            table="knowledge_chunks_1536",
            collection_id="col-1",
            embedding_dim=1536,
            query_embedding=[0.1],
            metadata_clause="",
            live_chunk_clause="",
            metadata_params={},
            top_k=10,
        )
        assert shortlist is None
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_halfvec_dimensions_are_never_prefiltered(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        fake_settings = MagicMock(rag_binary_prefilter_enabled=True)
        with patch("app.rag.engine.get_settings", return_value=fake_settings):
            shortlist = await _binary_prefilter_shortlist(
                AsyncMock(),
                table="knowledge_chunks_2048",
                collection_id="col-1",
                embedding_dim=2048,
                query_embedding=[0.1],
                metadata_clause="",
                live_chunk_clause="",
                metadata_params={},
                top_k=10,
            )
        assert shortlist is None

    @pytest.mark.asyncio
    async def test_enabled_large_collection_returns_shortlisted_ids(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        fake_settings = MagicMock(
            rag_binary_prefilter_enabled=True,
            rag_binary_prefilter_threshold=100,
            rag_binary_prefilter_shortlist=200,
        )

        session = AsyncMock()
        size_result = MagicMock()
        size_result.fetchone.return_value = (100_000,)
        shortlist_result = MagicMock()
        shortlist_result.fetchall.return_value = [("chunk-a",), ("chunk-b",)]

        nested_cm = AsyncMock()
        nested_cm.__aenter__.return_value = None
        nested_cm.__aexit__.return_value = None
        # `begin_nested()` itself is a plain sync call that returns an async
        # context manager — override the auto-AsyncMock so calling it doesn't
        # wrap the result in another coroutine.
        session.begin_nested = MagicMock(return_value=nested_cm)
        session.execute.side_effect = [size_result, shortlist_result]

        with patch("app.rag.engine.get_settings", return_value=fake_settings):
            shortlist = await _binary_prefilter_shortlist(
                session,
                table="knowledge_chunks_1536",
                collection_id="col-1",
                embedding_dim=1536,
                query_embedding=[0.1],
                metadata_clause="",
                live_chunk_clause="",
                metadata_params={},
                top_k=10,
            )

        assert shortlist == ["chunk-a", "chunk-b"]

    @pytest.mark.asyncio
    async def test_below_threshold_collection_skips_shortlist(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        fake_settings = MagicMock(
            rag_binary_prefilter_enabled=True,
            rag_binary_prefilter_threshold=100_000,
            rag_binary_prefilter_shortlist=200,
        )
        session = AsyncMock()
        size_result = MagicMock()
        size_result.fetchone.return_value = (10,)
        session.execute.return_value = size_result

        with patch("app.rag.engine.get_settings", return_value=fake_settings):
            shortlist = await _binary_prefilter_shortlist(
                session,
                table="knowledge_chunks_1536",
                collection_id="col-1",
                embedding_dim=1536,
                query_embedding=[0.1],
                metadata_clause="",
                live_chunk_clause="",
                metadata_params={},
                top_k=10,
            )
        assert shortlist is None

    @pytest.mark.asyncio
    async def test_savepoint_failure_falls_back_to_none_never_raises(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        fake_settings = MagicMock(
            rag_binary_prefilter_enabled=True,
            rag_binary_prefilter_threshold=1,
            rag_binary_prefilter_shortlist=200,
        )
        session = AsyncMock()
        size_result = MagicMock()
        size_result.fetchone.return_value = (100,)
        session.execute.return_value = size_result
        session.begin_nested.side_effect = RuntimeError("no bit index")

        with patch("app.rag.engine.get_settings", return_value=fake_settings):
            shortlist = await _binary_prefilter_shortlist(
                session,
                table="knowledge_chunks_1536",
                collection_id="col-1",
                embedding_dim=1536,
                query_embedding=[0.1],
                metadata_clause="",
                live_chunk_clause="",
                metadata_params={},
                top_k=10,
            )
        assert shortlist is None

    @pytest.mark.asyncio
    async def test_collection_size_lookup_failure_falls_back_to_none(self) -> None:
        from app.rag.engine import _binary_prefilter_shortlist

        fake_settings = MagicMock(rag_binary_prefilter_enabled=True)
        session = AsyncMock()
        session.execute.side_effect = RuntimeError("db down")

        with patch("app.rag.engine.get_settings", return_value=fake_settings):
            shortlist = await _binary_prefilter_shortlist(
                session,
                table="knowledge_chunks_1536",
                collection_id="col-1",
                embedding_dim=1536,
                query_embedding=[0.1],
                metadata_clause="",
                live_chunk_clause="",
                metadata_params={},
                top_k=10,
            )
        assert shortlist is None


class TestBM25SearchPersisted:
    @pytest.mark.asyncio
    async def test_scores_and_ranks_persisted_corpus(self) -> None:
        from app.rag.engine import _bm25_search_persisted

        session = ScriptedSession(
            bm25_pass1_page=[
                ("c1", "retention policy applies to all data"),
                ("c2", "unrelated content about weather"),
            ],
            bm25_pass2_page=[
                ("c1", "retention policy applies to all data", {"tag": "policy"}),
                ("c2", "unrelated content about weather", {}),
            ],
        )

        hits, trace = await _bm25_search_persisted(
            session,
            table="knowledge_chunks_1536",
            query="retention policy",
            collection_id="col-1",
            result_limit=10,
            metadata_clause="",
            live_chunk_clause="",
            metadata_params={},
        )

        assert hits[0].chunk_id == "c1"
        assert hits[0].score > 0
        assert trace["corpus_size"] == 2
        assert trace["scoring_mode"] == "application_okapi_bm25_two_pass_keyset"

    @pytest.mark.asyncio
    async def test_zero_result_limit_skips_scoring_pass(self) -> None:
        from app.rag.engine import _bm25_search_persisted

        session = ScriptedSession(
            bm25_pass1_page=[("c1", "retention policy")],
        )

        hits, trace = await _bm25_search_persisted(
            session,
            table="knowledge_chunks_1536",
            query="retention policy",
            collection_id="col-1",
            result_limit=0,
            metadata_clause="",
            live_chunk_clause="",
            metadata_params={},
        )

        assert hits == []
        assert trace["heap_capacity"] == 0

    @pytest.mark.asyncio
    async def test_heap_bounded_by_result_limit_keeps_top_scores(self) -> None:
        from app.rag.engine import _bm25_search_persisted

        pass1 = [(f"c{i}", "retention policy data governance") for i in range(5)]
        pass2 = [(f"c{i}", "retention policy data governance", {}) for i in range(5)]
        session = ScriptedSession(bm25_pass1_page=pass1, bm25_pass2_page=pass2)

        hits, trace = await _bm25_search_persisted(
            session,
            table="knowledge_chunks_1536",
            query="retention policy",
            collection_id="col-1",
            result_limit=2,
            metadata_clause="",
            live_chunk_clause="",
            metadata_params={},
        )

        assert len(hits) == 2
        assert trace["max_heap_size"] <= 2


class TestRerankResults:
    @pytest.mark.asyncio
    async def test_no_provider_returns_original_order_truncated(self) -> None:
        from app.rag.engine import RetrievalResult, rerank_results

        results = [
            RetrievalResult(chunk_id="c1", content="x", score=0.9, source_metadata={}),
            RetrievalResult(chunk_id="c2", content="y", score=0.8, source_metadata={}),
        ]

        out = await rerank_results(results, "query", provider=None, top_k=1)
        assert [r.chunk_id for r in out] == ["c1"]

    @pytest.mark.asyncio
    async def test_empty_results_short_circuits(self) -> None:
        from app.rag.engine import rerank_results

        out = await rerank_results([], "query", provider=AsyncMock())
        assert out == []

    @pytest.mark.asyncio
    async def test_provider_scores_reorder_candidates(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalResult, rerank_results

        results = [
            RetrievalResult(chunk_id="low", content="irrelevant", score=0.9, source_metadata={}),
            RetrievalResult(chunk_id="high", content="on topic", score=0.1, source_metadata={}),
        ]
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="[2, 9]", model="m")

        out = await rerank_results(results, "on topic query", provider=provider)

        assert out[0].chunk_id == "high"
        assert out[0].score == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_malformed_provider_response_falls_back_to_original_order(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalResult, rerank_results

        results = [
            RetrievalResult(chunk_id="c1", content="x", score=0.9, source_metadata={}),
            RetrievalResult(chunk_id="c2", content="y", score=0.8, source_metadata={}),
        ]
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="not json", model="m")

        out = await rerank_results(results, "query", provider=provider)

        assert [r.chunk_id for r in out] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_provider_exception_falls_back_to_original_order(self) -> None:
        from app.rag.engine import RetrievalResult, rerank_results

        results = [
            RetrievalResult(chunk_id="c1", content="x", score=0.9, source_metadata={}),
        ]
        provider = AsyncMock()
        provider.complete.side_effect = TimeoutError("embedding provider timed out")

        out = await rerank_results(results, "query", provider=provider)

        assert [r.chunk_id for r in out] == ["c1"]


class TestRetrieveHyde:
    @pytest.mark.asyncio
    async def test_empty_generated_document_raises_strict(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_hyde

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="   ", model="m")

        with pytest.raises(RetrievalStrategyExecutionError, match="empty"):
            await retrieve_hyde(
                AsyncMock(),
                query="what is retention policy",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=provider,
                model="m",
                embedder=AsyncMock(),
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_embedding_provider_failure_raises_strict(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_hyde

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="a doc", model="m")
        embedder = AsyncMock()
        embedder.embed.side_effect = TimeoutError("embedding provider timed out")

        with pytest.raises(RetrievalStrategyExecutionError, match="embedding failed"):
            await retrieve_hyde(
                AsyncMock(),
                query="what is retention policy",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=provider,
                model="m",
                embedder=embedder,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_generation_failure_non_strict_falls_back_to_hybrid(self) -> None:
        from app.rag.engine import retrieve_hyde

        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("provider unavailable")

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])) as hybrid:
            out = await retrieve_hyde(
                AsyncMock(),
                query="what is retention policy",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=provider,
                model="m",
                strict=False,
            )

        assert out == []
        hybrid.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_strict_requires_model(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_hyde

        with pytest.raises(RetrievalStrategyExecutionError, match="model"):
            await retrieve_hyde(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=AsyncMock(),
                model="",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_strict_requires_embedder(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_hyde

        with pytest.raises(RetrievalStrategyExecutionError, match="embedding provider"):
            await retrieve_hyde(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=AsyncMock(),
                model="m",
                embedder=None,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_hybrid_search_directly(self) -> None:
        from app.rag.engine import retrieve_hyde

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])) as hybrid:
            await retrieve_hyde(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=None,
            )
        hybrid.assert_awaited_once()


class TestRetrieveMultiHop:
    @pytest.mark.asyncio
    async def test_max_hops_out_of_range_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_multi_hop

        with pytest.raises(RetrievalStrategyExecutionError, match="max_hops"):
            await retrieve_multi_hop(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                provider=AsyncMock(),
                max_hops=99,
            )

    @pytest.mark.asyncio
    async def test_non_list_decomposition_non_strict_falls_back_to_single_hop(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content='"not a list"', model="m")

        async def fake_search(query, embedding):
            del embedding
            return []

        out = await retrieve_multi_hop(
            None,
            query="compare A and B",
            query_embedding=None,
            collection_id="col-1",
            provider=provider,
            model="m",
            search_operation=fake_search,
            strict=False,
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_decomposition_matching_only_original_query_falls_back_non_strict(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content='["compare A and B"]', model="m"
        )

        async def fake_search(query, embedding):
            del embedding
            assert query == "compare A and B"
            return []

        out = await retrieve_multi_hop(
            None,
            query="compare A and B",
            query_embedding=None,
            collection_id="col-1",
            provider=provider,
            model="m",
            search_operation=fake_search,
            strict=False,
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_strict_decomposition_failure_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("provider down")

        with pytest.raises(RetrievalStrategyExecutionError, match="decomposition failed"):
            await retrieve_multi_hop(
                AsyncMock(),
                query="compare A and B",
                query_embedding=None,
                collection_id="col-1",
                provider=provider,
                model="m",
                embedder=AsyncMock(),
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_hop_embedding_failure_non_strict_reuses_query_embedding(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalResult, retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content='["sub-query one", "sub-query two"]', model="m"
        )
        embedder = AsyncMock()
        embedder.embed.side_effect = RuntimeError("embedding provider timed out")

        seen_embeddings = []

        async def fake_search(query, embedding):
            seen_embeddings.append(embedding)
            return [
                RetrievalResult(chunk_id=query, content=query, score=1.0, source_metadata={})
            ]

        out = await retrieve_multi_hop(
            None,
            query="compare A and B",
            query_embedding=[0.5],
            collection_id="col-1",
            provider=provider,
            model="m",
            embedder=embedder,
            search_operation=fake_search,
            strict=False,
        )

        assert seen_embeddings == [[0.5], [0.5]]
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_hop_embedding_failure_strict_raises(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content='["sub-query one", "sub-query two"]', model="m"
        )
        embedder = AsyncMock()
        embedder.embed.side_effect = RuntimeError("embedding provider timed out")

        with pytest.raises(RetrievalStrategyExecutionError, match="hop embedding failed"):
            await retrieve_multi_hop(
                AsyncMock(),
                query="compare A and B",
                query_embedding=[0.5],
                collection_id="col-1",
                provider=provider,
                model="m",
                embedder=embedder,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_sequential_hop_failure_non_strict_degrades_instead_of_crashing(self) -> None:
        """Regression: a non-strict hop failure in the sequential fallback path
        (no `search_operation` supplied) used to silently drop that hop's slot
        from `per_hop_results`, so the later `zip(..., strict=True)` calls raised
        `ValueError: zip() argument 2 is shorter than argument 1` instead of
        degrading gracefully — turning one bad leg into a hard crash for the
        whole multi-hop query. It must now return the other hop's evidence."""
        from app.providers.base import CompletionResponse
        from app.rag.engine import retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content='["hop one", "hop two"]', model="m"
        )

        async def flaky_hybrid_search(*args, **kwargs):
            del args
            if kwargs.get("query") == "hop one":
                raise RuntimeError("leg failed")
            return []

        with patch("app.rag.engine.hybrid_search", side_effect=flaky_hybrid_search):
            out = await retrieve_multi_hop(
                AsyncMock(),
                query="compare A and B",
                query_embedding=None,
                collection_id="col-1",
                provider=provider,
                model="m",
                strict=False,
            )

        assert out == []

    @pytest.mark.asyncio
    async def test_no_provider_and_no_session_returns_empty(self) -> None:
        from app.rag.engine import retrieve_multi_hop

        out = await retrieve_multi_hop(
            None,
            query="q",
            query_embedding=None,
            collection_id="col-1",
            provider=None,
            strict=False,
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_merges_hop_results_and_tracks_hop_queries(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalResult, retrieve_multi_hop

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content='["hop one", "hop two"]', model="m"
        )

        async def fake_search(query, embedding):
            del embedding
            if query == "hop one":
                return [
                    RetrievalResult(
                        chunk_id="shared", content="c", score=0.5, source_metadata={}
                    )
                ]
            return [
                RetrievalResult(chunk_id="shared", content="c", score=0.9, source_metadata={}),
                RetrievalResult(chunk_id="unique", content="u", score=0.3, source_metadata={}),
            ]

        out = await retrieve_multi_hop(
            None,
            query="compare A and B",
            query_embedding=None,
            collection_id="col-1",
            provider=provider,
            model="m",
            top_k=10,
            search_operation=fake_search,
        )

        shared = next(r for r in out if r.chunk_id == "shared")
        assert shared.score == 0.9
        assert set(shared.source_metadata["hop_queries"]) == {"hop one", "hop two"}


class TestRecallLongTermMemory:
    @pytest.mark.asyncio
    async def test_no_memory_or_tenant_returns_empty(self) -> None:
        from app.rag.engine import recall_long_term_memory

        out = await recall_long_term_memory(
            None, query="q", tenant_ctx=MagicMock(), top_k=5
        )
        assert out == []

        out2 = await recall_long_term_memory(
            MagicMock(), query="q", tenant_ctx=None, top_k=5
        )
        assert out2 == []

    @pytest.mark.asyncio
    async def test_maps_ltm_entries_to_retrieval_results(self) -> None:
        from app.rag.engine import recall_long_term_memory

        entry = MagicMock(
            memory_id="mem-1",
            content="past learning",
            confidence=0.85,
            memory_type="lesson",
            source_goal_id="goal-1",
        )
        ltm = AsyncMock()
        ltm.recall_async.return_value = [entry]

        out = await recall_long_term_memory(
            ltm, query="q", tenant_ctx=MagicMock(tenant_id="t1"), top_k=5
        )

        assert out[0].chunk_id == "ltm_mem-1"
        assert out[0].content == "past learning"
        assert out[0].score == pytest.approx(0.85)
        assert out[0].retrieval_legs == ["long_term_memory"]

    @pytest.mark.asyncio
    async def test_recall_failure_non_strict_returns_empty(self) -> None:
        from app.rag.engine import recall_long_term_memory

        ltm = AsyncMock()
        ltm.recall_async.side_effect = RuntimeError("memory store unavailable")

        out = await recall_long_term_memory(
            ltm, query="q", tenant_ctx=MagicMock(), top_k=5, strict=False
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_recall_failure_strict_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, recall_long_term_memory

        ltm = AsyncMock()
        ltm.recall_async.side_effect = RuntimeError("memory store unavailable")

        with pytest.raises(RetrievalStrategyExecutionError, match="memory_augmented"):
            await recall_long_term_memory(
                ltm, query="q", tenant_ctx=MagicMock(), top_k=5, strict=True
            )

    @pytest.mark.asyncio
    async def test_empty_recall_returns_empty_list(self) -> None:
        from app.rag.engine import recall_long_term_memory

        ltm = AsyncMock()
        ltm.recall_async.return_value = []

        out = await recall_long_term_memory(
            ltm, query="q", tenant_ctx=MagicMock(), top_k=5
        )
        assert out == []


class TestRetrieveStrategyDispatch:
    @pytest.mark.asyncio
    async def test_parametric_strategy_skips_retrieval_entirely(self) -> None:
        from app.rag.engine import retrieve

        out = await retrieve(
            AsyncMock(),
            query="q",
            query_embedding=None,
            collection_id="col-1",
            strategy="parametric",
        )
        assert out == []

    @pytest.mark.asyncio
    async def test_memory_strategy_dispatches_to_long_term_memory_recall(self) -> None:
        from app.rag.engine import retrieve

        ltm = AsyncMock()
        entry = MagicMock(memory_id="m1", content="c", confidence=0.5)
        ltm.recall_async.return_value = [entry]

        out = await retrieve(
            AsyncMock(),
            query="q",
            query_embedding=None,
            collection_id="col-1",
            strategy="memory",
            long_term_memory=ltm,
            tenant_ctx=MagicMock(),
        )
        assert out[0].chunk_id == "ltm_m1"

    @pytest.mark.asyncio
    async def test_corrective_strategy_flags_low_confidence_results(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        low_conf_results = [
            RetrievalResult(chunk_id="c1", content="x", score=0.1, source_metadata={})
        ]
        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=low_conf_results)):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="corrective",
            )
        assert out[0].source_metadata["corrective_flagged"] is True

    @pytest.mark.asyncio
    async def test_corrective_strategy_non_strict_falls_back_on_error(self) -> None:
        from app.rag.engine import retrieve

        with patch(
            "app.rag.engine.hybrid_search",
            AsyncMock(side_effect=[RuntimeError("boom"), []]),
        ) as hybrid:
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="corrective",
                strict=False,
            )
        assert out == []
        assert hybrid.await_count == 2

    @pytest.mark.asyncio
    async def test_corrective_strategy_strict_raises_on_error(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve

        with patch(
            "app.rag.engine.hybrid_search",
            AsyncMock(side_effect=RuntimeError("boom")),
        ), pytest.raises(RetrievalStrategyExecutionError, match="corrective"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="corrective",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_graph_strategy_strict_is_not_wired_and_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve

        with pytest.raises(RetrievalStrategyExecutionError, match="graph"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="graph",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_graph_strategy_non_strict_falls_back_to_hybrid(self) -> None:
        from app.rag.engine import retrieve

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])) as hybrid:
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="graph",
                strict=False,
            )
        assert out == []
        hybrid.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raptor_strategy_tags_results_and_filters_by_rag_strategy(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        observed_filters = []

        async def hybrid(*args, **kwargs):
            del args
            observed_filters.append(kwargs.get("metadata_filter"))
            return base

        with patch("app.rag.engine.hybrid_search", side_effect=hybrid):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="raptor",
            )
        assert out[0].source_metadata["strategy"] == "raptor"
        assert "raptor" in out[0].retrieval_legs
        assert observed_filters[0]["rag_strategy"] == "raptor"

    @pytest.mark.asyncio
    async def test_raptor_strategy_wraps_any_failure(self) -> None:
        """raptor always raises internally on failure; `strict=True` is required
        for it to escape `retrieve()`'s outer non-strict fallback handler."""
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve

        with patch(
            "app.rag.engine.hybrid_search", AsyncMock(side_effect=RuntimeError("boom"))
        ), pytest.raises(RetrievalStrategyExecutionError, match="raptor"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="raptor",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_raptor_strategy_failure_non_strict_falls_back_to_plain_hybrid(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        fallback = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]

        with patch(
            "app.rag.engine.hybrid_search",
            AsyncMock(side_effect=[RuntimeError("boom"), fallback]),
        ) as hybrid:
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="raptor",
                strict=False,
            )
        assert out == fallback
        assert hybrid.await_count == 2

    @pytest.mark.asyncio
    async def test_agentic_chunking_requires_tenant_context(self) -> None:
        from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with patch(
            "app.rag.engine.hybrid_search", AsyncMock(return_value=base)
        ), pytest.raises(RetrievalStrategyExecutionError, match="tenant-scoped"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="agentic_chunking",
                embedding_dim=1536,
                tenant_ctx=None,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_agentic_chunking_empty_base_results_returns_empty(self) -> None:
        from app.rag.engine import retrieve

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="agentic_chunking",
                embedding_dim=1536,
                tenant_ctx=MagicMock(tenant_id="t1"),
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_agentic_chunking_full_success_expands_parents(self) -> None:
        from app.rag.engine import ParentWindowCitation, RetrievalResult, retrieve

        base = [
            RetrievalResult(chunk_id="prop-1", content="fact", score=0.9, source_metadata={})
        ]
        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)),
            patch(
                "app.rag.engine.load_agentic_parent_citations",
                AsyncMock(
                    return_value={"prop-1": ParentWindowCitation("parent-1", "parent text")}
                ),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="agentic_chunking",
                embedding_dim=1536,
                tenant_ctx=MagicMock(tenant_id="t1"),
            )
        assert out[0].chunk_id == "parent-1"
        assert out[0].content == "parent text"

    @pytest.mark.asyncio
    async def test_default_path_applies_reranking_stage(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)),
            patch(
                "app.rag.engine.apply_default_rerank", AsyncMock(return_value=list(reversed(base)))
            ) as rerank,
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="direct",
            )
        rerank.assert_awaited_once()
        assert out == list(reversed(base))

    @pytest.mark.asyncio
    async def test_naive_strategy_forces_vector_only_mode(self) -> None:
        from app.rag.engine import retrieve

        observed_modes = []

        async def hybrid(*args, **kwargs):
            del args
            observed_modes.append(kwargs.get("retrieval_mode"))
            return []

        with (
            patch("app.rag.engine.hybrid_search", side_effect=hybrid),
            patch("app.rag.engine.apply_default_rerank", AsyncMock(return_value=[])),
        ):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="naive",
            )
        assert observed_modes == ["vector"]

    @pytest.mark.asyncio
    async def test_strategy_auto_selection_failure_is_not_caught_by_dispatch(self) -> None:
        """Strategy auto-selection happens before the try/except that guards the
        rest of dispatch, so a planner failure always propagates — even non-strict."""
        from app.rag.engine import retrieve

        with patch(
            "app.rag.engine.RetrievalPlanner.select_strategy",
            side_effect=RuntimeError("planner exploded"),
        ), pytest.raises(RuntimeError, match="planner exploded"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy=None,
                strict=False,
            )

    @pytest.mark.asyncio
    async def test_malformed_query_still_dispatches_without_crashing(self) -> None:
        """A pathological/empty query should not crash strategy selection or the
        default retrieval path."""
        from app.rag.engine import retrieve

        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])),
            patch("app.rag.engine.apply_default_rerank", AsyncMock(return_value=[])),
        ):
            out = await retrieve(
                AsyncMock(),
                query="",
                query_embedding=None,
                collection_id="col-1",
            )
        assert out == []


class TestRetrieveFusionCoverage:
    @pytest.mark.asyncio
    async def test_max_variants_out_of_range_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

        with pytest.raises(RetrievalStrategyExecutionError, match="max_variants"):
            await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                max_variants=99,
            )

    @pytest.mark.asyncio
    async def test_strict_requires_model_when_provider_given(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

        with pytest.raises(RetrievalStrategyExecutionError, match="LLM model"):
            await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=AsyncMock(),
                model="",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_strict_requires_embedding_source(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

        with pytest.raises(RetrievalStrategyExecutionError, match="embedding provider"):
            await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                embedder=None,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_expander_exception_non_strict_falls_back_to_rule_based(self) -> None:
        from app.rag.engine import retrieve_fusion

        provider = AsyncMock()
        with (
            patch(
                "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion_async",
                side_effect=RuntimeError("llm expansion broke"),
            ),
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])),
        ):
            out = await retrieve_fusion(
                AsyncMock(),
                query="authentication error",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=provider,
                model="m",
                strict=False,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_expander_strategy_error_passes_through_untouched(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

        provider = AsyncMock()
        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion_async",
            side_effect=RetrievalStrategyExecutionError("fusion", "nested failure"),
        ), pytest.raises(RetrievalStrategyExecutionError, match="nested failure"):
            await retrieve_fusion(
                AsyncMock(),
                query="authentication error",
                query_embedding=[0.1],
                collection_id="col-1",
                provider=provider,
                model="m",
                strict=False,
            )

    @pytest.mark.asyncio
    async def test_single_variant_expansion_strict_raises(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve_fusion

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["only one variant"],
        ), pytest.raises(RetrievalStrategyExecutionError, match="one variant"):
            await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_variant_embedding_failure_non_strict_reuses_query_embedding(self) -> None:
        from app.rag.engine import retrieve_fusion

        embedder = AsyncMock()
        embedder.embed.side_effect = RuntimeError("embedding provider timed out")

        with (
            patch(
                "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
                return_value=["variant-1", "variant-2"],
            ),
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])),
        ):
            out = await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                embedder=embedder,
                strict=False,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_legacy_variant_failure_non_strict_logs_and_returns_empty(self) -> None:
        from app.rag.engine import retrieve_fusion

        with (
            patch(
                "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
                return_value=["variant-1", "variant-2"],
            ),
            patch(
                "app.rag.engine.hybrid_search",
                AsyncMock(side_effect=RuntimeError("db unavailable")),
            ),
        ):
            out = await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strict=False,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_legacy_path_requires_session_when_no_search_operation(self) -> None:
        """`_retrieve_legacy`'s missing-session guard sits outside its own
        try/except, so it always raises regardless of `strict`."""
        from app.rag.engine import retrieve_fusion

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["variant-1", "variant-2"],
        ), pytest.raises(ValueError, match="session is required"):
            await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strict=False,
            )

    @pytest.mark.asyncio
    async def test_merges_duplicate_chunks_across_variants_with_rrf(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve_fusion

        async def search_operation(variant, embedding):
            del embedding
            if variant == "v1":
                return [
                    RetrievalResult(chunk_id="shared", content="c", score=0.5, source_metadata={})
                ]
            return [
                RetrievalResult(chunk_id="shared", content="c", score=0.5, source_metadata={}),
                RetrievalResult(chunk_id="only-v2", content="u", score=0.4, source_metadata={}),
            ]

        strategy_evidence: list[dict] = []
        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2"],
        ):
            out = await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                search_operation=search_operation,
                strategy_evidence=strategy_evidence,
            )

        shared = next(r for r in out if r.chunk_id == "shared")
        assert shared.source_metadata["fusion_queries"] == ["v1", "v2"]
        assert strategy_evidence  # per-variant evidence recorded


class TestRetrieveProviderPatternDispatch:
    @pytest.mark.asyncio
    async def test_flare_strategy_success_replaces_first_result(self) -> None:
        from app.providers.base import CompletionResponse
        from app.rag.engine import RetrievalResult, retrieve

        base = [
            RetrievalResult(chunk_id="c1", content="evidence one", score=0.6, source_metadata={}),
        ]
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="grounded", model="m")

        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)),
            patch(
                "app.rag.agentic.patterns.flare.FLAREPattern.execute",
                AsyncMock(return_value="refined FLARE answer"),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="flare",
                provider=provider,
                model="m",
            )

        assert out[0].content == "refined FLARE answer"
        assert "flare" in out[0].retrieval_legs

    @pytest.mark.asyncio
    async def test_flare_strategy_empty_base_results_short_circuits(self) -> None:
        from app.rag.engine import retrieve

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="flare",
                provider=AsyncMock(),
                model="m",
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_self_rag_strategy_success_replaces_first_result(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [
            RetrievalResult(chunk_id="c1", content="evidence one", score=0.6, source_metadata={}),
        ]
        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)),
            patch(
                "app.rag.agentic.patterns.self_rag.SelfRAGPattern.execute",
                AsyncMock(return_value="refined self-rag answer"),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="self_rag",
                provider=AsyncMock(),
                model="m",
            )
        assert out[0].content == "refined self-rag answer"

    @pytest.mark.asyncio
    async def test_flare_pattern_failure_non_strict_falls_back_to_hybrid(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]

        with (
            patch(
                "app.rag.engine.hybrid_search",
                AsyncMock(side_effect=[base, []]),
            ) as hybrid,
            patch(
                "app.rag.agentic.patterns.flare.FLAREPattern.execute",
                AsyncMock(side_effect=RuntimeError("pattern exploded")),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="flare",
                provider=AsyncMock(),
                model="m",
                strict=False,
            )
        assert out == []
        assert hybrid.await_count == 2

    @pytest.mark.asyncio
    async def test_flare_retrieve_callback_wraps_hybrid_search(self) -> None:
        """Exercise the FLARE `_flare_retrieve` closure directly by letting the
        real FLAREPattern.execute call back into it."""
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="evidence one", score=0.6, source_metadata={})]
        extra = [RetrievalResult(chunk_id="c2", content="extra context", score=0.4, source_metadata={})]

        captured_retrieve_fn = {}

        async def fake_execute(*, query, provider, retrieve_fn, model, strict):
            del query, provider, model, strict
            captured_retrieve_fn["fn"] = retrieve_fn
            return await retrieve_fn("follow-up question")

        with (
            patch(
                "app.rag.engine.hybrid_search",
                AsyncMock(side_effect=[base, extra]),
            ),
            patch(
                "app.rag.agentic.patterns.flare.FLAREPattern.execute",
                side_effect=fake_execute,
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="flare",
                provider=AsyncMock(),
                model="m",
            )

        assert out[0].content == "extra context"
        assert "fn" in captured_retrieve_fn

    @pytest.mark.asyncio
    async def test_speculative_strategy_no_provider_returns_base_results(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="speculative",
                provider=None,
            )
        assert out == base

    @pytest.mark.asyncio
    async def test_speculative_strategy_success_replaces_first_result(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with (
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=base)),
            patch(
                "app.rag.agentic.patterns.speculative.SpeculativeRAGPattern.execute",
                AsyncMock(return_value="best speculative answer"),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="speculative",
                provider=AsyncMock(),
                model="m",
            )
        assert out[0].content == "best speculative answer"
        assert out[0].score == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_speculative_strategy_strict_requires_provider(self) -> None:
        from app.rag.engine import RetrievalStrategyExecutionError, retrieve

        with patch(
            "app.rag.engine.hybrid_search", AsyncMock(return_value=[])
        ), pytest.raises(RetrievalStrategyExecutionError, match="speculative"):
            await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="speculative",
                provider=None,
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_speculative_pattern_failure_non_strict_falls_back(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with (
            patch(
                "app.rag.engine.hybrid_search",
                AsyncMock(side_effect=[base, []]),
            ),
            patch(
                "app.rag.agentic.patterns.speculative.SpeculativeRAGPattern.execute",
                AsyncMock(side_effect=RuntimeError("speculative pattern exploded")),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="speculative",
                provider=AsyncMock(),
                model="m",
                strict=False,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_colbert_strategy_failure_non_strict_falls_back_to_hybrid(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        base = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with (
            patch(
                "app.rag.engine.hybrid_search",
                AsyncMock(side_effect=[base, []]),
            ) as hybrid,
            patch(
                "app.rag.agentic.patterns.colbert.ColBERTPattern.rerank_async",
                AsyncMock(side_effect=RuntimeError("colbert unavailable")),
            ),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="colbert",
                strict=False,
            )
        assert out == []
        assert hybrid.await_count == 2

    @pytest.mark.asyncio
    async def test_colbert_strategy_empty_base_results_short_circuits(self) -> None:
        from app.rag.engine import retrieve

        with patch("app.rag.engine.hybrid_search", AsyncMock(return_value=[])):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strategy="colbert",
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_graph_strategy_import_failure_non_strict_falls_back(self) -> None:
        from app.rag.engine import RetrievalResult, retrieve

        fallback = [RetrievalResult(chunk_id="c1", content="x", score=0.5, source_metadata={})]
        with (
            patch.dict("sys.modules", {"app.state_runtime.kg_query_engine": None}),
            patch("app.rag.engine.hybrid_search", AsyncMock(return_value=fallback)),
        ):
            out = await retrieve(
                AsyncMock(),
                query="q",
                query_embedding=None,
                collection_id="col-1",
                strategy="graph",
                strict=False,
            )
        assert out == fallback
