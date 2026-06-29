"""Comprehensive tests for OpenAICompatibleProvider — targets 85% coverage.

openai is NOT installed in this environment; all tests mock the SDK via
sys.modules injection so the real openai package is never required.
"""
from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message, ToolDefinition


# ---------------------------------------------------------------------------
# Helpers: build mock openai module and mock response objects
# ---------------------------------------------------------------------------

def _make_openai_module() -> tuple[ModuleType, MagicMock]:
    """Return (mock_openai_module, mock_async_client).

    The mock client is already set as AsyncOpenAI(...) return value.
    """
    mock_openai = MagicMock()  # no spec — allows dynamic attribute access
    mock_client = MagicMock()
    mock_openai.AsyncOpenAI.return_value = mock_client
    return mock_openai, mock_client


def _make_chat_response(
    content: str = "Hello",
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str = "stop",
    tool_calls: list | None = None,
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens

    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls  # None or list

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


def _make_embed_response(
    vectors: list[list[float]],
    model: str = "text-embedding-3-small",
    total_tokens: int = 5,
) -> MagicMock:
    items = []
    for i, vec in enumerate(vectors):
        item = MagicMock()
        item.embedding = vec
        item.index = i
        items.append(item)

    usage = MagicMock()
    usage.total_tokens = total_tokens

    resp = MagicMock()
    resp.data = items
    resp.model = model
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

def test_constructor_stores_default_model() -> None:
    mock_openai, _ = _make_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(api_key="key", default_model="gpt-4o-mini")
    assert p._default_model == "gpt-4o-mini"


def test_constructor_raises_import_error_when_openai_missing() -> None:
    with patch.dict(sys.modules, {"openai": None}):  # type: ignore[dict-item]
        import importlib
        import app.providers.openai_compatible as _mod
        importlib.reload(_mod)
        with pytest.raises(ImportError, match="openai"):
            _mod.OpenAICompatibleProvider(api_key="key")
    # Restore
    import importlib
    import app.providers.openai_compatible as _mod2
    importlib.reload(_mod2)


# ---------------------------------------------------------------------------
# complete() — text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_text_response() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response("AI response", prompt_tokens=5, completion_tokens=10)
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="sk-test")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="gpt-4o",
            )
        )

    assert result.content == "AI response"
    assert result.model == "gpt-4o"
    assert result.input_tokens == 5
    assert result.output_tokens == 10
    assert result.usage.total_tokens == 15
    assert result.stop_reason == "stop"


@pytest.mark.asyncio
async def test_complete_uses_default_model_when_empty() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response("resp")
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key", default_model="gpt-4o-mini")
        await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="")
        )

    call_kw = mock_client.chat.completions.create.call_args
    assert call_kw.kwargs["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_complete_no_usage_returns_zero_tokens() -> None:
    """When response.usage is None, tokens default to 0."""
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response()
    mock_resp.usage = None
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
        )

    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.asyncio
async def test_complete_empty_content_defaults_to_empty_string() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response(content=None)  # type: ignore[arg-type]
    mock_resp.choices[0].message.content = None
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
        )

    assert result.content == ""


# ---------------------------------------------------------------------------
# complete() — tool calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_with_function_tool_calls() -> None:
    """Tool calls from the response are decoded and returned."""
    mock_openai, mock_client = _make_openai_module()

    tc = MagicMock()
    tc.id = "call_123"
    tc.function.name = "search"
    tc.function.arguments = json.dumps({"query": "cats"})

    mock_resp = _make_chat_response(tool_calls=[tc])
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Search cats")],
                model="gpt-4o",
                tools=[
                    ToolDefinition(
                        name="search",
                        description="Search the web",
                        input_schema={"type": "object"},
                    )
                ],
            )
        )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "search"
    assert result.tool_calls[0]["input"] == {"query": "cats"}
    assert result.tool_calls[0]["id"] == "call_123"


@pytest.mark.asyncio
async def test_complete_tool_call_non_string_arguments() -> None:
    """Tool call arguments that are already a dict (not JSON string) are passed through."""
    mock_openai, mock_client = _make_openai_module()

    tc = MagicMock()
    tc.id = "call_abc"
    tc.function.name = "calc"
    tc.function.arguments = {"value": 42}  # already a dict

    mock_resp = _make_chat_response(tool_calls=[tc])
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="Calc")], model="gpt-4o")
        )

    assert result.tool_calls[0]["input"] == {"value": 42}


@pytest.mark.asyncio
async def test_complete_tool_definitions_serialised_correctly() -> None:
    """Tool definitions are serialized as OpenAI function specs."""
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response()
    captured: list[dict] = []

    async def _capture(**kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        return mock_resp

    mock_client.chat.completions.create = _capture

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="gpt-4o",
                tools=[ToolDefinition(name="fn", description="desc", input_schema={"type": "object"})],
            )
        )

    assert len(captured) == 1
    tools = captured[0]["tools"]
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "fn"


