"""On-prem model cluster — a model→endpoint dispatching LLM provider.

A self-hosted vLLM cluster serves several models on separate OpenAI-compatible
ports (a capable chat model, a small/fast one, an embedding service). The model
router picks a *model name* per task (planning vs verification, etc.); this
provider dispatches each request to the endpoint that actually serves that model,
so one logical provider fronts many endpoints. Embeddings route to the dedicated
embedding endpoint. Reranking is handled separately by the hosted-reranker config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.providers.openai_compatible import OpenAICompatibleProvider

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.providers.base import (
        CompletionRequest,
        CompletionResponse,
        EmbedRequest,
        EmbedResponse,
    )


class MultiEndpointLLMProvider:
    """Fronts several OpenAI-compatible endpoints, dispatching by model name.

    ``endpoints`` maps a model id → its provider. ``default_model`` is used when a
    request names no model. ``embed_provider`` (optional) serves embeddings.
    ``_agentverse_provider_type`` steers the model router to a matching profile.
    """

    def __init__(
        self,
        *,
        endpoints: dict[str, OpenAICompatibleProvider],
        default_model: str,
        embed_provider: OpenAICompatibleProvider | None = None,
        provider_type: str = "onprem",
    ) -> None:
        if not endpoints:
            raise ValueError("MultiEndpointLLMProvider needs at least one endpoint")
        self._endpoints = endpoints
        self._default_model = default_model
        self._embed = embed_provider
        self._agentverse_provider_type = provider_type

    def _for(self, model: str | None) -> OpenAICompatibleProvider:
        """Pick the endpoint serving *model* (falls back to the default endpoint)."""
        if model and model in self._endpoints:
            return self._endpoints[model]
        return self._endpoints.get(self._default_model) or next(iter(self._endpoints.values()))

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        return await self._for(request.model).complete(request)

    def stream_complete(self, request: CompletionRequest) -> Any:
        return self._for(request.model).stream_complete(request)

    async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
        return await self._for(request.model).stream_tokens(request, on_token)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        target = self._embed or self._for(None)
        return await target.embed(request)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        target = self._embed or self._for(None)
        return await target.embed_batch(texts)

    def supports_vision(self) -> bool:
        return self._for(None).supports_vision()

    def supports_tool_use(self) -> bool:
        return self._for(None).supports_tool_use()

    def supports_structured_output(self) -> bool:
        return self._for(None).supports_structured_output()


def build_onprem_provider(settings: Settings) -> MultiEndpointLLMProvider | None:
    """Build the combined model cluster (NVIDIA + on-prem), or None when unconfigured.

    Precedence: when an NVIDIA key is set it is the *top* model (the cluster's
    default + fallback endpoint); the on-prem Qwen/Gemma endpoints join the same
    model→endpoint router so every model is selectable per task. Returns None only
    when neither NVIDIA nor the on-prem cluster is configured.
    """
    qwen_url = settings.onprem_qwen_base_url.strip()
    onprem_on = bool(settings.onprem_enabled and qwen_url)
    nvidia_on = bool(settings.nvidia_api_key.strip())
    if not (onprem_on or nvidia_on):
        return None

    endpoints: dict[str, OpenAICompatibleProvider] = {}
    default_model = ""

    # NVIDIA is the top model (planning + fallback). It is the chat *default* only
    # when there is no local Qwen to keep interactive chat fast.
    if nvidia_on:
        endpoints[settings.nvidia_model] = OpenAICompatibleProvider(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            default_model=settings.nvidia_model,
        )
        default_model = settings.nvidia_model

    api_key = settings.onprem_api_key or "EMPTY"
    embed_provider: OpenAICompatibleProvider | None = None
    if onprem_on:
        endpoints[settings.onprem_qwen_model] = OpenAICompatibleProvider(
            api_key=api_key, base_url=qwen_url, default_model=settings.onprem_qwen_model
        )
        # Fast local Qwen fronts interactive chat by default (NVIDIA stays top-tier
        # for planning/fallback via the router), unless explicitly disabled.
        if settings.onprem_qwen_is_chat_default or not default_model:
            default_model = settings.onprem_qwen_model
        gemma_url = settings.onprem_gemma_base_url.strip()
        if gemma_url:
            endpoints[settings.onprem_gemma_model] = OpenAICompatibleProvider(
                api_key=api_key, base_url=gemma_url, default_model=settings.onprem_gemma_model
            )

    # Embeddings: prefer the NVIDIA embedding model when configured (dim must match
    # the DB), else the on-prem embedding endpoint.
    if nvidia_on and settings.nvidia_embed_model.strip():
        embed_provider = OpenAICompatibleProvider(
            api_key=settings.nvidia_api_key, base_url=settings.nvidia_base_url,
            default_model=settings.nvidia_embed_model, embed_model=settings.nvidia_embed_model,
        )
    elif onprem_on and settings.onprem_embedding_base_url.strip():
        embed_provider = OpenAICompatibleProvider(
            api_key=api_key, base_url=settings.onprem_embedding_base_url.strip(),
            default_model=settings.onprem_embedding_model,
            embed_model=settings.onprem_embedding_model,
        )

    provider_type = "hybrid" if (nvidia_on and onprem_on) else ("nvidia" if nvidia_on else "onprem")
    return MultiEndpointLLMProvider(
        endpoints=endpoints,
        default_model=default_model,
        embed_provider=embed_provider,
        provider_type=provider_type,
    )
