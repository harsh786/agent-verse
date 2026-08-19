"""Simple OpenAI-compatible providers — one API key, different base URL.

Each class is a thin subclass of OpenAICompatibleProvider that sets the
correct base_url and reads the correct env var for its API key.
This keeps provider proliferation out of the filesystem while still giving
each provider a distinct class for isinstance() checks, tracing, etc.
"""

from __future__ import annotations

import os

from app.providers.openai_compatible import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    """Mistral AI — mistral-large, mistral-medium, codestral, etc."""

    provider_name = "mistral"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "mistral-large-latest",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("MISTRAL_API_KEY", ""),
            base_url="https://api.mistral.ai/v1",
            default_model=default_model,
        )


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek — deepseek-chat, deepseek-coder, deepseek-reasoner."""

    provider_name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "deepseek-chat",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            default_model=default_model,
        )


class PerplexityProvider(OpenAICompatibleProvider):
    """Perplexity — sonar models with real-time internet search."""

    provider_name = "perplexity"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "llama-3.1-sonar-large-128k-online",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("PERPLEXITY_API_KEY", ""),
            base_url="https://api.perplexity.ai",
            default_model=default_model,
        )


class FireworksProvider(OpenAICompatibleProvider):
    """Fireworks AI — fast inference for open-source models."""

    provider_name = "fireworks"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "accounts/fireworks/models/llama-v3p1-70b-instruct",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("FIREWORKS_API_KEY", ""),
            base_url="https://api.fireworks.ai/inference/v1",
            default_model=default_model,
        )


class XAIProvider(OpenAICompatibleProvider):
    """xAI — Grok models."""

    provider_name = "xai"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "grok-beta",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("XAI_API_KEY", ""),
            base_url="https://api.x.ai/v1",
            default_model=default_model,
        )


class MoonshotProvider(OpenAICompatibleProvider):
    """Moonshot AI — long-context Kimi models."""

    provider_name = "moonshot"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "moonshot-v1-8k",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("MOONSHOT_API_KEY", ""),
            base_url="https://api.moonshot.cn/v1",
            default_model=default_model,
        )


class CerebrasProvider(OpenAICompatibleProvider):
    """Cerebras — ultra-fast inference via wafer-scale chips."""

    provider_name = "cerebras"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "llama3.1-70b",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("CEREBRAS_API_KEY", ""),
            base_url="https://api.cerebras.ai/v1",
            default_model=default_model,
        )


class YiProvider(OpenAICompatibleProvider):
    """01.AI — Yi models."""

    provider_name = "yi"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "yi-large",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("YI_API_KEY", ""),
            base_url="https://api.01.ai/v1",
            default_model=default_model,
        )


class HuggingFaceProvider(OpenAICompatibleProvider):
    """HuggingFace Inference API — serverless endpoint for HF models."""

    provider_name = "huggingface"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "meta-llama/Llama-3.1-70B-Instruct",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("HF_API_KEY", ""),
            base_url="https://api-inference.huggingface.co/v1",
            default_model=default_model,
        )


class SambanovaProvider(OpenAICompatibleProvider):
    """SambaNova Cloud — fast inference on SambaNova hardware."""

    provider_name = "sambanova"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "Meta-Llama-3.1-70B-Instruct",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("SAMBANOVA_API_KEY", ""),
            base_url="https://api.sambanova.ai/v1",
            default_model=default_model,
        )


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Azure OpenAI Service — deployment-based routing.

    Environment variables:
        AZURE_OPENAI_API_KEY   — Azure key
        AZURE_OPENAI_RESOURCE  — Azure resource name (e.g. "my-openai")
        AZURE_OPENAI_DEPLOYMENT — Deployment name (default: gpt-4o)
    """

    provider_name = "azure_openai"

    def __init__(
        self,
        api_key: str | None = None,
        resource_name: str | None = None,
        deployment: str | None = None,
        api_version: str = "2025-01-01",
    ) -> None:
        _key = api_key or os.getenv("AZURE_OPENAI_API_KEY", "")
        _resource = resource_name or os.getenv("AZURE_OPENAI_RESOURCE", "")
        _deployment = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

        # Build the deployment-scoped base URL
        _base = (
            f"https://{_resource}.openai.azure.com/openai/deployments/{_deployment}"
            if _resource
            else ""
        )

        try:
            import openai as _openai

            self._azure_client = _openai.AsyncAzureOpenAI(
                api_key=_key,
                azure_endpoint=f"https://{_resource}.openai.azure.com" if _resource else "",
                api_version=api_version,
            )
        except ImportError as exc:
            raise ImportError("Install 'openai' to use AzureOpenAIProvider") from exc

        super().__init__(
            api_key=_key,
            base_url=_base,
            default_model=str(_deployment),
        )
        # Override the internally created client with the Azure-typed one so that
        # AzureOpenAI-specific auth (api_key header + api_version query param) is used.
        self._client = self._azure_client
