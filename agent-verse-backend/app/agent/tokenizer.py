"""
Tokenizer — tiktoken-backed token counter with byte-length fallback.

Provides accurate token counts for prompt compression decisions.
Never breaks the pipeline on import failures (tiktoken is optional).
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Bytes-per-token approximation when tiktoken is unavailable
_BYTES_PER_TOKEN = 4


class Tokenizer:
    """Thin tiktoken wrapper with byte-length fallback."""

    def __init__(self, model: str = "cl100k_base") -> None:
        self._enc: Any = None
        self._model = model
        self._fallback = False
        try:
            import tiktoken

            self._enc = tiktoken.get_encoding(model)
            logger.debug("tokenizer_tiktoken_loaded", model=model)
        except Exception as e:
            logger.debug("tokenizer_tiktoken_unavailable", error=str(e)[:60])
            self._fallback = True

    def count(self, text: str) -> int:
        """Return the token count for text."""
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass
        # Byte-length fallback
        return max(1, len(text.encode("utf-8")) // _BYTES_PER_TOKEN)

    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to at most max_tokens tokens."""
        if not text:
            return text
        if self.count(text) <= max_tokens:
            return text
        if self._enc is not None:
            try:
                tokens = self._enc.encode(text)[:max_tokens]
                decoded: str = self._enc.decode(tokens)
                return decoded
            except Exception:
                pass
        # Byte fallback
        max_bytes = max_tokens * _BYTES_PER_TOKEN
        return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")

    @property
    def is_accurate(self) -> bool:
        """True when tiktoken is available (accurate count), False when using fallback."""
        return self._enc is not None


# Module-level singleton
_default_tokenizer = Tokenizer()


def count_tokens(text: str) -> int:
    """Convenience function using the module-level tokenizer."""
    return _default_tokenizer.count(text)
