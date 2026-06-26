"""Tests for FakeProvider streaming and supports_streaming."""
from __future__ import annotations

import pytest

from app.providers.base import CompletionRequest, Message
from app.providers.fake import FakeProvider


def _req(text: str = "test") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=text)],
        model="fake-model",
        max_tokens=50,
    )


@pytest.mark.asyncio
async def test_stream_complete_yields_tokens():
    """stream_complete yields at least one non-empty token."""
    provider = FakeProvider(responses=["Hello world"])
    tokens = []
    async for token in provider.stream_complete(_req()):
        tokens.append(token)
    assert len(tokens) > 0
    assert all(isinstance(t, str) for t in tokens)


def test_supports_streaming_returns_true():
    """FakeProvider.supports_streaming returns True."""
    provider = FakeProvider()
    assert provider.supports_streaming() is True


@pytest.mark.asyncio
async def test_stream_complete_produces_non_empty_output():
    """Joined tokens from stream_complete are non-empty."""
    provider = FakeProvider(responses=["I am a fake response"])
    output = ""
    async for token in provider.stream_complete(_req()):
        output += token
    assert len(output.strip()) > 0


@pytest.mark.asyncio
async def test_stream_complete_cycles_through_responses():
    """Each call to stream_complete advances the response index."""
    provider = FakeProvider(responses=["first response", "second response"])

    first_tokens: list[str] = []
    async for token in provider.stream_complete(_req()):
        first_tokens.append(token)

    second_tokens: list[str] = []
    async for token in provider.stream_complete(_req()):
        second_tokens.append(token)

    first_text = "".join(first_tokens).strip()
    second_text = "".join(second_tokens).strip()
    assert "first" in first_text
    assert "second" in second_text


@pytest.mark.asyncio
async def test_stream_complete_words_sum_to_full_response():
    """Concatenated stream tokens reconstruct the full response (modulo spacing)."""
    response_text = "The quick brown fox"
    provider = FakeProvider(responses=[response_text])
    collected = ""
    async for token in provider.stream_complete(_req()):
        collected += token
    # After stripping trailing whitespace from each word + joining, should match
    assert collected.strip() == response_text


@pytest.mark.asyncio
async def test_stream_complete_works_as_async_generator():
    """stream_complete behaves as a proper async generator (supports async for)."""
    provider = FakeProvider(responses=["alpha beta gamma"])
    tokens: list[str] = []

    gen = provider.stream_complete(_req())
    async for token in gen:
        tokens.append(token)

    assert tokens == ["alpha ", "beta ", "gamma "]
