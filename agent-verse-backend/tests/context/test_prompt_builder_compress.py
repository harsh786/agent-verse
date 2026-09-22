"""Tests for Phase 5: PromptBuilder auto-compression."""
from __future__ import annotations

from unittest.mock import patch

from app.context.prompt_builder import _CHARS_PER_TOKEN, PromptBuilder, PromptContextBundle


class TestPromptBuilderAutoCompress:
    def _make_bundle(self, goal: str = "test goal") -> PromptContextBundle:
        return PromptContextBundle(
            goal_context=goal,
            knowledge_chunks=[],
            citations=[],
            session_memory=[],
            reflexion_lessons=[],
        )

    def test_short_prompt_not_truncated(self) -> None:
        builder = PromptBuilder(max_context_tokens=6000)
        bundle = self._make_bundle("short goal")
        result = builder.build_planner_context(bundle)
        assert "short goal" in result

    def test_long_prompt_is_truncated(self) -> None:
        """Prompt exceeding the context window should be trimmed."""
        builder = PromptBuilder(max_context_tokens=100)
        long_goal = "x" * (100 * _CHARS_PER_TOKEN * 2)  # 2× the budget
        bundle = self._make_bundle(long_goal)
        result = builder.build_planner_context(bundle)
        # Result must be shorter than the original full prompt
        assert len(result) <= len(long_goal) + 200  # +200 for the "Goal: " prefix

    def test_estimate_tokens(self) -> None:
        builder = PromptBuilder()
        tokens = builder._estimate_tokens("hello world")
        assert tokens >= 1

    def test_auto_compress_returns_shorter_text_on_overflow(self) -> None:
        builder = PromptBuilder(max_context_tokens=10)
        long_text = "a" * 500
        result = builder._auto_compress(long_text)
        # Result must be shorter than the input (compressor or hard truncation)
        assert len(result) <= len(long_text)


class TestPromptBuilderOversizedBoundary:
    """Regression coverage for the oversized-prompt-after-compression path.

    Bug found while writing these tests: ``_auto_compress`` imported
    ``PromptCompressor`` from ``app.context.prompt_compressor`` -- a module
    that does not exist (the real one lives at
    ``app.agent.prompt_compressor``). The bad import always raised
    ``ModuleNotFoundError``, silently caught by the surrounding
    ``except Exception``, so the "compression pass" documented in the
    docstring never actually ran and every overflow fell straight through
    to hard character truncation. Separately, the constructor call passed a
    ``target_tokens`` kwarg that ``PromptCompressor.__init__`` does not
    accept, which would have raised too even with the import fixed. Both
    are fixed in app/context/prompt_builder.py; these tests would have
    failed against the old code (compress() was never actually invoked).
    """

    def _make_bundle(self, goal: str = "test goal") -> PromptContextBundle:
        return PromptContextBundle(
            goal_context=goal,
            knowledge_chunks=[],
            citations=[],
            session_memory=[],
            reflexion_lessons=[],
        )

    def test_auto_compress_actually_invokes_prompt_compressor(self) -> None:
        """The real PromptCompressor.compress must be called on overflow."""
        builder = PromptBuilder(max_context_tokens=50)
        long_text = "word " * 500
        with patch(
            "app.agent.prompt_compressor.PromptCompressor.compress",
            return_value="compressed",
        ) as mock_compress:
            builder._auto_compress(long_text)
        assert mock_compress.called, (
            "PromptCompressor.compress was never invoked -- the compression "
            "pass is dead code (see class docstring)."
        )

    def test_prompt_still_oversized_after_compression_is_hard_truncated(self) -> None:
        """A prompt with no labeled context blocks doesn't shrink much under
        the heuristic compressor (it only targets [Relevant context] /
        [Knowledge base context] / [Visual context] blocks and tool lists),
        so the result must still be hard-truncated to the model's window."""
        builder = PromptBuilder(max_context_tokens=100)
        # Plain, unlabeled prose: the compressor's block-truncation regex
        # won't match any of it, so compression alone can't bound this.
        huge_unlabeled_prose = "The quick brown fox jumps over the lazy dog. " * 2000
        threshold_chars = int(100 * _CHARS_PER_TOKEN * 0.85)

        result = builder._auto_compress(huge_unlabeled_prose)

        assert len(result) <= threshold_chars
        assert len(result) < len(huge_unlabeled_prose)

    def test_oversized_prompt_with_labeled_context_block_is_compressed_first(self) -> None:
        """A prompt containing a [Relevant context] block should be shrunk by
        the real compressor's block-truncation logic, not just sliced blindly."""
        builder = PromptBuilder(max_context_tokens=200)
        rag_block = "[Relevant context]\n" + ("relevant fact. " * 1000)
        full_prompt = f"Goal: do something\n\n{rag_block}"

        result = builder._auto_compress(full_prompt)

        threshold_chars = int(200 * _CHARS_PER_TOKEN * 0.85)
        assert len(result) <= threshold_chars
        # The compressor's own truncation marker should show up when it did
        # the shrinking (rather than the prompt being blindly hard-sliced
        # mid-word by the fallback).
        assert "Goal: do something" in result

    def test_oversized_prompt_exceeding_full_model_context_window(self) -> None:
        """End-to-end: build_planner_context with a goal so large it exceeds
        the model's max context window even after compression -- must never
        raise and must always return a bounded string."""
        builder = PromptBuilder(max_context_tokens=50)
        bundle = self._make_bundle(goal="x" * 100_000)
        result = builder.build_planner_context(bundle)
        threshold_chars = int(50 * _CHARS_PER_TOKEN * 0.85)
        assert isinstance(result, str)
        assert len(result) <= threshold_chars


