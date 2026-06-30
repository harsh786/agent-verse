"""Tests for stream_tokens on OpenAI, Gemini, Voyage providers + base.py helpers.

Covers:
  - OpenAICompatibleProvider.stream_tokens (lines 152-187)
  - GeminiProvider.stream_tokens (new method)
  - VoyageProvider.stream_tokens (raises NotImplementedError)
  - LLMProvider.embed_batch default implementation (base.py 132-136)
  - embed_texts standalone function (base.py 155-163)

google-generativeai and voyageai are NOT installed in this environment;
all tests mock via sys.modules (same pattern as test_gemini_provider_comprehensive.py
and test_voyage_provider_comprehensive.py).
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── OpenAICompatibleProvider.stream_tokens ────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_stream_tokens_success() -> None:
    """Lines 152-187: stream_tokens happy path — tokens emitted, response built."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.openai_compatible import OpenAICompatibleProvider

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    # Build fake async-iterable chunks
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock(delta=MagicMock(content="Hello"))]
    chunk1.usage = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock(delta=MagicMock(content=" world"))]
    chunk2.usage = None

    # Final chunk with usage info
    chunk3 = MagicMock()
    chunk3.choices = [MagicMock(delta=MagicMock(content=None))]
    usage_mock = MagicMock()
    usage_mock.prompt_tokens = 5
    usage_mock.completion_tokens = 2
    chunk3.usage = usage_mock

    async def fake_stream(*args, **kwargs):
        for c in [chunk1, chunk2, chunk3]:
            yield c

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    provider._vision = True
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())
    provider._client = mock_client

    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="gpt-4o")
    resp = await provider.stream_tokens(req, collect)

    assert "Hello" in received
    assert " world" in received
    assert resp.content == "Hello world"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 2


@pytest.mark.asyncio
async def test_openai_stream_tokens_no_delta_content() -> None:
    """Lines 172-173: chunk with None delta content is skipped (no on_token call)."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.openai_compatible import OpenAICompatibleProvider

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    chunk_empty = MagicMock()
    chunk_empty.choices = [MagicMock(delta=MagicMock(content=None))]
    chunk_empty.usage = None

    async def fake_stream(*args, **kwargs):
        yield chunk_empty

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    provider._vision = True
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())
    provider._client = mock_client

    req = CompletionRequest(messages=[Message(role="user", content="test")], model="")
    resp = await provider.stream_tokens(req, collect)

    assert received == []
    assert resp.content == ""


@pytest.mark.asyncio
async def test_openai_stream_tokens_fallback_on_error() -> None:
    """Lines 181-185: streaming API raises → falls back to complete()."""
    from app.providers.base import CompletionRequest, CompletionResponse, Message
    from app.providers.openai_compatible import OpenAICompatibleProvider

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    provider._vision = True
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("API down"))
    provider._client = mock_client

    fallback = CompletionResponse(content="fallback text", model="gpt-4o")
    request = CompletionRequest(messages=[Message(role="user", content="hi")], model="")

    with patch.object(provider, "complete", AsyncMock(return_value=fallback)):
        resp = await provider.stream_tokens(request, collect)

    assert resp.content == "fallback text"
    assert received == []  # no tokens emitted before error


@pytest.mark.asyncio
async def test_openai_stream_tokens_no_choices_chunk() -> None:
    """Line 172: chunk.choices is empty → delta is None, no on_token call."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.openai_compatible import OpenAICompatibleProvider

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    chunk_no_choices = MagicMock()
    chunk_no_choices.choices = []
    chunk_no_choices.usage = None

    async def fake_stream(*args, **kwargs):
        yield chunk_no_choices

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    provider._vision = True
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())
    provider._client = mock_client

    req = CompletionRequest(messages=[Message(role="user", content="x")], model="")
    resp = await provider.stream_tokens(req, collect)

    assert received == []


# ── GeminiProvider.stream_tokens ─────────────────────────────────────────────


