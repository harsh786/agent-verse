"""PROV-09: provider resolution surfaces failures and never hardcodes model names."""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import registry
from app.providers.registry import (
    ProviderConfig,
    ProviderConfigurationError,
    _detect_providers,
    _instantiate_provider,
    resolve_provider,
)

_ENV = (
    "LLM_PROVIDERS", "NVIDIA_API_KEY", "NVIDIA_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENAI_BASE_URL", "GOOGLE_API_KEY", "GROQ_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    "OLLAMA_EMBED_MODEL", "OLLAMA_OCR_MODEL", "OPENROUTER_API_KEY", "NGC_API_KEY",
    "NVIDIA_NIM_BASE_URL", "AZURE_OPENAI_API_KEY", "ENVIRONMENT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_nvidia_key_without_model_fails_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    with pytest.raises(ProviderConfigurationError, match="NVIDIA_MODEL"):
        _detect_providers()


def test_nvidia_key_without_model_is_skipped_loudly_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    assert [c.provider_type for c in _detect_providers()] == []


def test_nvidia_instantiation_never_falls_back_to_a_hardcoded_model() -> None:
    with pytest.raises(ProviderConfigurationError):
        _instantiate_provider(ProviderConfig(provider_type="nvidia", api_key="k"))


def test_ollama_models_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3:8b")
    monkeypatch.setenv("OLLAMA_EMBED_MODEL", "nomic-embed")
    monkeypatch.setenv("OLLAMA_OCR_MODEL", "my-ocr")
    [cfg] = _detect_providers()
    assert cfg.models == ["llama3:8b"]
    provider = _instantiate_provider(cfg)
    assert provider is not None
    assert provider._default_model == "llama3:8b"
    assert getattr(provider, "_default_embed_model", None) == "nomic-embed"
    assert getattr(provider, "_default_ocr_model", None) == "my-ocr"


def test_failing_init_logs_a_warning_with_the_provider_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(cfg: Any) -> Any:
        raise RuntimeError("sdk exploded")

    seen: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            seen.append((event, kw))

        def info(self, *a: Any, **k: Any) -> None:
            pass

        def error(self, event: str, **kw: Any) -> None:
            seen.append((event, kw))

        debug = info

    monkeypatch.setattr(registry, "_instantiate_provider", _boom)
    monkeypatch.setattr(registry, "logger", _Log())
    resolve_provider([ProviderConfig(provider_type="anthropic", api_key="k")])
    assert any(e == "provider_init_failed" and kw.get("type") == "anthropic" for e, kw in seen)


def test_gemini_import_error_is_logged_not_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def _imp(name: str, *a: Any, **k: Any) -> Any:
        if name == "app.providers.gemini_provider":
            raise ImportError("google-genai not installed")
        return real_import(name, *a, **k)

    warned: list[str] = []
    monkeypatch.setattr(builtins, "__import__", _imp)
    monkeypatch.setattr(
        registry, "logger",
        type("L", (), {"warning": lambda self, e, **k: warned.append(e),
                       "info": lambda self, *a, **k: None,
                       "error": lambda self, *a, **k: None,
                       "debug": lambda self, *a, **k: None})(),
    )
    assert _instantiate_provider(ProviderConfig(provider_type="gemini", api_key="k")) is None
    assert "provider_sdk_missing" in warned


def test_provider_config_has_no_unchecked_healthy_flag() -> None:
    assert "healthy" not in ProviderConfig.__dataclass_fields__


def test_llm_providers_entries_with_legacy_healthy_key_still_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LLM_PROVIDERS",
        '[{"provider_type": "anthropic", "api_key": "k", "healthy": true}]',
    )
    assert [c.provider_type for c in _detect_providers()] == ["anthropic"]
