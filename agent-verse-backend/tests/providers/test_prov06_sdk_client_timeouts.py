"""PROV-06: SDK clients are built with a Settings-driven timeout and retry count.

OpenAI-compatible and Anthropic SDK clients used the SDK default (600 s timeout),
so any caller outside complete_with_failover could hang for ten minutes.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CLIENT_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("LLM_CLIENT_MAX_RETRIES", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _timeout_seconds(client: object) -> float:
    timeout = client.timeout  # type: ignore[attr-defined]
    return float(getattr(timeout, "read", timeout))


def test_openai_compatible_client_uses_configured_timeout_and_retries() -> None:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    p = OpenAICompatibleProvider(api_key="k", base_url="http://x/v1", default_model="m")
    assert _timeout_seconds(p._client) == 42.0
    assert p._client.max_retries == 1


def test_anthropic_client_uses_configured_timeout_and_retries() -> None:
    from app.providers.anthropic_provider import AnthropicProvider

    p = AnthropicProvider(api_key="k", default_model="claude-x")
    assert _timeout_seconds(p._client) == 42.0
    assert p._client.max_retries == 1


def test_shared_openai_factory_uses_configured_timeout() -> None:
    from app.providers.openai_client import async_openai_client

    client = async_openai_client(api_key="k")
    assert _timeout_seconds(client) == 42.0 and client.max_retries == 1


def test_defaults_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_CLIENT_TIMEOUT_SECONDS")
    monkeypatch.delenv("LLM_CLIENT_MAX_RETRIES")
    get_settings.cache_clear()
    from app.providers.sdk_options import sdk_client_options

    opts = sdk_client_options()
    assert opts["timeout"] <= 300.0 and opts["max_retries"] == 2
