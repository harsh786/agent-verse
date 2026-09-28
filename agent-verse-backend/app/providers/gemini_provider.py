"""Google Gemini provider using the current async Google Gen AI SDK."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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

    @staticmethod
    def _reject_unsupported(request: CompletionRequest) -> None:
        """Fail honestly on request features this adapter does not implement.

        The prompt is flattened to text, so tool definitions and image payloads
        used to be dropped silently and the model answered as if they were never
        offered (a fake tool-less "success"). Raise instead.
        """
        if request.tools:
            raise NotImplementedError(
                "GeminiProvider does not implement tool calling; "
                f"{len(request.tools)} tool definition(s) cannot be sent"
            )
        if any(getattr(m, "image_data", None) for m in request.messages):
            raise NotImplementedError("GeminiProvider does not implement image input")

    def _config(self, request: CompletionRequest) -> object:
        kwargs: dict[str, object] = {
            "max_output_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.response_schema is not None or request.json_object:
            # JSON mode; the schema itself is stated in the prompt (see _prompt).
            kwargs["response_mime_type"] = "application/json"
        return self._types.GenerateContentConfig(**kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self._reject_unsupported(request)
        model_name = request.model or self._default_model
        prompt = self._prompt(request)
        config = self._config(request)
        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return CompletionResponse(
            content=str(getattr(response, "text", "") or ""),
            model=model_name,
            input_tokens=prompt_tokens,
            output_tokens=output_tokens,
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
        self._reject_unsupported(request)
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
        response = await self._client.aio.models.embed_content(
            model=self._embed_model,
            contents=request.texts,  # type: ignore[arg-type]
            config=self._types.EmbedContentConfig(task_type=task_type),
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
        # Images are not sent (see _reject_unsupported) — do not advertise vision.
        return False

    def supports_tool_use(self) -> bool:
        # Tool definitions are not sent (see _reject_unsupported).
        return False
