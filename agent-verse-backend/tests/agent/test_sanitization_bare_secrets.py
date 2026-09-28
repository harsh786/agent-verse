"""Regression: bare secrets and non-list containers passed event sanitization.

Only ``key=value`` / ``Authorization:`` / ``Basic`` forms were redacted, so a bare
``sk-...`` / ``ghp_...`` / ``AKIA...`` / JWT / PEM block in a step output reached SSE
events and the persisted event log; tuples/sets were returned unsanitized. The
ResultProcessor missed AWS keys, JWTs and PEM blocks and never stripped control
characters although its docstring promised it.
"""

from __future__ import annotations

import pytest

from app.agent.sanitization import redact_sensitive_text, sanitize_event
from app.reliability.result_processor import ResultProcessor

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxyz\nabc123\n-----END RSA PRIVATE KEY-----"

SECRETS = [
    "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "sk-ant-api03-abcdefghijklmnopqrstuv",
    "ghp_" + "a" * 36,
    "github_pat_" + "B" * 30,
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-1234567890-abcdefghij",
    _JWT,
    _PEM,
]


@pytest.mark.parametrize("secret", SECRETS)
def test_bare_secret_is_redacted(secret: str) -> None:
    out = redact_sensitive_text(f"the tool returned {secret} in its output")
    assert secret not in out
    assert "[REDACTED]" in out
    assert out.startswith("the tool returned ")


@pytest.mark.parametrize("secret", SECRETS)
def test_result_processor_redacts_bare_secret(secret: str) -> None:
    out = ResultProcessor().process(f"value: {secret}")
    assert secret not in out


def test_ordinary_text_is_untouched() -> None:
    text = "Use scikit-learn and sk-learn docs; commit a1b2c3d4e5f6; AKIA is a prefix."
    assert redact_sensitive_text(text) == text


def test_tuple_and_set_values_are_sanitized() -> None:
    secret = "ghp_" + "z" * 36
    event = {"type": "step_complete", "items": (secret, "ok"), "tags": {secret}}
    out = sanitize_event(event)
    assert secret not in str(out)
    assert isinstance(out["items"], list) and "ok" in out["items"]


def test_result_processor_strips_control_chars_but_keeps_formatting() -> None:
    out = ResultProcessor().process("line1\x00\x07\x1b[31m\nline2\tcol\r\n\x7f")
    assert out == "line1[31m\nline2\tcol\r\n"
