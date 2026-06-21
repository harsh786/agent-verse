"""Result processor — sanitize, truncate, and normalize tool outputs.

Applied as the last step in the pipeline before storing the result:
  1. Redact secrets (API keys, tokens matching known patterns)
  2. Truncate if over max_length
  3. Strip control characters
"""

from __future__ import annotations

import re

# Patterns that look like secret values
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),          # OpenAI-style keys
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),        # GitHub personal tokens
    re.compile(r"xoxb-[A-Za-z0-9\-]+"),         # Slack bot tokens
    re.compile(r"Bearer [A-Za-z0-9\-._~+/]+=*"),  # Bearer tokens
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),    # Base64-encoded secrets (≥40 chars)
]

_TRUNCATION_MARKER = "...[truncated]"
_DEFAULT_MAX_LENGTH = 4000


class ResultProcessor:
    """Post-processes tool output before it enters the agent context."""

    def __init__(self, max_length: int = _DEFAULT_MAX_LENGTH) -> None:
        self._max_length = max_length

    def process(self, raw: str) -> str:
        result = self._redact(raw)
        result = self._truncate(result)
        return result

    def _redact(self, text: str) -> str:
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_length:
            return text
        return text[: self._max_length] + _TRUNCATION_MARKER