def _make_gemini_provider(mock_genai: MagicMock):
    """Build a GeminiProvider instance without calling __init__ (no SDK needed)."""
    from app.providers.gemini_provider import GeminiProvider
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._genai = mock_genai
    provider._default_model = "gemini-1.5-pro"
    provider._embed_model = "models/embedding-001"
    return provider


@pytest.mark.asyncio
async def test_gemini_stream_tokens_success() -> None:
    """Lines 100-123: stream_tokens emits tokens via async for loop."""
    from app.providers.base import CompletionRequest, Message

    mock_genai = MagicMock()
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    chunk1 = MagicMock()
    chunk1.text = "Hello"
    chunk2 = MagicMock()
    chunk2.text = " Gemini"

    async def fake_gen(*args, **kwargs):
        yield chunk1
        yield chunk2

    mock_model.generate_content_async = fake_gen

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    provider = _make_gemini_provider(mock_genai)
    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
    resp = await provider.stream_tokens(req, collect)

    assert "Hello" in received
    assert " Gemini" in received
    assert resp.content == "Hello Gemini"


@pytest.mark.asyncio
async def test_gemini_stream_tokens_fallback_on_error() -> None:
    """Line 120-121: stream raises → falls back to complete()."""
    from app.providers.base import CompletionRequest, CompletionResponse, Message

    mock_genai = MagicMock()
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    async def failing_gen(*args, **kwargs):
        raise RuntimeError("stream error")
        yield  # pragma: no cover

    mock_model.generate_content_async = failing_gen

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    provider = _make_gemini_provider(mock_genai)
    fallback = CompletionResponse(content="fallback", model="gemini-1.5-pro")
    with patch.object(provider, "complete", AsyncMock(return_value=fallback)):
        req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
        resp = await provider.stream_tokens(req, collect)

    assert resp.content == "fallback"


@pytest.mark.asyncio
async def test_gemini_stream_tokens_empty_text_chunk() -> None:
    """Lines 116-119: chunks with empty text are skipped."""
    from app.providers.base import CompletionRequest, Message

    mock_genai = MagicMock()
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    chunk_empty = MagicMock()
    chunk_empty.text = ""
    chunk_real = MagicMock()
    chunk_real.text = "answer"

    async def fake_gen(*args, **kwargs):
        yield chunk_empty
        yield chunk_real

    mock_model.generate_content_async = fake_gen

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    provider = _make_gemini_provider(mock_genai)
    req = CompletionRequest(messages=[Message(role="user", content="q")], model="")
    resp = await provider.stream_tokens(req, collect)

    assert received == ["answer"]
    assert resp.content == "answer"


@pytest.mark.asyncio
async def test_gemini_stream_tokens_with_system_prompt() -> None:
    """Lines 104-108: system prompt is prepended to the content."""
    from app.providers.base import CompletionRequest, Message

    mock_genai = MagicMock()
    mock_model = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    captured_prompt: list[str] = []

    async def fake_gen(prompt, **kwargs):
        captured_prompt.append(prompt)
        chunk = MagicMock()
        chunk.text = "ok"
        yield chunk

    mock_model.generate_content_async = fake_gen

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    provider = _make_gemini_provider(mock_genai)
    req = CompletionRequest(
        messages=[Message(role="user", content="hello")],
        model="",
        system="You are a helpful assistant",
    )
    resp = await provider.stream_tokens(req, collect)

    assert resp.content == "ok"
    assert "[System]: You are a helpful assistant" in captured_prompt[0]


