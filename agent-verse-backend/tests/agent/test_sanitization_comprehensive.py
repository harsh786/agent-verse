"""Comprehensive tests for app/agent/sanitization.py — targets 90%+ statement coverage."""
from __future__ import annotations

import pytest

from app.agent.sanitization import (
    ResultProcessor,
    redact_sensitive_text,
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)


# ── redact_sensitive_text ────────────────────────────────────────────────────

def test_redact_api_key() -> None:
    text = 'api_key: "my-super-secret-key"'
    result = redact_sensitive_text(text)
    assert "my-super-secret-key" not in result
    assert "[REDACTED]" in result


def test_redact_access_token() -> None:
    text = "access_token=abc123secret"
    result = redact_sensitive_text(text)
    assert "abc123secret" not in result
    assert "[REDACTED]" in result


def test_redact_password() -> None:
    text = "password: mysecretpassword"
    result = redact_sensitive_text(text)
    assert "mysecretpassword" not in result
    assert "[REDACTED]" in result


def test_redact_authorization_bearer() -> None:
    text = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.token_data"
    result = redact_sensitive_text(text)
    assert "eyJhbGciOiJSUzI1NiJ9" not in result
    assert "[REDACTED]" in result


def test_redact_basic_auth() -> None:
    text = "Basic dXNlcjpwYXNzd29yZA=="
    result = redact_sensitive_text(text)
    assert "dXNlcjpwYXNzd29yZA==" not in result
    assert "[REDACTED]" in result


def test_redact_none_returns_empty() -> None:
    assert redact_sensitive_text(None) == ""


def test_redact_safe_text_unchanged() -> None:
    text = "Hello world, no secrets here"
    result = redact_sensitive_text(text)
    assert result == text


def test_redact_token_key() -> None:
    text = "token: abcdef123"
    result = redact_sensitive_text(text)
    assert "abcdef123" not in result


def test_redact_secret_key() -> None:
    text = "secret: mysecret123"
    result = redact_sensitive_text(text)
    assert "mysecret123" not in result


def test_redact_auth_token() -> None:
    text = "auth_token: xyz987"
    result = redact_sensitive_text(text)
    assert "xyz987" not in result


def test_redact_refresh_token() -> None:
    text = "refresh_token: refresh123"
    result = redact_sensitive_text(text)
    assert "refresh123" not in result


# ── sanitize_tool_raw_output ─────────────────────────────────────────────────

def test_sanitize_raw_output_none_returns_empty() -> None:
    assert sanitize_tool_raw_output(None) == ""


def test_sanitize_raw_output_short_text_unchanged() -> None:
    text = "This is a short output"
    result = sanitize_tool_raw_output(text)
    assert result == "This is a short output"


def test_sanitize_raw_output_truncates_long_text() -> None:
    long_text = "A" * 2000
    result = sanitize_tool_raw_output(long_text)
    assert len(result) < len(long_text) + 20
    assert "...[truncated]" in result


def test_sanitize_raw_output_custom_max_length() -> None:
    text = "Hello World This Is A Test"
    result = sanitize_tool_raw_output(text, max_length=10)
    assert len(result) <= 10 + len("...[truncated]")
    assert "...[truncated]" in result


def test_sanitize_raw_output_with_result_processor() -> None:
    class UpperProcessor:
        def process(self, text: str) -> str:
            return text.upper()

    result = sanitize_tool_raw_output("hello world", result_processor=UpperProcessor())
    assert result == "HELLO WORLD"


def test_sanitize_raw_output_redacts_secrets() -> None:
    text = 'Response: {"api_key": "secret123", "data": "ok"}'
    result = sanitize_tool_raw_output(text)
    assert "secret123" not in result
    assert "[REDACTED]" in result


def test_sanitize_raw_output_non_string_converted() -> None:
    result = sanitize_tool_raw_output(42)
    assert result == "42"


def test_sanitize_raw_output_bool_converted() -> None:
    result = sanitize_tool_raw_output(True)
    assert result == "True"


# ── sanitize_tool_event_value ─────────────────────────────────────────────────

def test_sanitize_event_value_none_returns_empty() -> None:
    assert sanitize_tool_event_value(None) == ""


