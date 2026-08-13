"""Streaming guardrail — mid-stream token-level content filter.

Checks a rolling buffer of accumulated LLM output tokens against a set of
compiled regex patterns. When a match is detected the guard returns a BLOCK
decision, allowing the `_on_token` callback in AgentGraph to redact output.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass


@dataclass
class GuardDecision:
    allow: bool
    reason: str | None = None
    matched_pattern: str | None = None


class StreamingGuard:
    """Rolling-buffer token-level content guard.

    Parameters
    ----------
    patterns : list[str]
        Regex patterns that, if matched in the rolling buffer, trigger a block.
    buffer_size : int
        Number of characters kept in the rolling buffer. Must be large enough
        to capture multi-token patterns that span token boundaries.
    """

    def __init__(
        self,
        patterns: list[str] | None = None,
        buffer_size: int = 300,
    ) -> None:
        self._buffer_size = buffer_size
        self._buffer: deque[str] = deque()
        self._buffer_len: int = 0
        self._patterns: list[re.Pattern[str]] = []
        for p in (patterns or []):
            try:
                self._patterns.append(re.compile(p, re.IGNORECASE | re.DOTALL))
            except re.error:
                pass  # skip invalid patterns

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_token(self, token: str) -> GuardDecision:
        """Accumulate *token* in the rolling buffer and check patterns.

        Returns a :class:`GuardDecision` indicating whether to allow or block.
        """
        self._push(token)
        window = self._window()
        for pat in self._patterns:
            m = pat.search(window)
            if m:
                return GuardDecision(
                    allow=False,
                    reason="Pattern matched in streaming output",
                    matched_pattern=pat.pattern[:80],
                )
        return GuardDecision(allow=True)

    def reset(self) -> None:
        """Clear the rolling buffer between goals."""
        self._buffer.clear()
        self._buffer_len = 0

    def add_pattern(self, pattern: str) -> bool:
        """Dynamically add a pattern. Returns True on success."""
        try:
            self._patterns.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
            return True
        except re.error:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _push(self, token: str) -> None:
        self._buffer.append(token)
        self._buffer_len += len(token)
        # Evict oldest characters when buffer exceeds size
        while self._buffer_len > self._buffer_size and self._buffer:
            oldest = self._buffer.popleft()
            self._buffer_len -= len(oldest)

    def _window(self) -> str:
        return "".join(self._buffer)


# ---------------------------------------------------------------------------
# Default instance with common dangerous patterns
# ---------------------------------------------------------------------------

DEFAULT_STREAMING_PATTERNS: list[str] = [
    r"rm\s+-rf\s+/",
    r"DROP\s+TABLE",
    r"DELETE\s+FROM\s+\w+\s+WHERE\s+1\s*=\s*1",
    r"sudo\s+.*--force",
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*bash",
    r"eval\s*\(.*base64",
    r"os\.system\(['\"]rm",
    r"subprocess.*\brm\b.*-rf",
]

default_streaming_guard = StreamingGuard(patterns=DEFAULT_STREAMING_PATTERNS)
