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
_OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"


class ProviderConfigurationError(ValueError):
    """A tenant provider configuration cannot be instantiated safely."""

    def __init__(self, provider_type: str, reason: str) -> None:
        super().__init__(f"Invalid {provider_type} provider configuration: {reason}")
        self.provider_type = provider_type
        self.reason = reason


def _requires_explicit_openai_model(provider_type: str, base_url: str) -> bool:
    provider_type = provider_type.strip().lower()
    if provider_type in {"azure", "together", "openai_compatible"}:
        return True
    normalized_url = base_url.strip().rstrip("/").lower()
    return provider_type == "openai" and bool(normalized_url) and normalized_url != (
        _OFFICIAL_OPENAI_BASE_URL.lower()
    )


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
        openai_base_url = os.getenv("OPENAI_BASE_URL", _OFFICIAL_OPENAI_BASE_URL)
        provider_type = (
            "openai"
            if openai_base_url.rstrip("/").lower() == _OFFICIAL_OPENAI_BASE_URL.lower()
            else "openai_compatible"
        )
        providers.append(
            ProviderConfig(
                provider_type=provider_type,
                api_key=os.getenv("OPENAI_API_KEY", ""),
                base_url=openai_base_url,
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
                models=["qwen3:8b", "qwen2.5-coder:7b", "nomic-embed-text"],
            )
        )

    # OpenRouter — single key, 50+ models
    if os.getenv("OPENROUTER_API_KEY"):
        providers.append(
            ProviderConfig(
                provider_type="openrouter",
                api_key=os.getenv("OPENROUTER_API_KEY", ""),
                models=["anthropic/claude-3-5-sonnet", "openai/gpt-4o", "deepseek/deepseek-v3"],
                display_name="OpenRouter",
            )
        )

    # NVIDIA NIM
    if os.getenv("NGC_API_KEY") or os.getenv("NVIDIA_NIM_BASE_URL"):
        providers.append(
            ProviderConfig(
                provider_type="nvidia_nim",
                api_key=os.getenv("NGC_API_KEY", ""),
                base_url=os.getenv("NVIDIA_NIM_BASE_URL", ""),
                models=["nvidia/llama-3.1-nemotron-70b-instruct"],
                display_name="NVIDIA NIM",
            )
        )

    # Simple OpenAI-compatible providers
    _SIMPLE_PROVIDERS = [
        ("mistral",     "MISTRAL_API_KEY",     "mistral-large-latest",                              "Mistral AI"),
        ("deepseek",    "DEEPSEEK_API_KEY",     "deepseek-chat",                                     "DeepSeek"),
        ("perplexity",  "PERPLEXITY_API_KEY",   "llama-3.1-sonar-large-128k-online",                 "Perplexity"),
        ("fireworks",   "FIREWORKS_API_KEY",    "accounts/fireworks/models/llama-v3p1-70b-instruct", "Fireworks AI"),
        ("xai",         "XAI_API_KEY",          "grok-beta",                                         "xAI (Grok)"),
        ("moonshot",    "MOONSHOT_API_KEY",      "moonshot-v1-8k",                                    "Moonshot AI"),
        ("cerebras",    "CEREBRAS_API_KEY",      "llama3.1-70b",                                      "Cerebras"),
        ("yi",          "YI_API_KEY",            "yi-large",                                          "01.AI (Yi)"),
        ("huggingface", "HF_API_KEY",            "meta-llama/Llama-3.1-70B-Instruct",                "HuggingFace"),
        ("sambanova",   "SAMBANOVA_API_KEY",     "Meta-Llama-3.1-70B-Instruct",                      "SambaNova"),
    ]
    for provider_type, env_key, default_model, display_name in _SIMPLE_PROVIDERS:
        if os.getenv(env_key):
            providers.append(
                ProviderConfig(
                    provider_type=provider_type,
                    api_key=os.getenv(env_key, ""),
                    models=[default_model],
                    display_name=display_name,
                )
            )

    # Azure OpenAI
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_RESOURCE"):
        providers.append(
            ProviderConfig(
                provider_type="azure_openai",
                api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
                models=[os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")],
                display_name="Azure OpenAI",
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

    # Fallback: FakeProvider for dev/test — uses realistic cycling responses so
    # the AgentGraph fully executes (plan → execute → verify → complete) even
    # without an LLM API key.
    logger.warning("no_llm_provider_configured_using_fake")
    from app.providers.fake import FakeProvider

    _FAKE_RESPONSES = [
        # Planner call 1 — returns a valid JSON plan
        '{"steps": ["Analyse the goal and gather relevant context", "Research and compile key findings", "Synthesise results and identify patterns", "Draft comprehensive answer with evidence", "Review and refine the final output"]}',
        # Executor call 1 — execution result
        "I have analysed the goal thoroughly. Initial context gathered and key parameters identified. Proceeding with research phase.",
        # Verifier call 1 — success
        '{"success": true, "feedback": "Step completed successfully. Findings are relevant and accurate. Proceeding to next step."}',
        # Executor call 2
        "Research complete. Key findings compiled: multiple relevant data points discovered, patterns identified, and insights formulated based on available knowledge.",
        # Verifier call 2
        '{"success": true, "feedback": "Research step verified. High confidence in findings."}',
        # Executor call 3
        "Synthesis complete. Patterns identified and cross-referenced. Comprehensive analysis ready for final compilation.",
        # Verifier call 3
        '{"success": true, "feedback": "Synthesis verified. Ready for final output."}',
        # Executor call 4
        "Final output drafted. Comprehensive, well-structured response addressing all aspects of the goal with supporting evidence and clear conclusions.",
        # Verifier call 4 — triggers completion
        '{"success": true, "feedback": "Complete and comprehensive response. Goal fully achieved.", "complete": true}',
    ]

    return FakeProvider(responses=_FAKE_RESPONSES)


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

    elif ptype in ("openai_compatible", "openai", "azure", "together"):
        if _requires_explicit_openai_model(ptype, cfg.base_url) and not configured_model:
            raise ProviderConfigurationError(ptype, "explicit deployment/model is required")
        from app.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            api_key=cfg.api_key,
            base_url=cfg.base_url or _OFFICIAL_OPENAI_BASE_URL,
            default_model=configured_model or "gpt-5.2",
        )

    elif ptype == "gemini":
        if not cfg.api_key:
            return None
        try:
            from app.providers.gemini_provider import GeminiProvider

            return GeminiProvider(
                api_key=cfg.api_key,
                default_model=configured_model or "gemini-2.5-pro",
            )
        except ImportError:
            return None

    elif ptype == "ollama":
        # Delegate to the full OllamaProvider (native embed API, model management, etc.)
        from app.providers.ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=cfg.base_url or "http://localhost:11434",
            default_model=configured_model or "qwen3:8b",
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

    elif ptype == "openrouter":
        if not cfg.api_key:
            return None
        from app.providers.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(
            api_key=cfg.api_key,
            default_model=configured_model or "anthropic/claude-3-5-sonnet",
        )

    elif ptype == "nvidia_nim":
        from app.providers.nvidia_nim_provider import NvidiaNIMProvider

        return NvidiaNIMProvider(
            api_key=cfg.api_key or None,
            base_url=cfg.base_url or None,
            default_model=configured_model or "nvidia/llama-3.1-nemotron-70b-instruct",
        )

    elif ptype in (
        "mistral", "deepseek", "perplexity", "fireworks", "xai",
        "moonshot", "cerebras", "yi", "huggingface", "sambanova", "azure_openai",
    ):
        from app.providers.simple_providers import (
            AzureOpenAIProvider,
            CerebrasProvider,
            DeepSeekProvider,
            FireworksProvider,
            HuggingFaceProvider,
            MistralProvider,
            MoonshotProvider,
            PerplexityProvider,
            SambanovaProvider,
            XAIProvider,
            YiProvider,
        )

        _klass_map = {
            "mistral":     MistralProvider,
            "deepseek":    DeepSeekProvider,
            "perplexity":  PerplexityProvider,
            "fireworks":   FireworksProvider,
            "xai":         XAIProvider,
            "moonshot":    MoonshotProvider,
            "cerebras":    CerebrasProvider,
            "yi":          YiProvider,
            "huggingface": HuggingFaceProvider,
            "sambanova":   SambanovaProvider,
            "azure_openai": AzureOpenAIProvider,
        }
        klass = _klass_map[ptype]
        if not cfg.api_key and ptype != "azure_openai":
            return None
        return klass(api_key=cfg.api_key or None)

    return None


def instantiate_configured_provider(
    provider_type: str,
    *,
    api_key: str,
    model: str = "",
    base_url: str = "",
) -> Any | None:
    """Instantiate one tenant-configured provider without global fallback."""

    original_type = provider_type.strip().lower()
    if _requires_explicit_openai_model(original_type, base_url) and not model.strip():
        raise ProviderConfigurationError(original_type, "explicit deployment/model is required")
    provider = _instantiate_provider(
        ProviderConfig(
            provider_type=original_type,
            api_key=api_key,
            base_url=base_url,
            models=[model] if model.strip() else None,
        )
    )
    if provider is not None:
        provider._agentverse_provider_type = original_type
    return provider


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
