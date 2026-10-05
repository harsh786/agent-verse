"""BYOK-only production (owner decision 2026-10-05).

``LLM_REQUIRE_PLATFORM_KEY`` (default true) keeps the production refusal to start
without a platform LLM key. Set to false, production starts with the failing
``UnconfiguredLLMProvider`` stand-in: tenants with their own BYOK key run,
tenants without one get "no LLM provider configured" — never canned output.
"""

from __future__ import annotations

import pytest

from app.providers.llm_resolution import (
    NoLLMProviderConfiguredError,
    UnconfiguredLLMProvider,
    platform_key_required,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("true", True), ("1", True), ("false", False), ("0", False), ("no", False)],
)
def test_platform_key_required_reads_the_setting(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv("LLM_REQUIRE_PLATFORM_KEY", raising=False)
    else:
        monkeypatch.setenv("LLM_REQUIRE_PLATFORM_KEY", value)
    assert platform_key_required() is expected


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_BASE_URL",
        "NVIDIA_API_KEY",
        "ONPREM_ENABLED",
        "VOYAGE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_production_without_platform_key_refuses_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.main import _resolve_provider_for_app

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LLM_REQUIRE_PLATFORM_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No LLM provider configured for production"):
        _resolve_provider_for_app(Settings())


async def test_byok_only_production_starts_and_fails_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.main import _resolve_provider_for_app
    from app.providers.base import CompletionRequest, Message

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LLM_REQUIRE_PLATFORM_KEY", "false")
    provider = _resolve_provider_for_app(Settings())
    assert isinstance(provider, UnconfiguredLLMProvider)
    with pytest.raises(NoLLMProviderConfiguredError):
        await provider.complete(
            CompletionRequest(model="any", messages=[Message(role="user", content="hi")])
        )
