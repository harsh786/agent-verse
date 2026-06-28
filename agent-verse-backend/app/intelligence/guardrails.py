"""Output guardrails — validate LLM-generated tool calls before execution.

Checks performed:
  1. Tool name must be in the known_tools registry (prevents hallucinated tools).
  2. Parameter schema validation — detects prompt-injection patterns in args.
  3. Dangerous command detection (rm -rf, DROP TABLE, etc.).
  4. Output leakage detection (PII, credentials).
  5. Goal injection detection.

An empty known_tools set disables the registry check (allows all tools).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# ── Pattern libraries ─────────────────────────────────────────────────────────

_INJECTION_PHRASES = [
    "ignore all previous instructions",
    "ignore your previous instructions",
    "disregard previous",
    "forget your instructions",
    "you are now",
    "act as if",
    "pretend you are",
    "reveal the system prompt",
    "print your instructions",
    "bypass",
]

_DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"drop\s+table", re.IGNORECASE),
    re.compile(r"drop\s+database", re.IGNORECASE),
    re.compile(r"truncate\s+table", re.IGNORECASE),
    re.compile(r"delete\s+from", re.IGNORECASE),
    re.compile(r"format\s+[a-z]:?/?", re.IGNORECASE),
    re.compile(r"mkfs", re.IGNORECASE),
    re.compile(r">\s*/dev/sd", re.IGNORECASE),
]

_PII_PATTERNS = [
    # SSN: 123-45-6789
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # Credit card: 16-digit run
    re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|[25][1-7][0-9]{14}|6(?:011|5[0-9][0-9])[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b"),
    # Generic 16-digit card (fallback)
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
]

_ALWAYS_ALLOWED = {"llm_call"}


# ── Extended injection detectors ───────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normalize Unicode to NFKC and lower-case for injection detection."""
    return unicodedata.normalize("NFKC", text).lower()


def _detect_base64_injection(text: str) -> list[str]:
    """Detect injection phrases encoded as base64."""
    import base64
    issues: list[str] = []
    for word in text.split():
        if len(word) >= 16 and re.match(r'^[A-Za-z0-9+/=]+$', word):
            try:
                decoded = base64.b64decode(word.rstrip("=") + "==").decode("utf-8", errors="ignore").lower()
                if any(phrase in decoded for phrase in _INJECTION_PHRASES):
                    issues.append("base64-encoded injection phrase detected")
                    break
            except Exception:
                pass
    return issues


def _detect_rot13_injection(text: str) -> list[str]:
    """Detect injection phrases encoded with ROT13."""
    import codecs
    rot13 = codecs.encode(text.lower(), "rot_13")
    return ["rot13-encoded injection detected"] if any(
        phrase in rot13 for phrase in _INJECTION_PHRASES
    ) else []


def _detect_homoglyph_injection(text: str) -> list[str]:
    """Detect Unicode homoglyphs (e.g. cyrillic 'e', 'i', 'o') used to bypass filters."""
    normalized = _normalize_text(text)
    if normalized != text.lower() and any(phrase in normalized for phrase in _INJECTION_PHRASES):
        return ["unicode-homoglyph injection detected"]
    return []


def _detect_indirect_injection(text: str) -> list[str]:
    """Detect indirect/encoded injections and adversarial patterns."""
    issues: list[str] = []

    # Newline/delimiter injection
    if "\n\n" in text and any(phrase in text.lower() for phrase in _INJECTION_PHRASES[:3]):
        issues.append("possible prompt delimiter injection")

    # Leetspeak common substitutions
    leet_map = str.maketrans("4310!7", "aeioit")
    leet_normalized = text.translate(leet_map).lower()
    if any(phrase in leet_normalized for phrase in _INJECTION_PHRASES):
        issues.append("leetspeak injection phrase detected")

    return issues


def _scan_value_recursive(value: Any, depth: int = 0) -> list[str]:
    """FIX: Recursively scan nested dict/list values for injection and dangerous patterns.

    The original check() only iterated tool_args.values() which missed
    injections hidden in nested structures like:
      {"options": {"filter": {"description": "ignore previous instructions"}}}
    """
    if depth > 10:
        return []
    issues: list[str] = []
    if isinstance(value, str):
        text = value.lower()
        for phrase in _INJECTION_PHRASES:
            if phrase in text:
                issues.append(f"Possible prompt-injection detected: '{phrase}'")
                break
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(value):
                issues.append("Dangerous command pattern detected in args")
                break
    elif isinstance(value, dict):
        for k, v in value.items():
            child_issues = _scan_value_recursive(v, depth + 1)
            for ci in child_issues:
                issues.append(f"key={k}: {ci}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            child_issues = _scan_value_recursive(item, depth + 1)
            for ci in child_issues:
                issues.append(f"index={i}: {ci}")
    return issues


# ── Legacy dataclass ───────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    blocked: bool
    reason: str = ""


# ── Enhanced checker ───────────────────────────────────────────────────────────

class GuardrailChecker:
    """Validates tool calls, outputs, and goals against guardrail rules.

    Args:
        known_tools: Set of allowed tool names. Empty set disables registry check.
    """

    def __init__(self, *, known_tools: set[str] | None = None) -> None:
        self._known_tools: set[str] = set(known_tools) if known_tools else set()

    def register_tools(self, tools: set[str]) -> None:
        """Add tools to the known-tools registry at runtime."""
        self._known_tools.update(tools)

    # ── Legacy API (backward-compatible) ──────────────────────────────────────

    def check_tool_call(self, *, tool_name: str) -> GuardrailResult:
        """Legacy single-method check — returns GuardrailResult."""
        issues = self.check(tool_name=tool_name, tool_args={})
        if issues:
            return GuardrailResult(blocked=True, reason="; ".join(issues))
        return GuardrailResult(blocked=False)

    # ── Enhanced API ──────────────────────────────────────────────────────────

    def check(self, *, tool_name: str, tool_args: dict[str, object]) -> list[str]:
        """Validate a tool call; return list of issue strings (empty = OK).

        FIX: Uses recursive scanning (_scan_value_recursive) so injections
        hidden in nested dicts/lists are caught, not just top-level values.
        """
        issues: list[str] = []

        # Registry check (skip for always-allowed tools and when registry empty)
        if self._known_tools and tool_name not in _ALWAYS_ALLOWED and tool_name not in self._known_tools:
            issues.append(f"Unknown tool '{tool_name}' not in known-tools registry")

        # Recursive injection / dangerous pattern scan over all arg values
        for value in tool_args.values():
            issues.extend(_scan_value_recursive(value))

        return issues

    def check_output(self, *, output: str) -> list[str]:
        """Check LLM or tool output for PII leakage; return list of issues."""
        issues: list[str] = []
        for pattern in _PII_PATTERNS:
            if pattern.search(output):
                issues.append("Possible PII detected in output")
                break
        return issues

    def check_goal(self, goal: str) -> list[str]:
        """Check a goal text for injection attempts; return list of issues."""
        issues: list[str] = []
        text_lower = goal.lower()

        # Direct phrase matching
        for phrase in _INJECTION_PHRASES:
            if phrase in text_lower:
                issues.append(f"injection phrase detected: '{phrase}'")

        # Extended detection
        issues.extend(_detect_base64_injection(goal))
        issues.extend(_detect_rot13_injection(goal))
        issues.extend(_detect_homoglyph_injection(goal))
        issues.extend(_detect_indirect_injection(goal))

        return issues
