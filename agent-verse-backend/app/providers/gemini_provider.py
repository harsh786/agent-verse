"""Google Gemini provider using the current async Google Gen AI SDK."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    TokenUsage,
)


class GeminiProvider:
    """Cancellable Gemini generation and embedding through ``google-genai``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        default_model: str = "gemini-2.5-pro",
        embed_model: str = "gemini-embedding-001",
        request_timeout_ms: int = 30_000,
        embed_dimensions: int | None = None,
    ) -> None:
        try:
            import google.genai as genai
        except ImportError as exc:
            raise ImportError("Install 'google-genai' to use GeminiProvider") from exc

        self._types = genai.types
        self._client = genai.Client(
            api_key=api_key,
            http_options=self._types.HttpOptions(timeout=request_timeout_ms),
        )
        self._default_model = default_model
        self._embed_model = embed_model
        # Requested output width (``output_dimensionality``, 128..3072 for
        # gemini-embedding-001); None = the model's native width.
        self._embed_dimensions = (
            embed_dimensions if embed_dimensions and embed_dimensions > 0 else None
        )

    @staticmethod
    def _structured(request: CompletionRequest) -> bool:
        """Tools, images or tool turns need structured contents (not a text prompt)."""
        return bool(request.tools) or any(
            getattr(m, "image_data", None) or m.role == "tool" or m.tool_calls
            for m in request.messages
        )

    # JSON-schema keys Gemini function declarations reject.
    _UNSUPPORTED_SCHEMA_KEYS = frozenset({"additionalProperties", "$schema", "$defs", "$ref"})

    @classmethod
    def _gemini_schema(cls, schema: Any) -> Any:
        if isinstance(schema, dict):
            return {
                k: cls._gemini_schema(v)
                for k, v in schema.items()
                if k not in cls._UNSUPPORTED_SCHEMA_KEYS
            }
        if isinstance(schema, list):
            return [cls._gemini_schema(v) for v in schema]
        return schema

    def _contents(self, request: CompletionRequest) -> list[Any]:
        """Messages as google-genai Content: text, inline images, function calls and
        function responses (a tool result is matched to its call by id)."""
        types = self._types
        call_names: dict[str, str] = {}
        contents: list[Any] = []
        for m in request.messages:
            if m.role == "system":
                continue
            parts: list[Any] = []
            if m.role == "tool":
                name = call_names.get(m.tool_call_id or "", m.tool_call_id or "tool")
                parts.append(
                    types.Part.from_function_response(
                        name=name, response={"result": m.content}
                    )
                )
                contents.append(types.Content(role="user", parts=parts))
                continue
            if isinstance(m.content, str) and m.content:
                parts.append(types.Part(text=m.content))
            elif isinstance(m.content, list):
                parts.extend(
                    types.Part(text=str(p.get("text", "")))
                    for p in m.content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if m.image_data:
                parts.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(m.image_data), mime_type="image/png"
                    )
                )
            for tc in m.tool_calls or []:
                call_names[str(tc.get("id") or "")] = str(tc.get("name") or "")
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=str(tc.get("name") or ""), args=dict(tc.get("input") or {})
                        )
                    )
                )
            contents.append(
                types.Content(role="model" if m.role == "assistant" else "user", parts=parts)
            )
        return contents

    def _config(self, request: CompletionRequest) -> Any:  # GenerateContentConfig
        kwargs: dict[str, object] = {
            "max_output_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.response_schema is not None or request.json_object:
            # JSON mode; the schema itself is stated in the prompt (see _prompt).
            kwargs["response_mime_type"] = "application/json"
        if self._structured(request):
            system = request.system or next(
                (m.content for m in request.messages if m.role == "system"), None
            )
            if system:
                kwargs["system_instruction"] = system
        if request.tools:
            kwargs["tools"] = [
                self._types.Tool(
                    function_declarations=[
                        self._types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters=self._gemini_schema(t.input_schema),
                        )
                        for t in request.tools
                    ]
                )
            ]
        return self._types.GenerateContentConfig(**kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model_name = request.model or self._default_model
        contents: Any = (
            self._contents(request) if self._structured(request) else self._prompt(request)
        )
        config = self._config(request)
        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        tool_calls = [
            {
                "name": str(getattr(fc, "name", "") or ""),
                "input": dict(getattr(fc, "args", None) or {}),
                "id": str(getattr(fc, "id", "") or f"call_{i}"),
            }
            for i, fc in enumerate(getattr(response, "function_calls", None) or [])
        ]
        try:
            text = str(getattr(response, "text", "") or "")
        except Exception:  # the SDK raises reading .text on a function-call-only reply
            text = ""
        return CompletionResponse(
            content=text,
            model=model_name,
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=output_tokens,
                total_tokens=prompt_tokens + output_tokens,
            ),
        )

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        if self._structured(request):
            # Tool / image turns: one structured call (function calls are not
            # streamed as text tokens).
            return await self.complete(request)
        model_name = request.model or self._default_model
        content = ""
        usage = None
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model_name,
                contents=self._prompt(request),
                config=self._config(request),
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage_metadata", None) or usage
                token = str(getattr(chunk, "text", "") or "")
                if token:
                    content += token
                    await on_token(token)
        except Exception:
            if content:
                # Tokens were already delivered: re-running complete() would emit
                # a second, different answer after the partial one. Propagate.
                raise
            return await self.complete(request)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return CompletionResponse(
            content=content,
            model=model_name,
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=output_tokens,
                total_tokens=prompt_tokens + output_tokens,
            )
            if usage is not None
            else None,
        )

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        task_type = "RETRIEVAL_QUERY" if request.input_type == "query" else "RETRIEVAL_DOCUMENT"
        config: dict[str, Any] = {"task_type": task_type}
        dimensions = getattr(self, "_embed_dimensions", None)
        if dimensions:
            config["output_dimensionality"] = dimensions
        response = await self._client.aio.models.embed_content(
            model=self._embed_model,
            contents=request.texts,  # type: ignore[arg-type]
            config=self._types.EmbedContentConfig(**config),
        )
        embeddings = [
            list(getattr(item, "values", None) or [])
            for item in (getattr(response, "embeddings", None) or [])
        ]
        return EmbedResponse(embeddings=embeddings, model=self._embed_model)

    async def aclose(self) -> None:
        await self._client.aio.aclose()

    @staticmethod
    def _prompt(request: CompletionRequest) -> str:
        parts: list[str] = []
        system = request.system or next(
            (message.content for message in request.messages if message.role == "system"),
            None,
        )
        if system:
            parts.append(f"[System]: {system}")
        for message in request.messages:
            if message.role != "system":
                parts.append(f"[{message.role.capitalize()}]: {message.content}")
        if request.response_schema is not None:
            import json

            parts.append(
                "[System]: Respond with a single JSON object that matches this JSON "
                f"Schema exactly: {json.dumps(request.response_schema)}"
            )
        return "\n".join(parts)

    def supports_vision(self) -> bool:
        # Images are sent as inline parts (see _contents).
        return True

    def supports_tool_use(self) -> bool:
        # Tools are sent as function declarations; calls come back as tool_calls.
        return True