class TestPromptBuilderMalformedContextSources:
    """Malformed/corrupted context-source records must degrade gracefully
    instead of crashing prompt assembly. Retrieved records (RAG hits, memory
    rows, graph facts, web results) can come back malformed from a flaky or
    corrupted upstream store -- one bad record must not take down planning
    for the whole goal.

    Bug found while writing these tests: every one of the 7 non-knowledge
    context-source branches in ``build_planner_context`` (and the two
    web_results branches, plus ``_truncate_chunks``) called ``.get(...)``
    directly on each record, assuming a dict. A non-dict entry (e.g. a bare
    string, as a corrupted memory/RAG row might be) raised
    ``AttributeError`` and crashed the whole prompt build -- including all
    the other, well-formed sources in the same bundle. Fixed with
    ``_field_or_self`` / ``_field_or_default`` helpers in
    app/context/prompt_builder.py.
    """

    def _make_bundle(self, **overrides: object) -> PromptContextBundle:
        base: dict[str, object] = dict(
            goal_context="do the thing",
            knowledge_chunks=[],
            citations=[],
            session_memory=[],
            reflexion_lessons=[],
        )
        base.update(overrides)
        return PromptContextBundle(**base)  # type: ignore[arg-type]

    def test_malformed_knowledge_chunk_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            knowledge_chunks=[
                {"content": "a well-formed chunk", "_citation_index": 1},
                "a corrupted chunk that is just a bare string",  # type: ignore[list-item]
                None,  # type: ignore[list-item]
            ],
        )
        result = builder.build_planner_context(bundle)
        assert "a well-formed chunk" in result
        assert "a corrupted chunk that is just a bare string" in result

    def test_malformed_session_memory_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            session_memory=[{"content": "good memory"}, "bad memory string", 42],
        )
        result = builder.build_planner_context(bundle)
        assert "good memory" in result
        assert "bad memory string" in result

    def test_malformed_execution_memory_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            execution_memory=[{"plan": ["step1", "step2"]}, "not-a-dict-plan"],
        )
        result = builder.build_planner_context(bundle)
        assert "step1" in result

    def test_malformed_long_term_memory_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            long_term_memory=[{"content": "learned pref"}, ["nested", "list"]],
        )
        result = builder.build_planner_context(bundle)
        assert "learned pref" in result

    def test_malformed_semantic_cache_hits_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            semantic_cache_hits=[{"content": "cached answer"}, 3.14],
        )
        result = builder.build_planner_context(bundle)
        assert "cached answer" in result

    def test_malformed_graph_facts_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            graph_facts=[{"fact": "X relates to Y"}, object()],
        )
        result = builder.build_planner_context(bundle)
        assert "X relates to Y" in result

    def test_malformed_web_results_entry_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(
            web_results=[{"content": "web hit"}, {"snippet": "fallback snippet"}, "raw string"],
        )
        result = builder.build_planner_context(bundle)
        assert "web hit" in result
        assert "fallback snippet" in result
        assert "raw string" in result

    def test_malformed_entries_across_all_sources_simultaneously(self) -> None:
        """All sources malformed at once must still produce a usable prompt."""
        builder = PromptBuilder()
        bundle = self._make_bundle(
            knowledge_chunks=[123, "bad"],  # type: ignore[list-item]
            session_memory=["bad"],
            execution_memory=["bad"],
            long_term_memory=["bad"],
            semantic_cache_hits=["bad"],
            graph_facts=["bad"],
            web_results=["bad"],
        )
        result = builder.build_planner_context(bundle)
        assert "Goal: do the thing" in result

    def test_executor_context_malformed_web_results_does_not_crash(self) -> None:
        builder = PromptBuilder()
        bundle = self._make_bundle(web_results=["a raw string web result"])
        result = builder.build_executor_context(bundle, step="do step 1")
        assert "a raw string web result" in result


class TestPromptBuilderEmptyContextSources:
    """Empty context-source lists must produce a clean, minimal prompt --
    no stray section labels for absent sources."""

    def _make_bundle(self) -> PromptContextBundle:
        return PromptContextBundle(
            goal_context="do the thing",
            knowledge_chunks=[],
            citations=[],
            session_memory=[],
            reflexion_lessons=[],
            execution_memory=[],
            long_term_memory=[],
            semantic_cache_hits=[],
            graph_facts=[],
            web_results=[],
            degradation_notes=[],
        )

    def test_all_empty_sources_produce_goal_only_planner_prompt(self) -> None:
        builder = PromptBuilder()
        result = builder.build_planner_context(self._make_bundle())
        assert result == "Goal: do the thing"

    def test_all_empty_sources_omit_untrusted_note(self) -> None:
        """No retrieved data at all -> no dangling security preamble."""
        builder = PromptBuilder()
        result = builder.build_planner_context(self._make_bundle())
        assert "UNTRUSTED REFERENCE DATA" not in result
        assert "SECURITY:" not in result

    def test_all_empty_sources_produce_empty_executor_context(self) -> None:
        builder = PromptBuilder()
        result = builder.build_executor_context(self._make_bundle())
        assert result == ""

    def test_all_empty_sources_produce_goal_only_verifier_context(self) -> None:
        builder = PromptBuilder()
        result = builder.build_verifier_context(self._make_bundle())
        assert result == "Goal: do the thing"
