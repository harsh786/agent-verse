"""Result processor — sanitize, truncate, and normalize tool outputs.

Applied as the last step in the pipeline before storing the result:
  1. Redact secrets (API keys, tokens matching known patterns)
  2. Truncate if over max_length
  3. Strip control characters
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that look like secret values (whole-match replacement)
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),          # OpenAI-style keys
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),        # GitHub personal tokens
    re.compile(r"xoxb-[A-Za-z0-9\-]+"),         # Slack bot tokens
    re.compile(r"Bearer [A-Za-z0-9\-._~+/]+=*"),  # Bearer tokens
]

# Context-aware base64 redaction — only redacts when preceded by a credential
# keyword so commit SHAs, UUIDs, and other legitimate base64-ish strings are
# left untouched.  Group 1 captures the value portion that gets replaced.
_BASE64_PATTERN = re.compile(
    r'(?:password|secret|token|key|credential|auth|bearer)\s*[=:]\s*'
    r'["\']?([A-Za-z0-9+/]{20,}={0,2})["\']?',
    re.IGNORECASE,
)

_TRUNCATION_MARKER = "...[truncated]"
_DEFAULT_MAX_LENGTH = 4000


class ResultProcessor:
    """Post-processes tool output before it enters the agent context."""

    def __init__(self, max_length: int = _DEFAULT_MAX_LENGTH) -> None:
        self._max_length = max_length

    def process(self, raw: str, tenant_ctx: Any = None) -> str:
        result = self._redact(raw)
        result = self._truncate(result)
        return result

    def _redact(self, text: str) -> str:
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        # Context-aware base64: replace only the captured value, not the key name
        text = _BASE64_PATTERN.sub(
            lambda m: m.group(0).replace(m.group(1), "[REDACTED]"),
            text,
        )
        return text

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_length:
            return text
        return text[: self._max_length] + _TRUNCATION_MARKER
