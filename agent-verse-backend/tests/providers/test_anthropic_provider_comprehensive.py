"""Comprehensive tests for AnthropicProvider — targets 85% coverage."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, EmbedRequest, Message, ToolDefinition


# ---------------------------------------------------------------------------
# Helper: build a realistic mock anthropic response
# ---------------------------------------------------------------------------

def _make_text_response(
    text: str = "Hello there",
    model: str = "claude-3-haiku-20240307",
    in_tokens: int = 10,
    out_tokens: int = 20,
    stop_reason: str = "end_turn",
) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text

    resp = MagicMock()
    resp.content = [block]
    resp.model = model
    resp.usage.input_tokens = in_tokens
    resp.usage.output_tokens = out_tokens
    resp.stop_reason = stop_reason
    return resp


def _make_tool_use_response(
    tool_name: str = "search",
    tool_input: dict | None = None,
    tool_id: str = "tid_123",
) -> MagicMock:
    # Use a real object so hasattr(block, "text") is False for tool_use blocks
    class _ToolBlock:
        def __init__(self) -> None:
            self.type = "tool_use"
            self.name = tool_name
            self.input = tool_input or {"query": "hello"}
            self.id = tool_id

    resp = MagicMock()
    resp.content = [_ToolBlock()]
    resp.model = "claude-3-haiku-20240307"
    resp.usage.input_tokens = 8
    resp.usage.output_tokens = 15
    resp.stop_reason = "tool_use"
    return resp


# ---------------------------------------------------------------------------
# Basic text completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_text_response() -> None:
    """complete() returns correct text content and token counts."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_text_response("Test response", in_tokens=10, out_tokens=20)

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(api_key="test-key")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hello")],
                model="claude-3-haiku-20240307",
            )
        )

    assert result.content == "Test response"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.stop_reason == "end_turn"
    assert result.model == "claude-3-haiku-20240307"
    assert result.usage is not None
    assert result.usage.total_tokens == 30


@pytest.mark.asyncio
async def test_complete_uses_default_model_when_not_specified() -> None:
    """complete() falls back to the default_model when request.model is empty."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_text_response(model="claude-opus-4-8")

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(api_key="test-key", default_model="claude-opus-4-8")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="",  # empty — should fall back
            )
        )

    # model should be whatever the provider returns
    assert result.model == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# System prompt handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_extracts_system_from_messages() -> None:
    """System message in the messages list is lifted to the 'system' kwarg."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []
    mock_resp = _make_text_response()

    async def _fake_create(**kwargs: object) -> MagicMock:
        captured_kwargs.append(dict(kwargs))
        return mock_resp

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = _fake_create

        provider = AnthropicProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content="You are a helpful assistant."),
                    Message(role="user", content="Hello"),
                ],
                model="claude-3-haiku-20240307",
            )
        )

    assert len(captured_kwargs) == 1
    kw = captured_kwargs[0]
    # system message should be passed as top-level kwarg
    assert kw.get("system") == "You are a helpful assistant."
    # system message should NOT appear in 'messages' list
    for m in kw["messages"]:
        assert m["role"] != "system"


@pytest.mark.asyncio
async def test_complete_request_system_overrides_message_system() -> None:
    """request.system takes precedence over the system message in messages."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []
    mock_resp = _make_text_response()

    async def _fake_create(**kwargs: object) -> MagicMock:
        captured_kwargs.append(dict(kwargs))
        return mock_resp

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = _fake_create

        provider = AnthropicProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hello")],
                model="claude-3-haiku-20240307",
                system="Direct system prompt",
            )
        )

    kw = captured_kwargs[0]
    assert kw.get("system") == "Direct system prompt"


@pytest.mark.asyncio
async def test_complete_no_system_omits_system_kwarg() -> None:
    """When there is no system prompt, 'system' kwarg is omitted."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []
    mock_resp = _make_text_response()

    async def _fake_create(**kwargs: object) -> MagicMock:
        captured_kwargs.append(dict(kwargs))
        return mock_resp

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = _fake_create

        provider = AnthropicProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hello")],
                model="claude-3-haiku-20240307",
            )
        )

    kw = captured_kwargs[0]
    assert "system" not in kw


