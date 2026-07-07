"""ContextGapDetector — detects 12 gap signal phrases defined in doc-2 §4."""
from __future__ import annotations
import re

_GAP_SIGNALS = frozenset({
    "insufficient",
    "unclear",
    "no information",
    "cannot determine",
    "lack of context",
    "not mentioned",
    "unknown",
    "not found",
    "need more",
    "more context",
    "cannot verify",
    "no relevant",
})


class ContextGapDetector:
    def has_gap(self, text: str) -> bool:
        lower = text.lower()
        return any(signal in lower for signal in _GAP_SIGNALS)

    def extract_missing_topic(self, text: str) -> str:
        patterns = [
            r"cannot determine (.+?)[\.\n]",
            r"no information (?:about|on) (.+?)[\.\n]",
            r"not found:? (.+?)[\.\n]",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                return m.group(1).strip()
        return ""
