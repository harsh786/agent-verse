"""Shared event sanitization helpers for agent and workflow events."""

from __future__ import annotations

import re
from typing import Any, Protocol

_TOOL_EVENT_MAX_LENGTH = 1000
_TOOL_EVENT_TRUNCATION_MARKER = "...[truncated]"
_SENSITIVE_KV_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|secret|password|passwd|pwd|token)"
    r"\b['\"]?\s*[:=]\s*['\"]?)[^\s,;}'\"]+"
)
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?i)\b(authorization\s*[:=]?\s*(?:basic|bearer)\s+)[^\s,;}'\"]+"
)
_BASIC_TOKEN_PATTERN = re.compile(r"(?i)\b(basic\s+)[A-Za-z0-9+/]{8,}={0,2}")
_SIMPLE_EVENT_VALUE_TYPES = (str, int, float, bool)


class ResultProcessor(Protocol):
    def process(self, text: str) -> str: ...


def redact_sensitive_text(value: object) -> str:
    """Return *value* as text with common credentials redacted."""
    text = "" if value is None else str(value)
    text = _SENSITIVE_KV_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    text = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}[REDACTED]", text
    )
    return _BASIC_TOKEN_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", text)


def sanitize_tool_raw_output(
    value: object,
    *,
    result_processor: ResultProcessor | None = None,
    max_length: int = _TOOL_EVENT_MAX_LENGTH,
) -> str:
    text = "" if value is None else str(value)
    if result_processor is not None:
        text = result_processor.process(text)

    text = redact_sensitive_text(text)
    if len(text) > max_length:
        return text[:max_length] + _TOOL_EVENT_TRUNCATION_MARKER
    return text


def sanitize_tool_event_value(
    value: object, *, result_processor: ResultProcessor | None = None
) -> str:
    if value is None:
        return ""

    if result_processor is None and not isinstance(value, _SIMPLE_EVENT_VALUE_TYPES):
        return sanitize_tool_raw_output(
            f"[{type(value).__name__} omitted from event payload]",
            result_processor=result_processor,
        )
    return sanitize_tool_raw_output(value, result_processor=result_processor)


def sanitize_event_value(
    value: Any, *, result_processor: ResultProcessor | None = None
) -> Any:
    if isinstance(value, str):
        return sanitize_tool_raw_output(value, result_processor=result_processor)
    if isinstance(value, dict):
        return {
            sanitize_tool_raw_output(key, result_processor=result_processor)
            if isinstance(key, str)
            else key: sanitize_event_value(
                nested_value, result_processor=result_processor
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_event_value(item, result_processor=result_processor) for item in value]
    return value


def sanitize_event(
    event: dict[str, Any], *, result_processor: ResultProcessor | None = None
) -> dict[str, Any]:
    return {
        sanitize_tool_raw_output(key, result_processor=result_processor): sanitize_event_value(
            value, result_processor=result_processor
        )
        for key, value in event.items()
    }