# ---------------------------------------------------------------------------
# complete() — metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_metrics_silently_swallowed_on_error() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_resp = _make_chat_response()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        with patch("app.governance.pricing.estimate_cost", side_effect=RuntimeError("boom")):
            result = await provider.complete(
                CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
            )

    assert result.content == "Hello"


# ---------------------------------------------------------------------------
# stream_complete()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_complete_yields_delta_content() -> None:
    mock_openai, mock_client = _make_openai_module()

    chunks = []
    for text in ["Hello", " ", "World"]:
        delta = MagicMock()
        delta.content = text
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        chunks.append(chunk)

    mock_client.chat.completions.create = AsyncMock(return_value=_AsyncIterator(chunks))

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        tokens = []
        async for tok in provider.stream_complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
        ):
            tokens.append(tok)

    assert "".join(tokens) == "Hello World"


@pytest.mark.asyncio
async def test_stream_complete_skips_none_deltas() -> None:
    mock_openai, mock_client = _make_openai_module()

    chunks = []
    for text in [None, "Hello", None]:
        delta = MagicMock()
        delta.content = text
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        chunks.append(chunk)

    mock_client.chat.completions.create = AsyncMock(return_value=_AsyncIterator(chunks))

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        tokens = []
        async for tok in provider.stream_complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
        ):
            tokens.append(tok)

    assert tokens == ["Hello"]


@pytest.mark.asyncio
async def test_stream_complete_yields_error_string_on_exception() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_client.chat.completions.create = AsyncMock(side_effect=OSError("conn refused"))

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        chunks = []
        async for tok in provider.stream_complete(
            CompletionRequest(messages=[Message(role="user", content="Hi")], model="gpt-4o")
        ):
            chunks.append(tok)

    assert len(chunks) == 1
    assert "[stream error:" in chunks[0]


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_returns_vectors_and_tokens() -> None:
    mock_openai, mock_client = _make_openai_module()
    vectors = [[0.1, 0.2], [0.3, 0.4]]
    mock_client.embeddings.create = AsyncMock(
        return_value=_make_embed_response(vectors, total_tokens=8)
    )

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.embed(EmbedRequest(texts=["hello", "world"]))

    assert result.embeddings == vectors
    assert result.total_tokens == 8


@pytest.mark.asyncio
async def test_embed_uses_default_model() -> None:
    mock_openai, mock_client = _make_openai_module()
    captured: list[dict] = []

    async def _capture(**kw: object) -> MagicMock:
        captured.append(dict(kw))
        return _make_embed_response([[0.0]])

    mock_client.embeddings.create = _capture

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        await provider.embed(EmbedRequest(texts=["text"]))

    assert captured[0]["model"] == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_embed_no_usage_returns_zero_tokens() -> None:
    mock_openai, mock_client = _make_openai_module()
    mock_embed_resp = _make_embed_response([[0.1]])
    mock_embed_resp.usage = None
    mock_client.embeddings.create = AsyncMock(return_value=mock_embed_resp)

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.embed(EmbedRequest(texts=["x"]))

    assert result.total_tokens == 0


# ---------------------------------------------------------------------------
# embed_batch()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_batch_empty_returns_empty_list() -> None:
    mock_openai, mock_client = _make_openai_module()

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.embed_batch([])

    assert result == []
    mock_client.embeddings.create.assert_not_called()


@pytest.mark.asyncio
async def test_embed_batch_single_batch_under_2048() -> None:
    mock_openai, mock_client = _make_openai_module()
    texts = [f"text_{i}" for i in range(10)]
    vectors = [[float(i)] for i in range(10)]
    mock_client.embeddings.create = AsyncMock(
        return_value=_make_embed_response(vectors)
    )

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.embed_batch(texts)

    assert len(result) == 10
    mock_client.embeddings.create.assert_called_once()


@pytest.mark.asyncio
async def test_embed_batch_splits_at_2048() -> None:
    """embed_batch() makes multiple API calls when texts > 2048."""
    mock_openai, mock_client = _make_openai_module()

    call_count = 0

    async def _fake_embed(**kw: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        batch_size = len(kw["input"])
        vecs = [[float(i)] for i in range(batch_size)]
        return _make_embed_response(vecs)

    mock_client.embeddings.create = _fake_embed

    texts = [f"text_{i}" for i in range(2050)]

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        provider = OpenAICompatibleProvider(api_key="key")
        result = await provider.embed_batch(texts)

    assert len(result) == 2050
    assert call_count == 2  # 2048 + 2


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------

def test_supports_vision_true_by_default() -> None:
    mock_openai, _ = _make_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(api_key="key")
        assert p.supports_vision() is True


def test_supports_vision_false_when_overridden() -> None:
    mock_openai, _ = _make_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(api_key="key", supports_vision_flag=False)
        assert p.supports_vision() is False


def test_supports_tool_use_always_true() -> None:
    mock_openai, _ = _make_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider
        p = OpenAICompatibleProvider(api_key="key")
        assert p.supports_tool_use() is True


# ---------------------------------------------------------------------------
# Helpers: async iterator for streaming
# ---------------------------------------------------------------------------

class _AsyncIterator:
    def __init__(self, items: list) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "_AsyncIterator":
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration
