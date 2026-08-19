"""OpenRouter meta-provider: access 50+ models via a single API key.

OpenRouter exposes an OpenAI-compatible API, so this provider subclasses
OpenAICompatibleProvider and only overrides the base_url and adds the
HTTP-Referer / X-Title headers required by OpenRouter.
"""

from __future__ import annotations

import os

import httpx

from app.providers.openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter provider.

    Set ``OPENROUTER_API_KEY`` in the environment to enable.
    Optionally set ``OPENROUTER_DEFAULT_MODEL`` to override the default model.
    """

    provider_name = "openrouter"

    POPULAR_MODELS: list[str] = [
        "anthropic/claude-3-5-sonnet",
        "anthropic/claude-3-opus",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "google/gemini-pro-1.5",
        "google/gemini-flash-1.5",
        "meta-llama/llama-3.1-70b-instruct",
        "meta-llama/llama-3.1-8b-instruct",
        "deepseek/deepseek-v3",
        "deepseek/deepseek-r1",
        "mistralai/mistral-large",
        "mistralai/mixtral-8x22b-instruct",
        "qwen/qwen-2.5-72b-instruct",
        "x-ai/grok-beta",
        "cohere/command-r-plus",
        "perplexity/llama-3.1-sonar-large-128k-online",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        _api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        _model: str = (
            default_model
            or os.getenv("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-3-5-sonnet")
            or "anthropic/claude-3-5-sonnet"
        )
        super().__init__(
            api_key=_api_key,
            base_url="https://openrouter.ai/api/v1",
            default_model=_model,
            supports_vision_flag=True,
        )
        self._api_key = _api_key
        self._or_headers = {
            "HTTP-Referer": "https://agentverse.ai",
            "X-Title": "AgentVerse",
        }
        # Rebuild the underlying openai client with OpenRouter-required headers.
        # The default_headers property on AsyncOpenAI is a read-only merged view;
        # custom headers must be supplied at construction time.
        try:
            import openai

            self._client = openai.AsyncOpenAI(
                api_key=_api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers=self._or_headers,
            )
        except ImportError as exc:
            raise ImportError("Install 'openai' to use OpenRouterProvider") from exc

    async def get_available_models(self) -> list[dict[str, object]]:
        """Fetch the current model catalog from OpenRouter."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            return resp.json().get("data", [])  # type: ignore[no-any-return]
