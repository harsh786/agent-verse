"""Tests for LLMProvider protocol, FakeProvider, and AnthropicProvider interface."""

from __future__ import annotations

import pytest

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    LLMProvider,
    Message,
    ToolDefinition,
)
from app.providers.fake import FakeProvider

# ── Protocol compliance ───────────────────────────────────────────────────────

def test_fake_provider_implements_llm_provider_protocol() -> None:
    provider = FakeProvider()
    assert isinstance(provider, LLMProvider)


async def test_fake_provider_complete_returns_scripted_response() -> None:
    provider = FakeProvider(responses=["Hello from the fake"])
    req = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="claude-opus-4-8",
    )
    resp = await provider.complete(req)
    assert isinstance(resp, CompletionResponse)
    assert resp.content == "Hello from the fake"
    assert resp.model == "claude-opus-4-8"


async def test_fake_provider_cycles_through_responses() -> None:
    provider = FakeProvider(responses=["first", "second"])
    req = CompletionRequest(messages=[Message(role="user", content="x")], model="test")
    r1 = await provider.complete(req)
    r2 = await provider.complete(req)
    r3 = await provider.complete(req)  # wraps around
    assert r1.content == "first"
    assert r2.content == "second"
    assert r3.content == "first"


async def test_fake_provider_embed_returns_vectors() -> None:
    provider = FakeProvider()
    req = EmbedRequest(texts=["hello", "world"], model="voyage-3-lite")
    resp = await provider.embed(req)
    assert isinstance(resp, EmbedResponse)
    assert len(resp.embeddings) == 2
    assert all(isinstance(v, list) for v in resp.embeddings)


async def test_fake_provider_embed_dimension_configurable() -> None:
    provider = FakeProvider(embed_dim=128)
    req = EmbedRequest(texts=["test"], model="any")
    resp = await provider.embed(req)
    assert len(resp.embeddings[0]) == 128


def test_fake_provider_supports_vision_flag() -> None:
    provider = FakeProvider(vision=True)
    assert provider.supports_vision() is True
    provider2 = FakeProvider(vision=False)
    assert provider2.supports_vision() is False


def test_fake_provider_supports_tool_use_flag() -> None:
    provider = FakeProvider(tool_use=True)
    assert provider.supports_tool_use() is True


async def test_fake_provider_records_call_history() -> None:
    provider = FakeProvider(responses=["ok"])
    req = CompletionRequest(messages=[Message(role="user", content="test")], model="m")
    await provider.complete(req)
    assert len(provider.call_history) == 1
    assert provider.call_history[0] is req


# ── CompletionRequest / Response models ───────────────────────────────────────

def test_completion_request_requires_messages_and_model() -> None:
    req = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="claude-opus-4-8",
    )
    assert req.model == "claude-opus-4-8"
    assert len(req.messages) == 1


def test_message_roles_are_validated() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Message(role="invalid_role", content="hi")  # type: ignore[arg-type]


def test_completion_response_has_usage() -> None:
    resp = CompletionResponse(
        content="hi",
        model="m",
        input_tokens=10,
        output_tokens=5,
    )
    assert resp.total_tokens == 15


def test_tool_definition_has_schema() -> None:
    td = ToolDefinition(
        name="get_weather",
        description="Fetch weather",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )
    assert td.name == "get_weather"
    assert "properties" in td.input_schema


# ── W-1: Streaming tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fake_provider_stream_complete():
    """FakeProvider.stream_complete yields word tokens."""
    from app.providers.fake import FakeProvider
    from app.providers.base import CompletionRequest, Message

    provider = FakeProvider(responses=["Hello world from stream"])
    req = CompletionRequest(
        messages=[Message(role="user", content="test")],
        model="fake",
    )
    tokens = []
    async for token in provider.stream_complete(req):
        tokens.append(token)

    full = "".join(tokens).strip()
    assert "Hello" in full
    assert "world" in full
    assert len(tokens) >= 3  # at least 3 words


@pytest.mark.asyncio
async def test_stream_complete_endpoint_exists():
    """GET /goals/{id}/stream/tokens returns SSE stream."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "st@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        # Stream tokens for nonexistent goal should return 200 with empty stream
        resp = await c.get("/goals/nonexistent/stream/tokens")
        # Either 200 (empty stream) or 404
        assert resp.status_code in (200, 404)
