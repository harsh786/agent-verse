"""Tests for LLMQueryTransformer."""
from __future__ import annotations

import pytest


class FakeProvider:
    """Minimal fake LLM provider for query transformer tests."""

    def __init__(self, response_text: str = "A broader question about the topic") -> None:
        self._resp = response_text

    async def complete(self, req: object) -> object:
        class R:
            content = None
        r = R()
        r.content = self._resp
        return r


@pytest.mark.asyncio
async def test_step_back_returns_original_and_expanded() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer
    transformer = LLMQueryTransformer(FakeProvider("What is the general theory behind X?"))
    result = await transformer.step_back("How does X work specifically?")
    assert len(result) >= 1
    assert "How does X work specifically?" in result


@pytest.mark.asyncio
async def test_step_back_no_duplicate_when_same_text() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer
    transformer = LLMQueryTransformer(FakeProvider("How does X work specifically?"))
    result = await transformer.step_back("How does X work specifically?")
    # If the LLM returns the same question, only the original should appear
    assert result.count("How does X work specifically?") == 1


@pytest.mark.asyncio
async def test_decompose_returns_multiple_questions() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer
    multi_response = "What is X?\nHow does X work?\nWhy was X invented?"
    transformer = LLMQueryTransformer(FakeProvider(multi_response))
    result = await transformer.decompose("Explain X and its history")
    assert len(result) >= 2  # original + sub-questions


@pytest.mark.asyncio
async def test_rewrite_returns_rewritten_query() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer
    transformer = LLMQueryTransformer(FakeProvider("A clearer version of the question"))
    result = await transformer.rewrite("vague question?")
    assert len(result) == 1
    assert "clearer version" in result[0]


@pytest.mark.asyncio
async def test_transform_deduplicates_results() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer
    # All strategies return the same text — should deduplicate
    transformer = LLMQueryTransformer(FakeProvider("exact same response"))
    result = await transformer.transform("original query")
    # No duplicates
    assert len(result) == len(set(result))


@pytest.mark.asyncio
async def test_transformer_graceful_on_provider_error() -> None:
    from app.rag.agentic.llm_query_transformer import LLMQueryTransformer

    class FailingProvider:
        async def complete(self, req: object) -> None:
            raise RuntimeError("provider down")

    transformer = LLMQueryTransformer(FailingProvider())
    result = await transformer.step_back("my query")
    # Should return at minimum the original query
    assert len(result) >= 1
