"""Deepened coverage for the legacy LLM `Reranker` (app/rag_platform/reranker.py).

Prior coverage (tests/test_gap_completions.py::test_reranker_returns_sorted_results)
only exercised the no-provider score-ordering path. This file exercises the
`_provider`-backed path: what happens when the provider is unavailable, times
out, or returns malformed data — and confirms the *successful* LLM-rerank path
actually reorders by the model's ranking (never previously tested at all).

Fallback destination (from reading `Reranker.rerank`): any failure in
`_llm_rerank` — including a timeout — degrades to sorting the original
documents by their pre-existing `score` field descending (i.e. "no reranking",
not a local cross-encoder; that's a separate code path via `build_reranker`).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.rag_platform.reranker import Reranker


class _FakeProvider:
    """Minimal stand-in for an LLMProvider — returns/raises whatever the test
    configures for `complete()`."""

    def __init__(self, *, content: str | None = None, exc: Exception | None = None) -> None:
        self._content = content
        self._exc = exc
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        from app.providers.base import CompletionResponse

        return CompletionResponse(content=self._content or "[]", model="fake")


def _docs() -> list[dict[str, Any]]:
    return [
        {"content": "Python is a language", "score": 0.7},
        {"content": "The cat sat on the mat", "score": 0.3},
        {"content": "Python pandas is a library", "score": 0.9},
    ]


@pytest.mark.asyncio
class TestRerankerFallback:
    async def test_provider_unavailable_falls_back_to_score_ordering(self) -> None:
        """A ConnectionError (service unreachable) must not propagate — the
        reranker degrades to sorting by the documents' existing score field."""
        r = Reranker()
        r.set_provider(_FakeProvider(exc=ConnectionError("reranker service unreachable")))

        result = await r.rerank("python", _docs(), top_k=2)

        assert len(result) == 2
        assert [d["score"] for d in result] == sorted(
            [d["score"] for d in result], reverse=True
        )
        assert result[0]["score"] == pytest.approx(0.9)

    async def test_provider_timeout_falls_back_to_score_ordering(self) -> None:
        """A timeout surfaces from the provider as an exception (however the
        provider signals it) — the broad except in rerank() must still catch
        it and degrade gracefully rather than letting the goal/step hang or
        fail outright."""
        r = Reranker()
        r.set_provider(_FakeProvider(exc=TimeoutError("reranker call timed out")))

        result = await r.rerank("python", _docs(), top_k=3)

        assert len(result) == 3
        assert result[0]["content"] == "Python pandas is a library"  # highest score

    async def test_provider_call_that_never_returns_is_not_bounded_by_a_timeout(self) -> None:
        """Documents current behaviour: `_llm_rerank` has no internal timeout of
        its own (unlike HostedReranker, which defaults to 10s) — a provider
        that never resolves will hang `rerank()` indefinitely. This test proves
        the absence of a guard by bounding the *test's* wait and expecting the
        call to still be pending afterwards, so a future timeout fix has a
        regression test to flip red->green against."""

        async def _hangs(_request: Any) -> Any:
            await asyncio.sleep(3600)

        provider = _FakeProvider()
        provider.complete = _hangs  # type: ignore[method-assign]
        r = Reranker()
        r.set_provider(provider)

        task = asyncio.ensure_future(r.rerank("python", _docs(), top_k=2))
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    async def test_malformed_json_falls_back_to_score_ordering(self) -> None:
        r = Reranker()
        r.set_provider(_FakeProvider(content="not valid json at all"))

        result = await r.rerank("python", _docs(), top_k=2)

        assert len(result) == 2
        assert result[0]["score"] == pytest.approx(0.9)

    async def test_non_list_json_falls_back_to_score_ordering(self) -> None:
        """Valid JSON but the wrong shape (an object instead of an index array)
        must not crash the reranker — indices[:top_k] on a dict raises
        TypeError, which the broad except must still catch."""
        r = Reranker()
        r.set_provider(_FakeProvider(content=json.dumps({"ranking": [1, 2, 3]})))

        result = await r.rerank("python", _docs(), top_k=2)

        assert len(result) == 2
        assert result[0]["score"] == pytest.approx(0.9)

    async def test_out_of_range_and_wrong_type_indices_are_skipped_not_crashed(self) -> None:
        """Malformed *scores*/indices inside an otherwise-valid array (out of
        range, wrong type, floats) are silently filtered rather than raising,
        and the result is padded back up to top_k from the original order."""
        r = Reranker()
        # 99 is out of range and 1.0 is a float, not an int (json.loads never
        # produces bool/float where an int was written, but a model could
        # emit "1.0") — only the literal int 3, in range, survives. Note the
        # raw list is sliced to top_k *before* filtering, so all three raw
        # entries need to fit within top_k for the valid one not to be cut off.
        r.set_provider(_FakeProvider(content=json.dumps([99, 1.0, 3])))

        result = await r.rerank("python", _docs(), top_k=3)

        assert len(result) == 3
        # Only index 3 (1-indexed -> "Python pandas is a library") was valid;
        # the rest are padded from the original document order, deduplicated.
        assert result[0]["content"] == "Python pandas is a library"
        # No duplicates in the padded result.
        assert len({id(d) for d in result}) == len(result)

    async def test_empty_documents_short_circuits_without_calling_the_provider(self) -> None:
        provider = _FakeProvider(content="[1]")
        r = Reranker()
        r.set_provider(provider)

        result = await r.rerank("python", [], top_k=5)

        assert result == []
        assert provider.calls == 0


@pytest.mark.asyncio
class TestRerankerSuccessPath:
    """The LLM-ranking success path was never exercised before — only the
    fallback. These confirm `_llm_rerank`'s indices actually drive the order
    (not just that *a* result of the right length comes back)."""

    async def test_llm_ranking_reorders_by_returned_indices(self) -> None:
        docs = _docs()  # score order would be [2] > [0] > [1]
        # Ask the "model" to rank the LOWEST-score doc first, to prove the
        # LLM ranking — not the score fallback — determined the order.
        r = Reranker()
        r.set_provider(_FakeProvider(content=json.dumps([2, 1, 3])))

        result = await r.rerank("cats", docs, top_k=3)

        assert [d["content"] for d in result] == [
            "The cat sat on the mat",
            "Python is a language",
            "Python pandas is a library",
        ]

    async def test_llm_ranking_respects_top_k(self) -> None:
        r = Reranker()
        r.set_provider(_FakeProvider(content=json.dumps([3, 2, 1])))

        result = await r.rerank("python", _docs(), top_k=1)

        assert len(result) == 1
        assert result[0]["content"] == "Python pandas is a library"
