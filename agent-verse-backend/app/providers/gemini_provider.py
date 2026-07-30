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

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model_name = request.model or self._default_model
        prompt = self._prompt(request)
        config = self._types.GenerateContentConfig(
            max_output_tokens=request.max_tokens,
            temperature=request.temperature,
        )
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
        model_name = request.model or self._default_model
        content = ""
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=model_name,
                contents=self._prompt(request),
                config=self._types.GenerateContentConfig(
                    max_output_tokens=request.max_tokens,
                    temperature=request.temperature,
                ),
            )
            async for chunk in stream:
                token = str(getattr(chunk, "text", "") or "")
                if token:
                    content += token
                    await on_token(token)
        except Exception:
            return await self.complete(request)
        return CompletionResponse(content=content, model=model_name)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        task_type = (
            "RETRIEVAL_QUERY"
            if request.input_type == "query"
            else "RETRIEVAL_DOCUMENT"
        )
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
        return "\n".join(parts)

    def supports_vision(self) -> bool:
        return "gemini" in self._default_model

    def supports_tool_use(self) -> bool:
        return True
