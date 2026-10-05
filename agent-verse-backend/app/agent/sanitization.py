"""Shared event sanitization helpers for agent and workflow events."""

from __future__ import annotations

from typing import Any, Protocol

# The credential patterns live in a leaf module so the logging pipeline can use
# them without importing the agent package (OI-3); re-exported here.
from app.observability.secret_patterns import (
    BARE_SECRET_PATTERNS,
    redact_sensitive_text,
)

# Cap for event payloads (step/tool outputs shown in the timeline AND used as the
# goal's result summary). 1000 was far too small — it truncated real answers
# (tables, multi-item recommendations) mid-content. 16000 comfortably fits a full
# synthesized answer while still bounding a runaway tool dump (e.g. a scraped page).
_TOOL_EVENT_MAX_LENGTH = 16000
_EXECUTOR_CONTEXT_MAX_LENGTH = 16000  # LLM context for executor — richer than SSE events
_TOOL_EVENT_TRUNCATION_MARKER = "...[truncated]"
_SIMPLE_EVENT_VALUE_TYPES = (str, int, float, bool)

__all__ = [
    "BARE_SECRET_PATTERNS",
    "ResultProcessor",
    "redact_sensitive_text",
    "sanitize_event",
    "sanitize_event_value",
    "sanitize_tool_event_value",
    "sanitize_tool_raw_output",
]


class ResultProcessor(Protocol):
    def process(self, text: str) -> str: ...


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


def sanitize_event_value(value: Any, *, result_processor: ResultProcessor | None = None) -> Any:
    if isinstance(value, str):
        return sanitize_tool_raw_output(value, result_processor=result_processor)
    if isinstance(value, dict):
        return {
            sanitize_tool_raw_output(key, result_processor=result_processor)
            if isinstance(key, str)
            else key: sanitize_event_value(nested_value, result_processor=result_processor)
            for key, nested_value in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        # Tuples/sets used to be returned unsanitized (only lists were walked).
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
