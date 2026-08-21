"""Sanitize bounded code observations before model exposure."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^\s,'\"]+")
_HOST_PATH = re.compile(r"(?:/Users/|/home/|/root/|/proc/|/sys/)[^\s:]+")
_INJECTION = re.compile(
    r"(?i)(?:ignore (?:all |your )?previous instructions|reveal (?:the )?system prompt|"
    r"print your instructions)"
)


@dataclass(frozen=True, slots=True)
class SanitizedObservation:
    text: str
    sanitization_codes: tuple[str, ...]
    content_sha256: str
    truncated: bool


def sanitize_observation(text: str, *, maximum_bytes: int = 64 * 1024) -> SanitizedObservation:
    value = text
    codes: set[str] = set()
    replacements = (
        (_ANSI, "ansi_removed"),
        (_CONTROL, "control_removed"),
        (_SECRET, "secret_redacted"),
        (_HOST_PATH, "host_path_redacted"),
        (_INJECTION, "injection_redacted"),
    )
    for pattern, code in replacements:
        replacement = "[REDACTED]" if "redacted" in code else ""
        value, count = pattern.subn(replacement, value)
        if count:
            codes.add(code)
    encoded = value.encode()
    truncated = len(encoded) > maximum_bytes
    if truncated:
        encoded = encoded[:maximum_bytes]
        value = encoded.decode(errors="ignore")
        codes.add("output_truncated")
    return SanitizedObservation(
        text=value,
        sanitization_codes=tuple(sorted(codes)),
        content_sha256=hashlib.sha256(value.encode()).hexdigest(),
        truncated=truncated,
    )


__all__ = ["SanitizedObservation", "sanitize_observation"]
