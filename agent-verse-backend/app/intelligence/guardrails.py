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
    "ignore previous instructions",
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

# Each rule is (pattern, label, requires_luhn). Card patterns require a Luhn
# checksum so that runs of years/IDs/timestamps (e.g. "2015 2019 2021 2024") are
# NOT misclassified as credit cards — the source of false positives that redacted
# valid answers.
_PII_RULES: list[tuple[re.Pattern[str], str, bool]] = [
    # SSN: 123-45-6789 (specific enough; no checksum)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN", False),
    # Credit card — brand-specific runs, validated by Luhn
    (
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|[25][1-7][0-9]{14}|6(?:011|5[0-9][0-9])[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b"
        ),
        "CARD",
        True,
    ),
    # Generic 16-digit grouping (fallback) — REQUIRES Luhn to avoid year/ID runs
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "CARD", True),
]


def _luhn_valid(text: str) -> bool:
    """Return True if the digits in *text* satisfy the Luhn checksum (a real card
    number). Filters out arbitrary digit runs (years, ids, timestamps)."""
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(digits):
        # Double every second digit counting from the right (position 1, 3, ...).
        if (len(digits) - 1 - i) % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _pii_spans(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, label) for every genuine PII match in *text*."""
    spans: list[tuple[int, int, str]] = []
    for pattern, label, requires_luhn in _PII_RULES:
        for m in pattern.finditer(text):
            if requires_luhn and not _luhn_valid(m.group()):
                continue
            spans.append((m.start(), m.end(), label))
    return spans


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact ONLY the matched PII spans (never the whole output) and return the
    redacted text plus the distinct issue labels found."""
    spans = _pii_spans(text)
    if not spans:
        return text, []
    # Redact right-to-left so earlier offsets stay valid.
    redacted = text
    for start, end, label in sorted(spans, key=lambda s: s[0], reverse=True):
        redacted = f"{redacted[:start]}[REDACTED:{label}]{redacted[end:]}"
    labels = sorted({label for _, _, label in spans})
    return redacted, [f"Possible {lbl} detected in output" for lbl in labels]

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
        if len(word) >= 16 and re.match(r"^[A-Za-z0-9+/=]+$", word):
            try:
                decoded = (
                    base64.b64decode(word.rstrip("=") + "==")
                    .decode("utf-8", errors="ignore")
                    .lower()
                )
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
    return (
        ["rot13-encoded injection detected"]
        if any(phrase in rot13 for phrase in _INJECTION_PHRASES)
        else []
    )


def _detect_homoglyph_injection(text: str) -> list[str]:
    """Detect Unicode homoglyphs (e.g. cyrillic 'e', 'i', 'o') used to bypass filters.

    BUG FIX: plain NFKC normalization (``_normalize_text``) only canonicalizes
    *compatibility* variants of a character (fullwidth forms, ligatures, etc.)
    — it does NOT map a character from one script to its lookalike in another
    script, so a genuine Cyrillic/Greek homoglyph swap (e.g. Cyrillic U+0456
    for Latin 'i') passed straight through unnoticed, defeating the exact
    attack this function's docstring claims to catch. Cross-script lookalikes
    are folded to ASCII first via ``normalize_homoglyphs`` (the same map used
    by ``app.intelligence.encoding_attacks``), then NFKC still runs for the
    compatibility-variant case.
    """
    from app.intelligence.encoding_attacks import normalize_homoglyphs

    normalized = _normalize_text(normalize_homoglyphs(text))
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


def _detect_base64_dangerous_command(text: str) -> bool:
    """Detect a dangerous shell/SQL command hidden inside a base64-encoded
    substring of a tool arg (e.g. ``{"cmd": "echo <b64> | base64 -d | sh"}``).

    BUG FIX: ``_scan_value_recursive`` previously ran ``_DANGEROUS_PATTERNS``
    only against the raw, undecoded string — a base64-wrapped ``rm -rf /``
    payload produced zero issues even though the equivalent plain-text
    injection phrase IS decoded and caught (via ``_detect_base64_injection``
    in ``check_goal``). Dangerous-command detection had no such decoding path
    at all, so this obfuscation bypassed tool-arg scanning entirely.
    """
    import base64

    # Dangerous commands ("rm -rf", "mkfs", ...) are much shorter than typical
    # injection phrases, so a base64 wrapper can be as short as ~8 chars — the
    # 16-char threshold used for injection-phrase detection would miss them.
    for word in text.split():
        if len(word) >= 8 and re.match(r"^[A-Za-z0-9+/=]+$", word):
            try:
                decoded = base64.b64decode(word.rstrip("=") + "==").decode(
                    "utf-8", errors="ignore"
                )
            except Exception:
                continue
            if any(pattern.search(decoded) for pattern in _DANGEROUS_PATTERNS):
                return True
    return False


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
        _dangerous_found = False
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(value):
                issues.append("Dangerous command pattern detected in args")
                _dangerous_found = True
                break
        if not _dangerous_found and _detect_base64_dangerous_command(value):
            issues.append("Dangerous command pattern detected in args (base64-encoded)")
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
        if (
            self._known_tools
            and tool_name not in _ALWAYS_ALLOWED
            and tool_name not in self._known_tools
        ):
            issues.append(f"Unknown tool '{tool_name}' not in known-tools registry")

        # Recursive injection / dangerous pattern scan over all arg values
        for value in tool_args.values():
            issues.extend(_scan_value_recursive(value))

        return issues

    def check_output(self, *, output: str) -> list[str]:
        """Check LLM or tool output for PII leakage; return list of issues.

        Card patterns are Luhn-validated so year/id/timestamp runs are not flagged.
        """
        _, issues = redact_pii(output)
        return issues

    def redact_output(self, *, output: str) -> tuple[str, list[str]]:
        """Redact only the genuine PII spans in *output*, preserving the rest.

        Returns (redacted_output, issues). Prefer this over check_output so a
        single PII hit never destroys an otherwise-valid answer.
        """
        return redact_pii(output)

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
