"""Isolated tests for app.rag.agentic.query_expander.QueryExpander.

Previously this module was only exercised indirectly (patched out) via
test_retrieval_engine.py's Fusion RAG tests. These tests drive the real
expansion logic directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.rag.agentic.query_expander import QueryExpander


class TestExpand:
    def test_generates_sensible_variant_for_find_keyword(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("find the deployment guide")

        assert "find the deployment guide" in variants
        assert "search for the deployment guide" in variants

    def test_generates_sensible_variant_for_ticket_issue_synonym(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("open ticket about login")

        assert "open ticket about login" in variants
        assert any("issue" in v for v in variants)

    def test_generates_sensible_variant_for_issue_ticket_synonym(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("open issue about login")

        assert "open issue about login" in variants
        assert any("ticket" in v for v in variants)

    def test_regression_ticket_query_previously_round_tripped_to_itself(self) -> None:
        """Regression test: chaining .replace("ticket","issue").replace(
        "issue","ticket") used to undo itself, so a query containing "ticket"
        (and not "issue") silently produced zero new variants. Both
        directions of the synonym must now actually expand."""
        expander = QueryExpander()

        variants = expander.expand("ticket")

        assert variants == ["ticket", "issue"]

    def test_specific_query_with_no_trigger_words_does_not_expand(self) -> None:
        """A query with none of the trigger substrings ('ticket', 'issue',
        'find') should short-circuit to just itself -- no invented variants."""
        expander = QueryExpander()

        variants = expander.expand("quarterly revenue by region")

        assert variants == ["quarterly revenue by region"]

    def test_respects_max_variants_cap(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("find the ticket about the issue", max_variants=1)

        assert len(variants) == 1

    def test_preserves_order_and_has_no_duplicate_variants(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("find the ticket")

        assert variants[0] == "find the ticket"
        assert len(variants) == len(set(variants))

    def test_empty_string_input(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("")

        assert variants == [""]

    def test_whitespace_only_input(self) -> None:
        expander = QueryExpander()

        variants = expander.expand("   ")

        assert variants == ["   "]


class TestExpandForFusion:
    def test_generates_synonym_variants_for_simple_query(self) -> None:
        expander = QueryExpander()

        variants = expander.expand_for_fusion("authentication flow error")

        assert "authentication flow error" in variants
        assert len(variants) > 1

    def test_specific_query_with_no_synonyms_or_stopwords_stays_single(self) -> None:
        """A query with no recognized synonym terms and no stopwords to strip
        produces no additional variants -- it is already maximally specific."""
        expander = QueryExpander()

        variants = expander.expand_for_fusion("quarterly revenue region")

        assert variants == ["quarterly revenue region"]

    def test_strips_stopwords_as_a_keyword_variant(self) -> None:
        expander = QueryExpander()

        variants = expander.expand_for_fusion("the deployment of the service")

        assert "the deployment of the service" in variants
        # "the" and "of" are stopwords and get stripped entirely.
        assert "deployment service" in variants

    def test_respects_max_variants_cap(self) -> None:
        expander = QueryExpander()

        variants = expander.expand_for_fusion("authentication flow error deploy", max_variants=2)

        assert len(variants) <= 2

    def test_empty_string_input_returns_empty_list(self) -> None:
        """Unlike expand(), expand_for_fusion() filters out blank variants,
        so an empty query yields no variants at all rather than [""]."""
        expander = QueryExpander()

        variants = expander.expand_for_fusion("")

        assert variants == []

    def test_whitespace_only_input_returns_empty_list(self) -> None:
        expander = QueryExpander()

        variants = expander.expand_for_fusion("   ")

        assert variants == []


class TestExpandForFusionAsync:
    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_rule_based_expansion(self) -> None:
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async("authentication flow error", provider=None)

        assert variants == expander.expand_for_fusion("authentication flow error")

    @pytest.mark.asyncio
    async def test_successful_llm_generation_is_used(self) -> None:
        from app.providers.base import CompletionResponse

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content="How do I sign in?\nWhat causes login failures?\nExplain the auth process",
            model="m",
        )
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async(
            "how does authentication work", provider=provider, model="m"
        )

        assert variants[0] == "how does authentication work"
        assert "How do I sign in?" in variants
        assert "What causes login failures?" in variants
        provider.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_rule_based_when_not_strict(self) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("provider unavailable")
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async(
            "authentication flow error", provider=provider, model="m", strict=False
        )

        assert variants == expander.expand_for_fusion("authentication flow error")

    @pytest.mark.asyncio
    async def test_llm_error_raises_when_strict(self) -> None:
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("provider unavailable")
        expander = QueryExpander()

        with pytest.raises(RuntimeError, match="provider unavailable"):
            await expander.expand_for_fusion_async(
                "authentication flow error", provider=provider, model="m", strict=True
            )

    @pytest.mark.asyncio
    async def test_empty_llm_response_falls_back_to_query_only(self) -> None:
        from app.providers.base import CompletionResponse

        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(content="   ", model="m")
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async(
            "authentication flow error", provider=provider, model="m"
        )

        assert variants == ["authentication flow error"]

    @pytest.mark.asyncio
    async def test_empty_string_input_with_no_provider(self) -> None:
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async("", provider=None)

        assert variants == []

    @pytest.mark.asyncio
    async def test_whitespace_only_input_with_no_provider(self) -> None:
        expander = QueryExpander()

        variants = await expander.expand_for_fusion_async("   ", provider=None)

        assert variants == []
