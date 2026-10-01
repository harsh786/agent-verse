"""Timeout / retry options every vendor SDK client is built with (PROV-06).

The OpenAI-compatible and Anthropic SDK clients were constructed with the SDK
defaults (a 600 s timeout), so any caller not going through
``complete_with_failover`` could hang for ten minutes. Values come from Settings
(``LLM_CLIENT_TIMEOUT_SECONDS``, ``LLM_CLIENT_MAX_RETRIES``).
"""

from __future__ import annotations

from typing import Any

_DEFAULT_TIMEOUT_S = 300.0  # matches the executor's per-attempt wall-clock cap
_DEFAULT_MAX_RETRIES = 2


def sdk_client_options() -> dict[str, Any]:
    """``{"timeout": seconds, "max_retries": n}`` for an SDK client constructor."""
    try:
        from app.core.config import get_settings

        s = get_settings()
        timeout = float(getattr(s, "llm_client_timeout_seconds", _DEFAULT_TIMEOUT_S))
        retries = int(getattr(s, "llm_client_max_retries", _DEFAULT_MAX_RETRIES))
    except Exception:
        timeout, retries = _DEFAULT_TIMEOUT_S, _DEFAULT_MAX_RETRIES
    return {"timeout": timeout, "max_retries": max(0, retries)}