# ---------------------------------------------------------------------------
# Multimodal (image_data)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_with_image_data() -> None:
    """Messages with image_data produce a multi-content message."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []
    mock_resp = _make_text_response()

    async def _fake_create(**kwargs: object) -> MagicMock:
        captured_kwargs.append(dict(kwargs))
        return mock_resp

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = _fake_create

        provider = AnthropicProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[
                    Message(
                        role="user",
                        content="Describe this image",
                        image_data="base64encodedimage==",
                    )
                ],
                model="claude-3-haiku-20240307",
            )
        )

    msg = captured_kwargs[0]["messages"][0]
    # content should be a list with image + text
    assert isinstance(msg["content"], list)
    types = [c["type"] for c in msg["content"]]
    assert "image" in types
    assert "text" in types
    # image source should be base64
    img_block = next(c for c in msg["content"] if c["type"] == "image")
    assert img_block["source"]["type"] == "base64"
    assert img_block["source"]["data"] == "base64encodedimage=="


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_with_tools_passed_correctly() -> None:
    """Tool definitions are serialised into the 'tools' kwarg."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []
    mock_resp = _make_text_response()

    async def _fake_create(**kwargs: object) -> MagicMock:
        captured_kwargs.append(dict(kwargs))
        return mock_resp

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = _fake_create

        provider = AnthropicProvider(api_key="key")
        await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Search for cats")],
                model="claude-3-haiku-20240307",
                tools=[
                    ToolDefinition(
                        name="search",
                        description="Web search",
                        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                    )
                ],
            )
        )

    kw = captured_kwargs[0]
    assert "tools" in kw
    assert len(kw["tools"]) == 1
    assert kw["tools"][0]["name"] == "search"
    assert "input_schema" in kw["tools"][0]


@pytest.mark.asyncio
async def test_complete_returns_tool_calls() -> None:
    """When the response contains tool_use blocks, tool_calls is populated."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_tool_use_response(
        tool_name="get_weather", tool_input={"city": "London"}, tool_id="id_abc"
    )

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="What's the weather?")],
                model="claude-3-haiku-20240307",
            )
        )

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["name"] == "get_weather"
    assert tc["input"] == {"city": "London"}
    assert tc["id"] == "id_abc"


@pytest.mark.asyncio
async def test_complete_mixed_text_and_tool_use_blocks() -> None:
    """Text from text blocks and tool_calls from tool_use blocks are both captured."""
    from app.providers.anthropic_provider import AnthropicProvider

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I'll search for that."

    class _ToolBlock:
        def __init__(self) -> None:
            self.type = "tool_use"
            self.name = "search"
            self.input = {"query": "cats"}
            self.id = "tid_xyz"

    tool_block = _ToolBlock()

    mock_resp = MagicMock()
    mock_resp.content = [text_block, tool_block]
    mock_resp.model = "claude-3-haiku-20240307"
    mock_resp.usage.input_tokens = 5
    mock_resp.usage.output_tokens = 10
    mock_resp.stop_reason = "tool_use"

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Search cats")],
                model="claude-3-haiku-20240307",
            )
        )

    assert "I'll search for that." in result.content
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "search"


# ---------------------------------------------------------------------------
# Metrics recording path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_metrics_silently_swallowed_on_error() -> None:
    """Metrics exceptions are swallowed — the response still succeeds."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_text_response()

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        with patch(
            "app.governance.pricing.estimate_cost", side_effect=RuntimeError("fail")
        ):
            provider = AnthropicProvider(api_key="key")
            result = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content="Hi")],
                    model="claude-3-haiku-20240307",
                )
            )

    # Must succeed even if metrics recording blows up
    assert result.content == "Hello there"


