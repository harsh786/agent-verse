"""Current Google Gen AI async provider coverage.

Tool calling / image input: tests/providers/test_prov07_gemini_tool_calling.py.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.providers.base import CompletionRequest, EmbedRequest, Message


class _Types:
    class GenerateContentConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class EmbedContentConfig:
        def __init__(self, *, task_type: str) -> None:
            self.task_type = task_type


class _Models:
    def __init__(self) -> None:
        self.generate_calls: list[dict[str, object]] = []
        self.embed_calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.generate_calls.append(dict(kwargs))
        usage = SimpleNamespace(prompt_token_count=2, candidates_token_count=3)
        return SimpleNamespace(text="answer", usage_metadata=usage)

    async def embed_content(self, **kwargs: object) -> object:
        self.embed_calls.append(dict(kwargs))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])


def _provider() -> tuple[object, _Models]:
    from app.providers.gemini_provider import GeminiProvider

    models = _Models()
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    provider._types = _Types
    provider._default_model = "gemini-2.5-pro"
    provider._embed_model = "gemini-embedding-001"
    return provider, models


async def test_complete_uses_async_models_and_usage() -> None:
    provider, models = _provider()
    result = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="hello")],
            model="gemini-2.5-pro",
            system="system",
        )
    )
    assert result.content == "answer"
    assert result.total_tokens == 5
    assert "[System]: system" in models.generate_calls[0]["contents"]


async def test_embed_maps_query_and_document_tasks() -> None:
    provider, models = _provider()
    query = await provider.embed(EmbedRequest(texts=["q"], input_type="query"))
    document = await provider.embed(EmbedRequest(texts=["d"], input_type="document"))

    assert query.embeddings == [[0.1, 0.2]]
    assert document.embeddings == [[0.1, 0.2]]
    assert models.embed_calls[0]["config"].task_type == "RETRIEVAL_QUERY"
    assert models.embed_calls[1]["config"].task_type == "RETRIEVAL_DOCUMENT"


async def test_response_schema_requests_json_mode_and_states_schema() -> None:
    provider, models = _provider()
    await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="hi")],
            model="gemini-2.5-pro",
            response_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
    )
    call = models.generate_calls[0]
    assert call["config"].kwargs["response_mime_type"] == "application/json"
    assert '"ok"' in call["contents"]


async def test_stream_tokens_mid_stream_failure_does_not_rerun_complete() -> None:
    import pytest

    provider, models = _provider()

    async def _broken_stream(**kwargs: object) -> object:
        async def gen():  # type: ignore[no-untyped-def]
            yield SimpleNamespace(text="partial ", usage_metadata=None)
            raise ConnectionError("dropped")

        return gen()

    models.generate_content_stream = _broken_stream  # type: ignore[attr-defined]
    tokens: list[str] = []

    async def on_token(tok: str) -> None:
        tokens.append(tok)

    with pytest.raises(ConnectionError):
        await provider.stream_tokens(
            CompletionRequest(messages=[Message(role="user", content="hi")], model="m"),
            on_token,
        )
    assert tokens == ["partial "]
    assert models.generate_calls == []  # complete() was not re-run


async def test_stream_tokens_reports_usage() -> None:
    provider, models = _provider()

    async def _stream(**kwargs: object) -> object:
        async def gen():  # type: ignore[no-untyped-def]
            yield SimpleNamespace(text="a", usage_metadata=None)
            yield SimpleNamespace(
                text="b",
                usage_metadata=SimpleNamespace(prompt_token_count=4, candidates_token_count=2),
            )

        return gen()

    models.generate_content_stream = _stream  # type: ignore[attr-defined]

    async def on_token(tok: str) -> None:
        return None

    resp = await provider.stream_tokens(
        CompletionRequest(messages=[Message(role="user", content="hi")], model="m"), on_token
    )
    assert resp.content == "ab"
    assert resp.total_tokens == 6
    assert resp.usage is not None and resp.usage.total_tokens == 6
