"""
Prompt Compressor
=================
Reduces system prompt token count before LLM calls using heuristic compression.

Techniques (applied in order):
1. Strip consecutive blank lines (common in docstrings pasted into prompts)
2. Abbreviate known verbose patterns (e.g. "you are an AI assistant that..." → "You are...")
3. Remove redundant whitespace within lines
4. Truncate RAG context blocks to a max length

Target savings: 15-30% token reduction on typical prompts without losing meaning.
This is a heuristic compressor — it does NOT use an LLM (zero latency, zero cost).
"""
from __future__ import annotations

import contextlib
import re
from typing import Any

from app.agent.tokenizer import Tokenizer
from app.observability.logging import get_logger

logger = get_logger(__name__)


# Patterns that add tokens but not meaning
_REDUNDANT_PHRASES = [
    (r"You are a helpful, harmless, and honest AI assistant\.", "You are an AI assistant."),
    (r"Please ensure your response is .{0,80}?\.", ""),
    (r"Always respond in a professional and helpful manner\.", ""),
    (r"Remember to think step by step\.", ""),
    (r"Note: The following is for informational purposes only\.", ""),
    (r"As an AI language model, ", ""),
    (r"I want you to ", ""),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _REDUNDANT_PHRASES]

_MAX_RAG_CHARS = 2000   # max characters for any single [context] block (legacy)
_MAX_RAG_TOKENS = 500   # max tokens for any single [context] block
_MAX_TOOL_LIST_ITEMS = 30  # cap injected tool list


class PromptCompressor:
    """
    Stateless heuristic prompt compressor.

    Usage:
        compressor = PromptCompressor()
        shorter = compressor.compress(long_system_prompt)
    """

    def __init__(
        self,
        max_rag_chars: int = _MAX_RAG_CHARS,
        max_rag_tokens: int = _MAX_RAG_TOKENS,
    ) -> None:
        self._max_rag_chars = max_rag_chars
        self._max_rag_tokens = max_rag_tokens
        self._tokenizer = Tokenizer()
        self._stats: dict[str, int] = {"calls": 0, "chars_saved": 0, "tokens_saved": 0}

    def compress(self, text: str) -> str:
        """Return a compressed version of text with ~15-30% fewer tokens."""
        if not text:
            return text
        original_len = len(text)
        original_tokens = self._tokenizer.count(text)
        result = text

        # 1. Collapse 3+ blank lines → 1 blank line
        result = re.sub(r"\n{3,}", "\n\n", result)

        # 2. Strip trailing whitespace on each line
        result = "\n".join(line.rstrip() for line in result.splitlines())

        # 3. Remove known verbose filler phrases
        for pattern, replacement in _COMPILED:
            result = pattern.sub(replacement, result)

        # 4. Truncate oversized [context] blocks (token-accurate)
        result = self._truncate_context_blocks(result)

        # 5. Cap tool list injections
        result = self._cap_tool_list(result)

        saved_chars = original_len - len(result)
        saved_tokens = original_tokens - self._tokenizer.count(result)
        self._stats["calls"] += 1
        self._stats["chars_saved"] += max(0, saved_chars)
        self._stats["tokens_saved"] += max(0, saved_tokens)
        if saved_chars > 200:
            pct = round(saved_chars / original_len * 100)
            logger.debug("prompt_compressed", saved_chars=saved_chars, pct=pct)
        if saved_tokens > 0:
            self._emit_tokens_saved(max(0, saved_tokens))
        return result

    def compress_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compress a list of {role, content} message dicts."""
        return [
            {**m, "content": self.compress(m["content"])}
            if isinstance(m.get("content"), str)
            else m
            for m in messages
        ]

    def compress_rag_context(self, text: str) -> str:
        """
        Truncate only [Relevant context] / [Knowledge base context] / [Visual context]
        blocks by token count. Leaves all other content (tool schemas, JSON, etc.) intact.
        This is the safe path for compressing agent prompts — it never touches JSON schemas.
        """
        if not text:
            return text
        before_tokens = self._tokenizer.count(text)
        result = self._truncate_context_blocks(text)
        saved = before_tokens - self._tokenizer.count(result)
        if saved > 0:
            self._stats["tokens_saved"] += saved
            self._emit_tokens_saved(saved)
        return result

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "avg_chars_saved": (
                self._stats["chars_saved"] // max(self._stats["calls"], 1)
            ),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _emit_tokens_saved(self, amount: int) -> None:
        with contextlib.suppress(Exception):
            from app.observability.metrics import record_prompt_tokens_saved
            record_prompt_tokens_saved(amount)

    def _truncate_context_blocks(self, text: str) -> str:
        """Truncate [context] / [Relevant context] blocks that exceed max token size."""
        def _truncate_block(m: re.Match) -> str:
            block = m.group(0)
            # Truncate if either character OR token limit is exceeded
            char_exceeded = len(block) > self._max_rag_chars
            token_exceeded = self._tokenizer.count(block) > self._max_rag_tokens
            if char_exceeded or token_exceeded:
                truncated = self._tokenizer.truncate_to_tokens(block, self._max_rag_tokens)
                # If char limit is the binding constraint, also truncate by chars
                if char_exceeded and len(truncated) > self._max_rag_chars:
                    truncated = truncated[:self._max_rag_chars]
                return truncated + "\n...[truncated]"
            return block
        # Match blocks starting with [Something context] or [Knowledge ...] up to next [
        return re.sub(
            r"\[(?:Relevant context|Knowledge base context|Visual context)[^\[]{100,}",
            _truncate_block,
            text,
            flags=re.DOTALL,
        )

    def _cap_tool_list(self, text: str) -> str:
        """If [Available tools] section has > _MAX_TOOL_LIST_ITEMS, trim it."""
        marker = "[Available tools]"
        if marker not in text:
            return text
        idx = text.index(marker)
        before = text[:idx + len(marker)]
        after = text[idx + len(marker):]
        lines = after.splitlines()
        tool_lines = [ln for ln in lines if ln.strip().startswith("- ")]
        if len(tool_lines) <= _MAX_TOOL_LIST_ITEMS:
            return text
        # Keep first N tool lines
        new_lines = []
        kept = 0
        for line in lines:
            if line.strip().startswith("- "):
                if kept >= _MAX_TOOL_LIST_ITEMS:
                    continue
                kept += 1
            new_lines.append(line)
        omitted = len(tool_lines) - _MAX_TOOL_LIST_ITEMS
        return before + "\n".join(new_lines) + f"\n...[{omitted} more tools omitted]"


# Module-level singleton
_default_compressor = PromptCompressor()