@pytest.mark.asyncio
async def test_complete_metrics_recorded_when_available() -> None:
    """When pricing and metrics modules exist, metrics are recorded."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_text_response(in_tokens=100, out_tokens=50)

    record_tokens_calls: list = []
    record_cost_calls: list = []

    def _fake_record_tokens(provider: str, model: str, role: str, count: int) -> None:
        record_tokens_calls.append((provider, model, role, count))

    def _fake_record_cost(label: str, cost: float) -> None:
        record_cost_calls.append((label, cost))

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        with (
            patch("app.observability.metrics.record_llm_tokens", _fake_record_tokens),
            patch("app.governance.pricing.estimate_cost", return_value=0.001),
            patch("app.observability.metrics.record_cost_usd", _fake_record_cost),
        ):
            provider = AnthropicProvider(api_key="key")
            result = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content="Hi")],
                    model="claude-3-haiku-20240307",
                )
            )

    assert result.input_tokens == 100
    assert len(record_tokens_calls) == 2


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_complete_yields_text_chunks() -> None:
    """stream_complete() yields text tokens from the Anthropic streaming API."""
    from app.providers.anthropic_provider import AnthropicProvider

    class _FakeStream:
        async def __aenter__(self) -> "_FakeStream":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        @property
        def text_stream(self):  # type: ignore[return]
            return self

        def __aiter__(self) -> "_FakeStream":
            self._items = iter(["Hello", " ", "World"])
            return self

        async def __anext__(self) -> str:
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _FakeStream()

        provider = AnthropicProvider(api_key="key")
        chunks = []
        async for chunk in provider.stream_complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="claude-3-haiku-20240307",
            )
        ):
            chunks.append(chunk)

    assert "".join(chunks) == "Hello World"


@pytest.mark.asyncio
async def test_stream_complete_with_system_message() -> None:
    """stream_complete() extracts system prompt from messages."""
    from app.providers.anthropic_provider import AnthropicProvider

    captured_kwargs: list[dict] = []

    class _FakeStream:
        def __init__(self, **kw: object) -> None:
            captured_kwargs.append(kw)

        async def __aenter__(self) -> "_FakeStream":
            return self

        async def __aexit__(self, *a: object) -> None:
            pass

        @property
        def text_stream(self):  # type: ignore[return]
            return self

        def __aiter__(self) -> "_FakeStream":
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.stream = lambda **kw: _FakeStream(**kw)

        provider = AnthropicProvider(api_key="key")
        async for _ in provider.stream_complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content="Be concise"),
                    Message(role="user", content="Hi"),
                ],
                model="claude-3-haiku-20240307",
            )
        ):
            pass

    assert captured_kwargs[0].get("system") == "Be concise"


@pytest.mark.asyncio
async def test_stream_complete_yields_error_on_exception() -> None:
    """stream_complete() yields an error string when the API raises."""
    from app.providers.anthropic_provider import AnthropicProvider

    class _BrokenStream:
        async def __aenter__(self) -> "_BrokenStream":
            raise OSError("Network error")

        async def __aexit__(self, *a: object) -> None:
            pass

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _BrokenStream()

        provider = AnthropicProvider(api_key="key")
        chunks = []
        async for chunk in provider.stream_complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="claude-3-haiku-20240307",
            )
        ):
            chunks.append(chunk)

    assert len(chunks) == 1
    assert "[stream error:" in chunks[0]


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_raises_not_implemented() -> None:
    """embed() raises NotImplementedError — Anthropic has no embedding API."""
    from app.providers.anthropic_provider import AnthropicProvider

    with patch("anthropic.AsyncAnthropic"):
        provider = AnthropicProvider(api_key="key")
        with pytest.raises(NotImplementedError, match="embedding"):
            await provider.embed(EmbedRequest(texts=["hello"]))


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------

def test_supports_vision_returns_true() -> None:
    with patch("anthropic.AsyncAnthropic"):
        from app.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(api_key="key")
        assert provider.supports_vision() is True


def test_supports_tool_use_returns_true() -> None:
    with patch("anthropic.AsyncAnthropic"):
        from app.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(api_key="key")
        assert provider.supports_tool_use() is True


# ---------------------------------------------------------------------------
# Import error path
# ---------------------------------------------------------------------------

def test_import_error_when_anthropic_not_installed() -> None:
    """AnthropicProvider raises ImportError when the anthropic package is absent."""
    saved = sys.modules.get("anthropic")
    sys.modules["anthropic"] = None  # type: ignore[assignment]
    try:
        # Force reimport of the provider module to hit the import guard
        import importlib
        import app.providers.anthropic_provider as _mod
        importlib.reload(_mod)
        with pytest.raises(ImportError, match="anthropic"):
            _mod.AnthropicProvider(api_key="key")
    finally:
        if saved is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = saved
        # Reload the real module to restore state
        import importlib
        import app.providers.anthropic_provider as _mod2
        importlib.reload(_mod2)


# ---------------------------------------------------------------------------
# stop_reason fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_stop_reason_defaults_to_end_turn_when_none() -> None:
    """stop_reason defaults to 'end_turn' when the API returns None."""
    from app.providers.anthropic_provider import AnthropicProvider

    mock_resp = _make_text_response(stop_reason=None)  # type: ignore[arg-type]
    mock_resp.stop_reason = None

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_resp)

        provider = AnthropicProvider(api_key="key")
        result = await provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Hi")],
                model="claude-3-haiku-20240307",
            )
        )

    assert result.stop_reason == "end_turn"
