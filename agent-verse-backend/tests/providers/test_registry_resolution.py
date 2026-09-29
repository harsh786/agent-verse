"""Provider resolution keeps configured model/base_url, fails loudly on bad config,
and labels metrics by the configured provider rather than always "openai"."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, Message
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    _detect_providers,
    _instantiate_provider,
    resolve_provider,
)


@pytest.mark.parametrize(
    "ptype",
    [
        "mistral",
        "deepseek",
        "perplexity",
        "fireworks",
        "xai",
        "moonshot",
        "cerebras",
        "yi",
        "huggingface",
        "sambanova",
    ],
)
def test_simple_providers_keep_configured_model_and_base_url(ptype: str) -> None:
    p = _instantiate_provider(
        ProviderConfig(
            provider_type=ptype,
            api_key="k",
            base_url="https://proxy.example.com/v1",
            models=["my-model"],
        )
    )
    assert p is not None
    assert p._default_model == "my-model"
    assert p._base_url == "https://proxy.example.com/v1"


def test_simple_provider_defaults_when_unconfigured() -> None:
    p = _instantiate_provider(ProviderConfig(provider_type="mistral", api_key="k"))
    assert p._default_model == "mistral-large-latest"
    assert p._base_url == "https://api.mistral.ai/v1"


def test_azure_keeps_configured_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    p = _instantiate_provider(
        ProviderConfig(provider_type="azure_openai", api_key="k", models=["dep-1"])
    )
    assert p._default_model == "dep-1"


def test_openrouter_keeps_configured_model() -> None:
    p = _instantiate_provider(
        ProviderConfig(provider_type="openrouter", api_key="k", models=["openai/gpt-4o"])
    )
    assert p._default_model == "openai/gpt-4o"


def test_malformed_llm_providers_fails_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LLM_PROVIDERS", "[{not json")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(ProviderConfigurationError, match="LLM_PROVIDERS"):
        _detect_providers()


def test_llm_providers_with_unknown_field_fails_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LLM_PROVIDERS", '[{"provider_type": "openai", "modle": "x"}]')
    with pytest.raises(ProviderConfigurationError):
        _detect_providers()


def test_llm_providers_not_a_list_fails_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LLM_PROVIDERS", '{"provider_type": "openai"}')
    with pytest.raises(ProviderConfigurationError):
        _detect_providers()


def test_malformed_llm_providers_in_dev_warns_and_auto_detects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("LLM_PROVIDERS", "[{not json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    types = [c.provider_type for c in _detect_providers()]
    assert "anthropic" in types


def test_resolve_provider_tags_configured_type() -> None:
    p = resolve_provider([ProviderConfig(provider_type="groq", api_key="k")])
    assert p._agentverse_provider_type == "groq"


async def test_openai_compatible_metrics_labelled_by_configured_provider() -> None:
    p = resolve_provider([ProviderConfig(provider_type="deepseek", api_key="k")])
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        model="deepseek-chat",
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
    )
    p._client = MagicMock()
    p._client.chat.completions.create = AsyncMock(return_value=response)
    with patch("app.observability.metrics.record_llm_tokens") as rec:
        await p.complete(
            CompletionRequest(messages=[Message(role="user", content="x")], model="")
        )
    labels = {call.args[0] for call in rec.call_args_list}
    assert labels == {"deepseek"}
