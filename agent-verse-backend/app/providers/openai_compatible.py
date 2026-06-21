"""OpenAI-compatible provider — covers OpenAI, Ollama, Groq, Together, Azure, vLLM.

Any service that speaks the OpenAI Chat Completions + Embeddings API works here.
"""

from __future__ import annotations

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
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            tool_calls = [
                {"name": tc.function.name, "input": tc.function.arguments, "id": tc.id}
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
