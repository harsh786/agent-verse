"""Anthropic (Claude) provider implementation.

Default provider for AgentVerse. Uses the official Anthropic Python SDK.
The API key is read from the credential vault or directly from env/secrets.
"""

from __future__ import annotations

from typing import Any

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    TokenUsage,
)


class AnthropicProvider:
    """Anthropic Claude provider.

    Args:
        api_key: Anthropic API key. Reads from env ANTHROPIC_API_KEY if not given.
        default_model: Model to use when the request does not specify one.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        default_model: str = "claude-opus-4-8",
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("Install 'anthropic' to use AnthropicProvider") from exc

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        import anthropic

        model = request.model or self._default_model
        messages = []
        for m in request.messages:
            if m.role == "system":
                continue
            if m.image_data:
                # Multi-modal message with image
                messages.append({
                    "role": m.role,
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": m.image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": m.content if isinstance(m.content, str) else str(m.content),
                        },
                    ],
                })
            else:
                messages.append({"role": m.role, "content": m.content})
        system_prompt = request.system or next(
            (m.content for m in request.messages if m.role == "system"), anthropic.NOT_GIVEN
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if system_prompt is not anthropic.NOT_GIVEN:
            kwargs["system"] = system_prompt
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in request.tools
            ]

        response = await self._client.messages.create(**kwargs)

        # Record token and cost metrics (never let this break the main path)
        try:
            from app.governance.pricing import estimate_cost
            from app.observability.metrics import record_cost_usd, record_llm_tokens
            record_llm_tokens("anthropic", response.model or "", "prompt",
                              getattr(response.usage, "input_tokens", 0))
            record_llm_tokens("anthropic", response.model or "", "completion",
                              getattr(response.usage, "output_tokens", 0))
            cost = estimate_cost(
                response.model or "",
                getattr(response.usage, "input_tokens", 0),
                getattr(response.usage, "output_tokens", 0),
            )
            if cost > 0:
                record_cost_usd("llm", cost)
        except Exception:
            pass  # Metrics must never break the API call

        text_content = " ".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        tool_calls = [
            {"name": block.name, "input": block.input, "id": block.id}
            for block in response.content
            if block.type == "tool_use"
        ]

        return CompletionResponse(
            content=text_content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            usage=TokenUsage(
                prompt_tokens=getattr(response.usage, "input_tokens", 0),
                completion_tokens=getattr(response.usage, "output_tokens", 0),
                total_tokens=(
                    getattr(response.usage, "input_tokens", 0)
                    + getattr(response.usage, "output_tokens", 0)
                ),
            ),
        )

    async def stream_complete(self, request: CompletionRequest):
        """Stream completion tokens one by one via the Anthropic streaming API."""
        model = request.model or self._default_model
        messages = []
        for m in request.messages:
            if m.role == "system":
                continue
            messages.append({"role": m.role, "content": m.content})
        system_prompt = request.system or next(
            (m.content for m in request.messages if m.role == "system"), None
        )
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "max_tokens": request.max_tokens,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            yield f"[stream error: {exc}]"

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        # Anthropic does not currently offer an embedding API.
        # This raises to ensure callers use a dedicated embedder (Voyage AI, etc.)
        raise NotImplementedError(
            "Anthropic does not provide an embedding API. "
            "Use VoyageProvider or OpenAICompatibleProvider for embeddings."
        )

    def supports_vision(self) -> bool:
        return True

    def supports_tool_use(self) -> bool:
        return True
