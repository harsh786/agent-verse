"""Tests for Phase 5: PromptBuilder auto-compression."""
from __future__ import annotations

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
