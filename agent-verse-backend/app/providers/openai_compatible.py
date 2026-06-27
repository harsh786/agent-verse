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

        return CompletionResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason or "stop",
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

    def supports_vision(self) -> bool:
        return self._vision

    def supports_tool_use(self) -> bool:
        return True