# ── VoyageProvider.stream_tokens ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voyage_stream_tokens_raises_not_implemented() -> None:
    """VoyageProvider.stream_tokens always raises NotImplementedError."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.voyage_provider import VoyageProvider

    # Bypass __init__ entirely — stream_tokens only uses self.complete()
    provider = VoyageProvider.__new__(VoyageProvider)
    provider._client = MagicMock()
    provider._model = "voyage-2"

    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
    with pytest.raises(NotImplementedError, match="embeddings only"):
        await provider.stream_tokens(req, lambda chunk: None)


# ── Protocol compliance check ─────────────────────────────────────────────────


def test_all_providers_have_stream_tokens() -> None:
    """All LLM providers must expose an async stream_tokens method."""
    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.fake import FakeProvider
    from app.providers.gemini_provider import GeminiProvider
    from app.providers.openai_compatible import OpenAICompatibleProvider
    from app.providers.voyage_provider import VoyageProvider

    for cls in (FakeProvider, AnthropicProvider, OpenAICompatibleProvider,
                GeminiProvider, VoyageProvider):
        assert hasattr(cls, "stream_tokens"), f"{cls.__name__} missing stream_tokens"
        assert inspect.iscoroutinefunction(cls.stream_tokens), (
            f"{cls.__name__}.stream_tokens must be async"
        )


# ── base.py: LLMProvider.embed_batch default implementation (lines 132-136) ──


@pytest.mark.asyncio
async def test_base_embed_batch_default_fallback() -> None:
    """Lines 132-136: LLMProvider.embed_batch default calls embed() sequentially."""
    from app.providers.base import EmbedRequest, EmbedResponse, LLMProvider

    call_count = 0

    class _MinimalProvider:
        async def complete(self, req):
            raise NotImplementedError

        async def stream_tokens(self, req, on_token):
            raise NotImplementedError

        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            nonlocal call_count
            call_count += 1
            return EmbedResponse(embeddings=[[1.0, 2.0, 3.0]])

        def supports_vision(self) -> bool:
            return False

        def supports_tool_use(self) -> bool:
            return False

    provider = _MinimalProvider()
    # Call through the Protocol default (bypasses overrides)
    result = await LLMProvider.embed_batch(provider, ["text1", "text2"])  # type: ignore[arg-type]
    assert len(result) == 2
    assert call_count == 2  # called once per text
    assert all(len(emb) == 3 for emb in result)


@pytest.mark.asyncio
async def test_base_embed_batch_empty_input() -> None:
    """LLMProvider.embed_batch with empty list returns empty list without calling embed."""
    from app.providers.base import LLMProvider

    class _NoopProvider:
        async def complete(self, req): ...
        async def stream_tokens(self, req, on_token): ...
        async def embed(self, req):
            raise AssertionError("embed must not be called for empty input")
        def supports_vision(self): return False
        def supports_tool_use(self): return False

    provider = _NoopProvider()
    result = await LLMProvider.embed_batch(provider, [])  # type: ignore[arg-type]
    assert result == []


# ── base.py: embed_texts standalone helper (lines 155-163) ───────────────────


@pytest.mark.asyncio
async def test_embed_texts_with_provider() -> None:
    """Lines 155-158: embed_texts calls provider.embed() and returns embeddings."""
    from app.providers.base import EmbedResponse, embed_texts

    mock_provider = AsyncMock()
    mock_provider.embed = AsyncMock(return_value=EmbedResponse(embeddings=[[0.1, 0.2]]))

    result = await embed_texts(["hello world"], mock_provider)
    assert result == [[0.1, 0.2]]
    mock_provider.embed.assert_called_once()


@pytest.mark.asyncio
async def test_embed_texts_provider_raises_not_implemented() -> None:
    """Lines 159-160: embed() raises NotImplementedError → empty embeddings."""
    from app.providers.base import embed_texts

    mock_provider = AsyncMock()
    mock_provider.embed = AsyncMock(side_effect=NotImplementedError("no embedding"))

    result = await embed_texts(["text"], mock_provider)
    assert result == [[]]  # empty fallback per text


@pytest.mark.asyncio
async def test_embed_texts_no_provider() -> None:
    """Lines 162-163: embed_texts(provider=None) returns empty embeddings."""
    from app.providers.base import embed_texts

    result = await embed_texts(["a", "b"], provider=None)
    assert result == [[], []]


# ── FakeProvider: embed_batch and supports_embeddings (lines 104, 119) ────────


@pytest.mark.asyncio
async def test_fake_provider_embed_batch() -> None:
    """Line 104: FakeProvider.embed_batch returns deterministic embeddings."""
    from app.providers.fake import FakeProvider

    provider = FakeProvider()
    result = await provider.embed_batch(["hello", "world"])
    assert len(result) == 2
    assert all(isinstance(v, float) for v in result[0])


def test_fake_provider_supports_embeddings() -> None:
    """Line 119: FakeProvider.supports_embeddings() returns False."""
    from app.providers.fake import FakeProvider

    provider = FakeProvider()
    assert provider.supports_embeddings() is False


# ── AnthropicProvider.stream_tokens: system skip + image_data paths ──────────


@pytest.mark.asyncio
async def test_anthropic_stream_tokens_with_system_message() -> None:
    """Line 179: system-role messages are skipped in stream_tokens."""
    from unittest.mock import AsyncMock, MagicMock

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, Message

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)

    async def _text_stream():
        yield "Answer"

    mock_stream.text_stream = _text_stream()
    mock_final = MagicMock()
    mock_final.usage.input_tokens = 5
    mock_final.usage.output_tokens = 1
    mock_stream.get_final_message = AsyncMock(return_value=mock_final)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._default_model = "claude-3-haiku-20240307"
    mock_client = MagicMock()
    mock_client.messages.stream = MagicMock(return_value=mock_stream)
    provider._client = mock_client

    # Include a system message (line 179: skipped via continue)
    req = CompletionRequest(
        messages=[
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hi"),
        ],
        model="claude-3-haiku-20240307",
    )
    resp = await provider.stream_tokens(req, collect)
    assert "Answer" in received
    assert resp.input_tokens == 5


@pytest.mark.asyncio
async def test_anthropic_stream_tokens_with_image_data() -> None:
    """Lines 181-197: message with image_data is formatted as vision message."""
    from unittest.mock import AsyncMock, MagicMock

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, Message

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    captured_kwargs: list[dict] = []

    def _capture_stream(**kwargs):
        captured_kwargs.append(kwargs)
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        async def _text():
            yield "Vision answer"

        mock_stream.text_stream = _text()
        mock_final = MagicMock()
        mock_final.usage.input_tokens = 10
        mock_final.usage.output_tokens = 3
        mock_stream.get_final_message = AsyncMock(return_value=mock_final)
        return mock_stream

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._default_model = "claude-3-haiku-20240307"
    mock_client = MagicMock()
    mock_client.messages.stream = MagicMock(side_effect=_capture_stream)
    provider._client = mock_client

    req = CompletionRequest(
        messages=[Message(role="user", content="What's in this image?", image_data="base64data")],
        model="claude-3-haiku-20240307",
    )
    resp = await provider.stream_tokens(req, collect)
    assert "Vision answer" in received
    # Verify the message was formatted with image content blocks
    assert len(captured_kwargs) == 1
    msgs = captured_kwargs[0]["messages"]
    assert isinstance(msgs[0]["content"], list)  # vision format


@pytest.mark.asyncio
async def test_anthropic_stream_tokens_with_system_and_tools() -> None:
    """Lines 212, 214: stream_tokens with system prompt and tools in kwargs."""
    from unittest.mock import AsyncMock, MagicMock

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, Message, ToolDefinition

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    captured_kwargs: list[dict] = []

    def _capture_stream(**kwargs):
        captured_kwargs.append(kwargs)
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        async def _text():
            yield "Tool answer"

        mock_stream.text_stream = _text()
        mock_final = MagicMock()
        mock_final.usage.input_tokens = 8
        mock_final.usage.output_tokens = 2
        mock_stream.get_final_message = AsyncMock(return_value=mock_final)
        return mock_stream

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._default_model = "claude-3-haiku-20240307"
    mock_client = MagicMock()
    mock_client.messages.stream = MagicMock(side_effect=_capture_stream)
    provider._client = mock_client

    tool = ToolDefinition(name="search", description="Search the web", input_schema={"type": "object"})
    req = CompletionRequest(
        messages=[Message(role="user", content="Search for something")],
        model="claude-3-haiku-20240307",
        system="You use tools when needed",
        tools=[tool],
    )
    resp = await provider.stream_tokens(req, collect)
    assert "Tool answer" in received
    assert "system" in captured_kwargs[0]
    assert "tools" in captured_kwargs[0]
