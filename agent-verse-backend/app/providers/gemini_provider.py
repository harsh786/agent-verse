"""Google Gemini provider implementation.

Supports the Gemini generative models via the google-generativeai SDK.
Falls back gracefully if the SDK is not installed.
"""

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
    """Google Gemini provider.

    Args:
        api_key: Google AI Studio API key. Reads from env GOOGLE_API_KEY if not given.
        default_model: Model to use when the request does not specify one.
        embed_model: Embedding model (e.g. "models/embedding-001").
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        default_model: str = "gemini-1.5-pro",
        embed_model: str = "models/embedding-001",
    ) -> None:
        try:
            import google.generativeai as genai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "Install 'google-generativeai' to use GeminiProvider"
            ) from exc

        if api_key:
            genai.configure(api_key=api_key)

        self._genai = genai
        self._default_model = default_model
        self._embed_model = embed_model

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        import asyncio

        model_name = request.model or self._default_model
        model = self._genai.GenerativeModel(model_name)

        # Build content list (system prompt is prepended as a user message)
        parts: list[str] = []
        system = request.system or next(
            (m.content for m in request.messages if m.role == "system"), None
        )
        if system:
            parts.append(f"[System]: {system}")

        conversation = [m for m in request.messages if m.role != "system"]
        for msg in conversation:
            parts.append(f"[{msg.role.capitalize()}]: {msg.content}")

        prompt = "\n".join(parts)

        # Run synchronously in a thread pool to keep the async interface
        response = await asyncio.get_event_loop().run_in_executor(
            None, model.generate_content, prompt
        )

        text = response.text if hasattr(response, "text") else ""
        _usage_meta = getattr(response, "usage_metadata", None)
        _prompt_toks = getattr(_usage_meta, "prompt_token_count", 0) if _usage_meta else 0
        _cand_toks = getattr(_usage_meta, "candidates_token_count", 0) if _usage_meta else 0
        return CompletionResponse(
            content=text,
            model=model_name,
            input_tokens=_prompt_toks,
            output_tokens=_cand_toks,
            usage=TokenUsage(
                prompt_tokens=_prompt_toks,
                completion_tokens=_cand_toks,
                total_tokens=_prompt_toks + _cand_toks,
            ),
        )

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        """Stream tokens from Gemini via generate_content_async with stream=True.

        Falls back to complete() if streaming raises.
        """
        model_name = request.model or self._default_model
        model = self._genai.GenerativeModel(model_name)

        parts: list[str] = []
        system = request.system or next(
            (m.content for m in request.messages if m.role == "system"), None
        )
        if system:
            parts.append(f"[System]: {system}")
        for msg in [m for m in request.messages if m.role != "system"]:
            parts.append(f"[{msg.role.capitalize()}]: {msg.content}")
        prompt = "\n".join(parts)

        full_text = ""
        try:
            async for chunk in model.generate_content_async(prompt, stream=True):
                text = getattr(chunk, "text", "") or ""
                if text:
                    full_text += text
                    await on_token(text)
        except Exception:
            return await self.complete(request)

        return CompletionResponse(content=full_text, model=model_name)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        import asyncio

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._genai.embed_content(
                model=self._embed_model,
                content=request.texts,
                task_type="retrieval_document",
            ),
        )
        embeddings: list[list[float]] = result.get("embedding", [[]])
        if embeddings and isinstance(embeddings[0], float):
            # Single text returns flat list
            embeddings = [embeddings]  # type: ignore[assignment]
        return EmbedResponse(embeddings=embeddings)

    def supports_vision(self) -> bool:
        return "vision" in self._default_model or "gemini" in self._default_model

    def supports_tool_use(self) -> bool:
        return True