def test_sanitize_event_value_string() -> None:
    result = sanitize_tool_event_value("plain text")
    assert result == "plain text"


def test_sanitize_event_value_int() -> None:
    result = sanitize_tool_event_value(42)
    assert result == "42"


def test_sanitize_event_value_float() -> None:
    result = sanitize_tool_event_value(3.14)
    assert "3.14" in result


def test_sanitize_event_value_bool() -> None:
    result = sanitize_tool_event_value(True)
    assert "True" in result


def test_sanitize_event_value_complex_object_omitted() -> None:
    class ComplexObj:
        pass

    result = sanitize_tool_event_value(ComplexObj())
    assert "omitted" in result or "ComplexObj" in result


def test_sanitize_event_value_list_omitted() -> None:
    result = sanitize_tool_event_value([1, 2, 3])
    assert "omitted" in result or "list" in result


def test_sanitize_event_value_dict_omitted() -> None:
    result = sanitize_tool_event_value({"key": "val"})
    assert "omitted" in result or "dict" in result


def test_sanitize_event_value_with_processor() -> None:
    class UpperProcessor:
        def process(self, text: str) -> str:
            return text.upper()

    result = sanitize_tool_event_value("hello", result_processor=UpperProcessor())
    assert result == "HELLO"


# ── sanitize_event_value ─────────────────────────────────────────────────────

def test_sanitize_event_value_string_redacts() -> None:
    from app.agent.sanitization import sanitize_event_value
    result = sanitize_event_value("api_key: secret")
    assert "secret" not in result or "REDACTED" in result


def test_sanitize_event_value_dict_recursive() -> None:
    from app.agent.sanitization import sanitize_event_value
    # sanitize_event_value recurses into dict values as strings
    # String values containing inline kv patterns are redacted
    event = {"data": "safe", "nested": {"info": "password: p4ssw0rd_secret"}}
    result = sanitize_event_value(event)
    # The value "password: p4ssw0rd_secret" should have secret redacted
    assert "p4ssw0rd_secret" not in str(result)


def test_sanitize_event_value_list_recursive() -> None:
    from app.agent.sanitization import sanitize_event_value
    data = ["safe text", "api_key: leak123"]
    result = sanitize_event_value(data)
    assert "leak123" not in str(result)


def test_sanitize_event_value_int_passthrough() -> None:
    from app.agent.sanitization import sanitize_event_value
    assert sanitize_event_value(42) == 42


def test_sanitize_event_value_none_passthrough() -> None:
    from app.agent.sanitization import sanitize_event_value
    assert sanitize_event_value(None) is None


# ── sanitize_event ────────────────────────────────────────────────────────────

def test_sanitize_event_basic() -> None:
    event = {"type": "step_complete", "output": "Done successfully"}
    result = sanitize_event(event)
    assert result["type"] == "step_complete"
    assert result["output"] == "Done successfully"


def test_sanitize_event_redacts_secret_values() -> None:
    event = {"info": "api_key: secretvalue123", "other": "safe data"}
    result = sanitize_event(event)
    assert "secretvalue123" not in str(result)


def test_sanitize_event_handles_numeric_values() -> None:
    event = {"count": 5, "score": 0.95}
    result = sanitize_event(event)
    assert result["count"] == 5
    assert result["score"] == 0.95


def test_sanitize_event_handles_nested_dict() -> None:
    event = {"data": {"inner": "safe value"}}
    result = sanitize_event(event)
    assert result["data"]["inner"] == "safe value"


def test_sanitize_event_handles_list_values() -> None:
    event = {"steps": ["step1", "step2"]}
    result = sanitize_event(event)
    assert "step1" in result["steps"]


def test_sanitize_event_with_result_processor() -> None:
    class UpperProcessor:
        def process(self, text: str) -> str:
            return text.upper()

    event = {"message": "hello"}
    result = sanitize_event(event, result_processor=UpperProcessor())
    # sanitize_event processes BOTH keys and values through the result_processor.
    # So the key "message" becomes "MESSAGE" and the value "hello" becomes "HELLO".
    assert result.get("MESSAGE") == "HELLO" or result.get("message") == "HELLO"
