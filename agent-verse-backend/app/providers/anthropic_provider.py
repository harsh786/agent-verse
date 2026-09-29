"""Anthropic (Claude) provider implementation.

Default provider for AgentVerse. Uses the official Anthropic Python SDK.
The API key is read from the credential vault or directly from env/secrets.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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
                messages.append(
                    {
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
                    }
                )
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
            # 2.2: Prompt caching — wrap system prompt in content blocks with
            # cache_control so Anthropic can cache the stable prefix across calls.
            # When cache_prefix is set, only the stable prefix gets cache_control;
            # the volatile part (feedback, replanning context) is NOT cached.
            system_str = system_prompt if isinstance(system_prompt, str) else str(system_prompt)
            if request.cache_prefix and request.cache_prefix in system_str:
                split_idx = system_str.find(request.cache_prefix) + len(request.cache_prefix)
                stable_part = system_str[:split_idx]
                volatile_part = system_str[split_idx:].strip()
                system_blocks: list[dict[str, Any]] = [
                    {"type": "text", "text": stable_part, "cache_control": {"type": "ephemeral"}}
                ]
                if volatile_part:
                    system_blocks.append({"type": "text", "text": volatile_part})
                kwargs["system"] = system_blocks
            else:
                # No cache_prefix — wrap whole system in single ephemeral block
                kwargs["system"] = [
                    {"type": "text", "text": system_str, "cache_control": {"type": "ephemeral"}}
                ]
        if request.response_schema is not None and not request.tools:
            # Force-tool structured output for Anthropic: inject a synthetic tool
            # and force the model to call it so we always get structured JSON back.
            kwargs["tools"] = [
                {
                    "name": "emit_structured_response",
                    "description": "Emit the structured response",
                    "input_schema": request.response_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": "emit_structured_response"}
        elif request.tools:
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

            record_llm_tokens(
                "anthropic",
                response.model or "",
                "prompt",
                getattr(response.usage, "input_tokens", 0),
            )
            record_llm_tokens(
                "anthropic",
                response.model or "",
                "completion",
                getattr(response.usage, "output_tokens", 0),
            )
            cost = estimate_cost(
                response.model or "",
                getattr(response.usage, "input_tokens", 0),
                getattr(response.usage, "output_tokens", 0),
            )
            if cost > 0:
                record_cost_usd("llm", cost)
        except Exception:
            pass  # Metrics must never break the API call

        # Extract content: structured output path returns tool_use input as JSON
        import json as _json

        _struct_block = None
        if request.response_schema is not None and not request.tools:
            _struct_block = next(
                (
                    block
                    for block in response.content
                    if block.type == "tool_use" and block.name == "emit_structured_response"
                ),
                None,
            )

        if _struct_block is not None:
            text_content = _json.dumps(_struct_block.input)
            tool_calls = []
        else:
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
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": request.max_tokens,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception:
            # Never yield the provider error AS model output (it used to emit
            # "[stream error: ...]" as content, which callers rendered/saved as
            # the answer). Propagate so callers can fail over or report it.
            raise

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Stream tokens from the Anthropic API, calling on_token for each text chunk.

        Uses the official Anthropic streaming API (messages.stream). Structured
        ``tool_use`` blocks are returned as ``tool_calls`` (read from the SDK's
        accumulated final message). Falls back to a non-streaming complete() call
        only if the stream fails before any token was delivered; a failure after
        partial output propagates instead of producing a second answer.
        """
        model = request.model or self._default_model
        messages = []
        for m in request.messages:
            if m.role == "system":
                continue
            if m.image_data:
                messages.append(
                    {
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
                    }
                )
            else:
                messages.append({"role": m.role, "content": m.content})

        import anthropic as _anthropic

        system_prompt = request.system or next(
            (m.content for m in request.messages if m.role == "system"),
            _anthropic.NOT_GIVEN,
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if system_prompt is not _anthropic.NOT_GIVEN:
            kwargs["system"] = system_prompt
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]

        if request.response_schema is not None and not request.tools:
            # Structured output forces a synthetic tool; that path lives in complete().
            return await self.complete(request)

        full_text = ""
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text_chunk in stream.text_stream:
                    full_text += text_chunk
                    await on_token(text_chunk)
                # The SDK accumulates every content block, including tool_use blocks
                # whose input arrived as input_json_delta events, into the final
                # message. Text-only iteration used to drop them, so a tool step on
                # Anthropic never dispatched its tool.
                final_msg = await stream.get_final_message()
        except Exception as exc:
            if full_text:
                # Tokens already reached the caller: re-running complete() would
                # emit a second, different answer after the partial one. Propagate
                # so the executor can send token_reset and fail over.
                raise
            logging.getLogger(__name__).warning(
                "anthropic_stream_tokens_failed error=%s fallback=True", str(exc)
            )
            return await self.complete(request)

        return self._response_from_final(final_msg, model, full_text)

    @staticmethod
    def _response_from_final(final_msg: Any, model: str, streamed_text: str) -> CompletionResponse:
        """Build a CompletionResponse (text + tool_calls + usage) from a streamed message."""
        usage = getattr(final_msg, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        blocks = getattr(final_msg, "content", None)
        blocks = blocks if isinstance(blocks, list) else []
        tool_calls = [
            {"name": b.name, "input": b.input, "id": b.id}
            for b in blocks
            if getattr(b, "type", None) == "tool_use"
        ]
        stop_reason = getattr(final_msg, "stop_reason", None)
        served = getattr(final_msg, "model", None)
        return CompletionResponse(
            content=streamed_text,
            model=served if isinstance(served, str) and served else model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            stop_reason=(
                stop_reason
                if isinstance(stop_reason, str) and stop_reason
                else ("tool_use" if tool_calls else "end_turn")
            ),
            usage=TokenUsage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

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

    def supports_structured_output(self) -> bool:
        return True
