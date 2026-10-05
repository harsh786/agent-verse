"""Credential patterns shared by event sanitization and log redaction.

A dependency-free leaf module: the logging pipeline imports it at startup, so it
must not import any ``app`` package (``app.agent`` imports logging).
"""

from __future__ import annotations

import re

_SENSITIVE_KV_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret|password|passwd|pwd|token)"
    r"\b['\"]?\s*[:=]\s*['\"]?)[^\s,;}&'\"]+"
)
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?i)\b(authorization\s*[:=]?\s*(?:basic|bearer)\s+)[^\s,;}'\"]+"
)
_BASIC_TOKEN_PATTERN = re.compile(r"(?i)\b(basic\s+)[A-Za-z0-9+/]{8,}={0,2}")
# Bare secrets that carry no "key=" / "Authorization:" prefix (a token pasted into
# a step output or error message). Only the key=value / header forms were
# redacted, so these reached SSE events and the persisted event log verbatim.
BARE_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # PEM private-key blocks
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
    # OpenAI / Anthropic style keys (sk-..., sk-proj-..., sk-ant-...)
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    # GitHub tokens
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})"),
    # AWS access key ids
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    # Slack tokens
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    # JSON Web Tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Stripe secret / restricted keys and webhook signing secrets
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{24,}"),
    # Google API keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    # GitLab personal / project / group access tokens
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    # Groq, xAI, Voyage and Hugging Face API keys (OI-3)
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"\bpa-[A-Za-z0-9_-]{30,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
)


def redact_sensitive_text(value: object) -> str:
    """Return *value* as text with common credentials redacted."""
    text = "" if value is None else str(value)
    text = _SENSITIVE_KV_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _AUTHORIZATION_HEADER_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _BASIC_TOKEN_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    for pattern in BARE_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
