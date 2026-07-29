"""
Provider Registry
=================
Declarative registry for LLM providers. Reads LLM_PROVIDERS env var
(JSON array) or auto-detects from individual API key env vars.

First healthy provider wins. Supports:
  - anthropic (ANTHROPIC_API_KEY)
  - openai_compatible (OPENAI_API_KEY or OPENAI_BASE_URL)
  - gemini (GOOGLE_API_KEY)
  - ollama (OLLAMA_BASE_URL, no key needed)
  - groq (GROQ_API_KEY)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProviderConfig:
    provider_type: str  # anthropic | openai_compatible | gemini | ollama | groq
    base_url: str = ""
    api_key: str = ""
    models: list[str] | None = None
    display_name: str = ""
    healthy: bool = False


def _detect_providers() -> list[ProviderConfig]:
    """Auto-detect available providers from environment variables."""
    providers: list[ProviderConfig] = []

    # Check LLM_PROVIDERS env var (ordered list override)
    raw = os.getenv("LLM_PROVIDERS", "")
    if raw:
        try:
            configs = json.loads(raw)
            for cfg in configs:
                providers.append(ProviderConfig(**cfg))
            return providers
        except Exception as e:
            logger.warning("llm_providers_parse_error", error=str(e)[:80])

    # Auto-detect from individual keys
    if os.getenv("ANTHROPIC_API_KEY"):
        providers.append(
            ProviderConfig(
                provider_type="anthropic",
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                display_name="Anthropic (Claude)",
            )
        )

    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        providers.append(
            ProviderConfig(
                provider_type="openai_compatible",
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                display_name="OpenAI",
            )
        )

    if os.getenv("GOOGLE_API_KEY"):
        providers.append(
            ProviderConfig(
                provider_type="gemini",
                api_key=os.getenv("GOOGLE_API_KEY", ""),
                display_name="Google Gemini",
            )
        )

    if os.getenv("GROQ_API_KEY"):
        providers.append(
            ProviderConfig(
                provider_type="groq",
                api_key=os.getenv("GROQ_API_KEY", ""),
                base_url="https://api.groq.com/openai/v1",
                display_name="Groq",
            )
        )

    if os.getenv("OLLAMA_BASE_URL"):
        providers.append(
            ProviderConfig(
                provider_type="ollama",
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                display_name="Ollama (local)",
            )
        )

    return providers


def resolve_provider(
    provider_configs: list[ProviderConfig] | None = None,
) -> Any:
    """
    Resolve the first healthy LLM provider.
    Returns a configured LLMProvider instance, or FakeProvider if none available.
    """
    configs = provider_configs if provider_configs is not None else _detect_providers()

    for cfg in configs:
        try:
            provider = _instantiate_provider(cfg)
            if provider is not None:
                logger.info(
                    "provider_resolved",
                    type=cfg.provider_type,
                    name=cfg.display_name,
                )
                return provider
        except Exception as e:
            logger.debug(
                "provider_init_failed", type=cfg.provider_type, error=str(e)[:60]
            )

    # Fallback: FakeProvider for dev/test
    logger.warning("no_llm_provider_configured_using_fake")
    from app.providers.fake import FakeProvider

    return FakeProvider(responses=["No LLM provider configured."])


def _instantiate_provider(cfg: ProviderConfig) -> Any | None:
    """Instantiate a provider from its config. Returns None if prerequisites missing."""
    ptype = cfg.provider_type
    configured_model = cfg.models[0].strip() if cfg.models and cfg.models[0].strip() else ""

    if ptype == "anthropic":
        if not cfg.api_key:
            return None
        from app.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=cfg.api_key,
            default_model=configured_model or "claude-opus-4-8",
        )

    elif ptype in ("openai_compatible", "openai"):
        from app.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=cfg.api_key,
            base_url=cfg.base_url or "https://api.openai.com/v1",
            default_model=configured_model or "gpt-5.2",
        )

    elif ptype == "gemini":
        if not cfg.api_key:
            return None
        try:
            from app.providers.gemini_provider import GeminiProvider

            return GeminiProvider(
                api_key=cfg.api_key,
                default_model=configured_model or "gemini-1.5-pro",
            )
        except ImportError:
            return None

    elif ptype == "ollama":
        # Ollama: no key needed, just a base_url
        from app.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key="ollama",  # placeholder
            base_url=cfg.base_url or "http://localhost:11434/v1",
            default_model=configured_model or "llama3.2",
        )

    elif ptype == "groq":
        if not cfg.api_key:
            return None
        from app.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=cfg.api_key,
            base_url=cfg.base_url or "https://api.groq.com/openai/v1",
            default_model=configured_model or "llama-3.1-70b-versatile",
        )

    return None


def instantiate_configured_provider(
    provider_type: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
) -> Any | None:
    """Instantiate one tenant-configured provider without global fallback."""

    normalized = provider_type.strip().lower()
    if normalized in {"openai", "together", "azure"}:
        normalized = "openai_compatible"
    return _instantiate_provider(
        ProviderConfig(
            provider_type=normalized,
            api_key=api_key,
            base_url=base_url,
            models=[model] if model.strip() else None,
        )
    )


def get_provider_catalog() -> list[dict[str, Any]]:
    """Return available provider configs (safe to expose via API — no keys)."""
    return [
        {
            "type": cfg.provider_type,
            "name": cfg.display_name,
            "configured": bool(cfg.api_key or cfg.base_url),
        }
        for cfg in _detect_providers()
    ]
