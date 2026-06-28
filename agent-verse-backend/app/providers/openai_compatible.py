"""OpenAI-compatible provider — covers OpenAI, Ollama, Groq, Together, Azure, vLLM.

Any service that speaks the OpenAI Chat Completions + Embeddings API works here.
"""

from __future__ import annotations

import json

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    TokenUsage,
)


class OpenAICompatibleProvider:
    """Provider for OpenAI and any OpenAI-compatible API.

    Args:
        api_key: API key. Uses OPENAI_API_KEY env var if not provided.
        base_url: Override for Ollama/Groq/Together/Azure/vLLM endpoints.
        default_model: Default model to use.
        supports_vision_flag: Whether this endpoint supports image inputs.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        default_model: str = "gpt-4o",
        supports_vision_flag: bool = True,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError("Install 'openai' to use OpenAICompatibleProvider") from exc

        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._default_model = default_model
        self._vision = supports_vision_flag

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = request.model or self._default_model
        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]

        response = await self._client.chat.completions.create(**kwargs)

        # Record token and cost metrics (never let this break the main path)
        try:
            from app.governance.pricing import estimate_cost
            from app.observability.metrics import record_cost_usd, record_llm_tokens
            usage = getattr(response, "usage", None)
            if usage:
                record_llm_tokens("openai", response.model or "", "prompt",
                                  getattr(usage, "prompt_tokens", 0))
                record_llm_tokens("openai", response.model or "", "completion",
                                  getattr(usage, "completion_tokens", 0))
                cost = estimate_cost(
                    response.model or "",
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
                if cost > 0:
                    record_cost_usd("llm", cost)
        except Exception:
            pass

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "name": tc.function.name,
                    "input": (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    ) if isinstance(tc.function.arguments, str) else (tc.function.arguments or {}),
                    "id": tc.id,
                }
                for tc in choice.message.tool_calls
            ]

        _prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        _completion_tokens = response.usage.completion_tokens if response.usage else 0
        return CompletionResponse(
            content=content,
            model=response.model,
            input_tokens=_prompt_tokens,
            output_tokens=_completion_tokens,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
            usage=TokenUsage(
                prompt_tokens=_prompt_tokens,
                completion_tokens=_completion_tokens,
                total_tokens=_prompt_tokens + _completion_tokens,
            ),
        )

    async def stream_complete(self, request: CompletionRequest):
        """Stream completion tokens one by one via the OpenAI streaming API."""
        model = request.model or self._default_model
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=request.max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as exc:
            yield f"[stream error: {exc}]"

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        model = request.model or "text-embedding-3-small"
        response = await self._client.embeddings.create(
            model=model,
            input=request.texts,
        )
        return EmbedResponse(
            embeddings=[item.embedding for item in response.data],
            model=response.model,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed up to 2048 texts in one OpenAI API call (OpenAI batch limit).

        Uses ``text-embedding-3-small`` by default (same as ``embed()``).
        Splits into batches of 2048 automatically when *texts* is longer.
        """
        if not texts:
            return []

        model = "text-embedding-3-small"
        all_embeddings: list[list[float]] = []
        batch_size = 2048  # OpenAI API limit per request

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await self._client.embeddings.create(
                model=model,
                input=batch,
            )
            # Sort by index to preserve input order (OpenAI may reorder)
            sorted_data = sorted(response.data, key=lambda e: e.index)
            all_embeddings.extend(e.embedding for e in sorted_data)

        return all_embeddings

    def supports_vision(self) -> bool:
        return self._vision

    def supports_tool_use(self) -> bool:
        return True
