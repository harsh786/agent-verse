"""CORE-12: bare Stripe, Google and GitLab keys are redacted before events leave.

A step output containing ``sk_live_`` / ``sk_test_`` / ``rk_live_``, ``AIza...``
or ``glpat-`` tokens used to be streamed over SSE and persisted in the event log
verbatim. Keys are assembled at runtime so no literal secret sits in the repo.
"""

from __future__ import annotations

import pytest

from app.agent.sanitization import redact_sensitive_text

_ALNUM = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"


def _key(prefix: str, body_len: int, alphabet: str = _ALNUM) -> str:
    return prefix + (alphabet * 4)[:body_len]


@pytest.mark.parametrize(
    "secret",
    [
        _key("sk" + "_live_", 24),
        _key("sk" + "_test_", 24),
        _key("rk" + "_live_", 24),
        _key("rk" + "_test_", 99),
        _key("whsec" + "_", 32),
        _key("AI" + "za", 35, _ALNUM + "-_"),
        _key("gl" + "pat-", 20, _ALNUM + "-_"),
        _key("gl" + "pat-", 40),
    ],
)
def test_bare_vendor_secret_is_redacted(secret: str) -> None:
    text = f"Tool returned: using key {secret} for the request."
    out = redact_sensitive_text(text)
    assert secret not in out
    assert "[REDACTED]" in out
    assert out.startswith("Tool returned: using key ")


@pytest.mark.parametrize(
    "benign",
    [
        "the task_live_status flag",  # not a key prefix
        "AIzaSmall",  # too short to be a Google API key
        "glpat-short",  # too short to be a GitLab PAT
        "sk_live_",  # prefix alone
    ],
)
def test_lookalikes_are_left_alone(benign: str) -> None:
    assert redact_sensitive_text(benign) == benign
