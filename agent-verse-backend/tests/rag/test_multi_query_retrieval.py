"""Dedicated coverage for multi-query fan-out retrieval.

There is no module distinctly named for "multi-query retrieval" -- the
generation of query variants lives in
``app.rag.agentic.query_expander.QueryExpander`` and the fan-out +
RRF-merge over those variants lives in ``app.rag.engine.retrieve_fusion``
(``app.rag.agentic.patterns.fusion.FusionRAGPattern`` is a thin dispatch
wrapper around it). Before this file, ``QueryExpander`` had only two smoke
assertions (``tests/rag/test_agentic/test_layer4_complete.py``) and
``retrieve_fusion`` had broad coverage folded into
``tests/rag/test_retrieval_engine.py``'s general fusion-strategy-error
tests, but nothing isolated the multi-query fan-out mechanics themselves:
each variant actually being executed, dedup/merge across variants, one
variant failing while others still contribute, and the all-empty case.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.base import CompletionResponse
from app.rag.agentic.query_expander import QueryExpander
from app.rag.engine import RetrievalResult, retrieve_fusion


# ---------------------------------------------------------------------------
# QueryExpander.expand_for_fusion -- rule-based multi-query generation
# ---------------------------------------------------------------------------


class TestQueryExpanderRuleBasedVariants:
    def test_original_query_is_always_first_variant(self) -> None:
        variants = QueryExpander().expand_for_fusion("authentication flow error")
        assert variants[0] == "authentication flow error"

    def test_synonym_expansion_generates_distinct_variants(self) -> None:
        variants = QueryExpander().expand_for_fusion("authentication error", max_variants=4)
        assert "login error" in variants
        assert len(variants) > 1

    def test_stopword_stripped_variant_is_appended_when_it_differs(self) -> None:
        variants = QueryExpander().expand_for_fusion("the deploy of an app", max_variants=4)
        assert "deploy app" in variants

    def test_no_matching_synonyms_or_stopwords_returns_single_variant(self) -> None:
        variants = QueryExpander().expand_for_fusion("simple query with no matches")
        assert variants == ["simple query with no matches"]

    def test_max_variants_caps_output_length(self) -> None:
        variants = QueryExpander().expand_for_fusion("authentication flow error deploy", max_variants=2)
        assert len(variants) == 2

    def test_variants_are_deduplicated(self) -> None:
        variants = QueryExpander().expand_for_fusion("error error error", max_variants=10)
        assert len(variants) == len(set(variants))

    def test_blank_variants_are_filtered_out(self) -> None:
        variants = QueryExpander().expand_for_fusion("   ", max_variants=4)
        assert all(v.strip() for v in variants)


# ---------------------------------------------------------------------------
# QueryExpander.expand_for_fusion_async -- LLM-driven multi-query generation
# ---------------------------------------------------------------------------


class TestQueryExpanderAsyncVariants:
    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_rule_based(self) -> None:
        expander = QueryExpander()
        variants = await expander.expand_for_fusion_async("authentication error", provider=None)
        assert variants == expander.expand_for_fusion("authentication error")

    @pytest.mark.asyncio
    async def test_provider_response_lines_become_variants(self) -> None:
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content="how do I authenticate\nlogin troubleshooting\nauth failure causes",
            model="m",
        )
        variants = await QueryExpander().expand_for_fusion_async(
            "authentication error", provider=provider, model="m"
        )
        assert variants[0] == "authentication error"
        assert "how do I authenticate" in variants
        assert "login troubleshooting" in variants
        assert "auth failure causes" in variants

    @pytest.mark.asyncio
    async def test_provider_duplicate_of_original_query_is_not_repeated(self) -> None:
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content="authentication error\nlogin troubleshooting",
            model="m",
        )
        variants = await QueryExpander().expand_for_fusion_async(
            "authentication error", provider=provider, model="m"
        )
        assert variants.count("authentication error") == 1

    @pytest.mark.asyncio
    async def test_max_variants_caps_llm_output(self) -> None:
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content="v1\nv2\nv3\nv4\nv5",
            model="m",
        )
        variants = await QueryExpander().expand_for_fusion_async(
            "q", provider=provider, model="m", max_variants=2
        )
        assert len(variants) == 2

    @pytest.mark.asyncio
    async def test_provider_failure_non_strict_falls_back_to_rule_based(self) -> None:
        expander = QueryExpander()
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("LLM timed out")
        variants = await expander.expand_for_fusion_async(
            "authentication error", provider=provider, model="m", strict=False
        )
        assert variants == expander.expand_for_fusion("authentication error")

    @pytest.mark.asyncio
    async def test_provider_failure_strict_raises(self) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("LLM timed out")
        with pytest.raises(RuntimeError, match="LLM timed out"):
            await QueryExpander().expand_for_fusion_async(
                "authentication error", provider=provider, model="m", strict=True
            )


# ---------------------------------------------------------------------------
# retrieve_fusion: each variant is actually executed
# ---------------------------------------------------------------------------


class TestMultiQueryFanOutExecutesEveryVariant:
    @pytest.mark.asyncio
    async def test_search_operation_called_once_per_variant(self) -> None:
        calls: list[str] = []

        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            calls.append(variant)
            return []

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2", "v3"],
        ):
            await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                search_operation=search_operation,
            )
        assert sorted(calls) == ["v1", "v2", "v3"]

    @pytest.mark.asyncio
    async def test_each_variant_receives_its_own_embedding(self) -> None:
        seen: dict[str, list[float] | None] = {}

        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            seen[variant] = embedding
            return []

        embedder = AsyncMock()

        async def fake_embed(request: Any) -> Any:
            from app.providers.base import EmbedResponse

            text = request.texts[0]
            return EmbedResponse(embeddings=[[len(text) * 1.0]])

        embedder.embed.side_effect = fake_embed

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["short", "a-longer-variant"],
        ):
            await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                embedder=embedder,
                search_operation=search_operation,
            )
        assert seen["short"] == [len("short") * 1.0]
        assert seen["a-longer-variant"] == [len("a-longer-variant") * 1.0]
        assert seen["short"] != seen["a-longer-variant"]


# ---------------------------------------------------------------------------
# retrieve_fusion: merge/dedupe correctness
# ---------------------------------------------------------------------------


class TestMultiQueryMergeAndDedup:
    @pytest.mark.asyncio
    async def test_result_seen_in_multiple_variants_is_not_duplicated(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            return [RetrievalResult(chunk_id="shared", content="c", score=0.5, source_metadata={})]

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2", "v3"],
        ):
            out = await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                search_operation=search_operation,
            )
        assert len(out) == 1
        assert out[0].source_metadata["fusion_queries"] == ["v1", "v2", "v3"]

    @pytest.mark.asyncio
    async def test_rrf_score_rewards_chunk_ranked_high_across_more_variants(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            if variant == "v1":
                return [
                    RetrievalResult(chunk_id="popular", content="c", score=0.9, source_metadata={}),
                    RetrievalResult(chunk_id="niche", content="c", score=0.1, source_metadata={}),
                ]
            return [RetrievalResult(chunk_id="popular", content="c", score=0.9, source_metadata={})]

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
            )
        by_id = {r.chunk_id: r for r in out}
        assert by_id["popular"].rrf_score > by_id["niche"].rrf_score
        # popular appears at rank 1 in both variants -> 2 * 1/(60+1)
        assert by_id["popular"].rrf_score == pytest.approx(2 / 61)
        # niche appears at rank 2 in v1 only -> 1/(60+2)
        assert by_id["niche"].rrf_score == pytest.approx(1 / 62)

    @pytest.mark.asyncio
    async def test_top_k_truncates_merged_results(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            return [
                RetrievalResult(chunk_id=f"{variant}-{i}", content="c", score=1.0, source_metadata={})
                for i in range(5)
            ]

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2"],
        ):
            out = await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                top_k=3,
                search_operation=search_operation,
            )
        assert len(out) == 3

    @pytest.mark.asyncio
    async def test_retrieval_legs_from_all_contributing_variants_are_preserved(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            return [
                RetrievalResult(
                    chunk_id="shared",
                    content="c",
                    score=0.5,
                    source_metadata={},
                    retrieval_legs=[f"leg_{variant}"],
                )
            ]

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
            )
        assert set(out[0].retrieval_legs) == {"leg_v1", "leg_v2"}


# ---------------------------------------------------------------------------
# retrieve_fusion: partial failure -- one variant fails, others still contribute
# ---------------------------------------------------------------------------


class TestMultiQueryPartialFailure:
    @pytest.mark.asyncio
    async def test_one_variant_legacy_search_failure_does_not_drop_the_others(self) -> None:
        """Non-strict legacy path: hybrid_search raises for one variant's
        query text but succeeds for another -- the surviving variant's
        results must still come through."""
        from app.rag.engine import RetrievalResult as _RR

        async def fake_hybrid_search(*, query: str, **kwargs: Any) -> list[_RR]:
            if query == "bad-variant":
                raise RuntimeError("search backend timeout")
            return [_RR(chunk_id=f"result-for-{query}", content="c", score=0.5, source_metadata={})]

        with (
            patch(
                "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
                return_value=["bad-variant", "good-variant"],
            ),
            patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid_search),
        ):
            out = await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strict=False,
            )
        assert len(out) == 1
        assert out[0].chunk_id == "result-for-good-variant"

    @pytest.mark.asyncio
    async def test_strategy_evidence_records_zero_results_for_the_failed_variant(self) -> None:
        from app.rag.engine import RetrievalResult as _RR

        async def fake_hybrid_search(*, query: str, **kwargs: Any) -> list[_RR]:
            if query == "bad-variant":
                raise RuntimeError("search backend timeout")
            return [_RR(chunk_id="ok", content="c", score=0.5, source_metadata={})]

        strategy_evidence: list[dict[str, Any]] = []
        with (
            patch(
                "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
                return_value=["bad-variant", "good-variant"],
            ),
            patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid_search),
        ):
            await retrieve_fusion(
                AsyncMock(),
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                strict=False,
                strategy_evidence=strategy_evidence,
            )
        bad = next(item for item in strategy_evidence if item["query"] == "bad-variant")
        assert bad["result_count"] == 0


# ---------------------------------------------------------------------------
# retrieve_fusion: all variants return empty
# ---------------------------------------------------------------------------


class TestMultiQueryAllVariantsEmpty:
    @pytest.mark.asyncio
    async def test_all_variants_empty_returns_empty_list(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            return []

        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2", "v3"],
        ):
            out = await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                search_operation=search_operation,
            )
        assert out == []

    @pytest.mark.asyncio
    async def test_all_variants_empty_still_records_strategy_evidence_per_variant(self) -> None:
        async def search_operation(variant: str, embedding: list[float] | None) -> list[RetrievalResult]:
            return []

        strategy_evidence: list[dict[str, Any]] = []
        with patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["v1", "v2"],
        ):
            await retrieve_fusion(
                None,
                query="q",
                query_embedding=[0.1],
                collection_id="col-1",
                search_operation=search_operation,
                strategy_evidence=strategy_evidence,
            )
        assert len(strategy_evidence) == 2
        assert all(item["result_count"] == 0 for item in strategy_evidence)
