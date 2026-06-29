"""Tests for the stream_tokens() protocol added to all LLM providers.

Covers:
  - FakeProvider emits word-by-word tokens and returns correct CompletionResponse
  - AnthropicProvider delegates to the Anthropic streaming API
  - All required provider classes expose an async stream_tokens method
  - AgentGraph emits token_chunk events during executor execution
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_provider_streams_word_by_word() -> None:
    """FakeProvider.stream_tokens emits each word and returns full content."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.fake import FakeProvider

    provider = FakeProvider(responses=["Hello world from streaming"])
    received_tokens: list[str] = []

    async def collect_token(chunk: str) -> None:
        received_tokens.append(chunk)

    req = CompletionRequest(messages=[Message(role="user", content="test")], model="")
    response = await provider.stream_tokens(req, collect_token)

    assert response.content == "Hello world from streaming"
    assert len(received_tokens) > 0, "At least one token must be emitted"
    # Joining tokens (stripped) must reconstruct the original text
    assert "".join(received_tokens).strip() == "Hello world from streaming"


@pytest.mark.asyncio
async def test_fake_provider_stream_tokens_returns_response_metadata() -> None:
    """FakeProvider.stream_tokens returns a proper CompletionResponse."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.fake import FakeProvider

    provider = FakeProvider(responses=["one two three"])
    tokens: list[str] = []

    async def collect(chunk: str) -> None:
        tokens.append(chunk)

    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="test-model")
    resp = await provider.stream_tokens(req, collect)

    assert resp.content == "one two three"
    assert resp.output_tokens == 3  # FakeProvider counts words
    assert resp.input_tokens == 10  # FakeProvider fixed value


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_provider_stream_tokens_calls_streaming_api() -> None:
    """AnthropicProvider.stream_tokens uses messages.stream and calls on_token per chunk."""
    from unittest.mock import AsyncMock, MagicMock

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, Message

    received: list[str] = []

    async def collect(chunk: str) -> None:
        received.append(chunk)

    # Build a fake streaming context manager
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=False)

    async def _fake_text_stream():
        for chunk in ["Hello", " ", "world"]:
            yield chunk

    mock_stream.text_stream = _fake_text_stream()

    mock_final = MagicMock()
    mock_final.usage.input_tokens = 10
    mock_final.usage.output_tokens = 5
    mock_stream.get_final_message = AsyncMock(return_value=mock_final)

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._client = MagicMock()
    provider._client.messages.stream = MagicMock(return_value=mock_stream)
    provider._default_model = "claude-3-haiku-20240307"

    req = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="claude-3-haiku-20240307",
    )
    response = await provider.stream_tokens(req, collect)

    assert "".join(received) == "Hello world"
    assert response.content == "Hello world"
    assert response.input_tokens == 10
    assert response.output_tokens == 5


@pytest.mark.asyncio
async def test_anthropic_provider_stream_tokens_falls_back_on_error() -> None:
    """AnthropicProvider.stream_tokens falls back to complete() on streaming error."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.base import CompletionRequest, CompletionResponse, Message

    fallback_response = CompletionResponse(
        content="fallback text",
        model="claude-3-haiku-20240307",
        input_tokens=5,
        output_tokens=2,
    )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._default_model = "claude-3-haiku-20240307"

    # Make messages.stream raise immediately
    mock_client = MagicMock()
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(side_effect=RuntimeError("stream unavailable"))
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_client.messages.stream = MagicMock(return_value=mock_stream)
    provider._client = mock_client

    tokens: list[str] = []

    async def collect(chunk: str) -> None:
        tokens.append(chunk)

    req = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="claude-3-haiku-20240307",
    )

    with patch.object(provider, "complete", AsyncMock(return_value=fallback_response)):
        resp = await provider.stream_tokens(req, collect)

    assert resp.content == "fallback text"
    # on_token should NOT have been called (stream failed before any chunks)
    assert tokens == []


# ---------------------------------------------------------------------------
# Protocol compliance — all required classes must have stream_tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_required_providers_have_stream_tokens() -> None:
    """FakeProvider and AnthropicProvider must expose an async stream_tokens method."""
    from app.providers.anthropic_provider import AnthropicProvider
    from app.providers.fake import FakeProvider

    for cls in (FakeProvider, AnthropicProvider):
        assert hasattr(cls, "stream_tokens"), f"{cls.__name__} missing stream_tokens()"
        method = getattr(cls, "stream_tokens")
        assert inspect.iscoroutinefunction(method), (
            f"{cls.__name__}.stream_tokens must be a coroutine function"
        )


@pytest.mark.asyncio
async def test_base_protocol_default_stream_tokens() -> None:
    """LLMProvider default stream_tokens calls complete() and emits full text once."""
    from app.providers.base import CompletionRequest, CompletionResponse, Message

    class _MinimalProvider:
        """Minimal class that satisfies LLMProvider structurally for this test."""

        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            return CompletionResponse(content="hello world", model="test")

        # Does NOT define stream_tokens — tests Protocol default via inheritance
        async def embed(self, request):  # type: ignore[override]
            raise NotImplementedError

        def supports_vision(self) -> bool:
            return False

        def supports_tool_use(self) -> bool:
            return False

    # Import the Protocol default implementation via mixin-style call
    from app.providers.base import LLMProvider

    provider = _MinimalProvider()
    tokens: list[str] = []

    async def collect(chunk: str) -> None:
        tokens.append(chunk)

    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="")
    # Call the Protocol's default implementation directly
    resp = await LLMProvider.stream_tokens(provider, req, collect)  # type: ignore[arg-type]

    assert resp.content == "hello world"
    assert tokens == ["hello world"]  # default emits the whole text once


# ---------------------------------------------------------------------------
# AgentGraph emits token_chunk events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_emits_token_chunk_events() -> None:
    """AgentGraph._execute_step emits token_chunk events for each token fragment."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    emitted_events: list[dict] = []

    async def capture_emit(event: dict) -> None:
        emitted_events.append(event)

    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["Send a greeting"]}']),
        executor=FakeProvider(responses=["Hello, how can I help you today?"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
    )

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    await graph.run(goal="Say hello", tenant_ctx=ctx, event_callback=capture_emit)

    token_events = [e for e in emitted_events if e.get("type") == "token_chunk"]
    assert len(token_events) > 0, (
        f"No token_chunk events emitted. Event types seen: "
        f"{[e.get('type') for e in emitted_events]}"
    )

    for evt in token_events:
        assert "token" in evt, f"token_chunk event missing 'token' field: {evt}"
        assert "step" in evt, f"token_chunk event missing 'step' field: {evt}"
        assert "cumulative" in evt, f"token_chunk event missing 'cumulative' field: {evt}"

    # The cumulative text of the last token_chunk should reconstruct the full output
    last_token_evt = token_events[-1]
    assert "Hello" in last_token_evt["cumulative"] or len(last_token_evt["cumulative"]) > 0


@pytest.mark.asyncio
async def test_goal_service_does_not_persist_token_chunk_events() -> None:
    """GoalService._dispatch_event must NOT append token_chunk to record.events."""
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService
    from app.tenancy.context import PlanTier, TenantContext

    svc = GoalService()
    tenant_ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    record = GoalRecord(
        goal_id="g1",
        goal_text="test goal",
        status=GoalStatus.EXECUTING,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at="2024-01-01T00:00:00Z",
    )
    svc._goals["g1"] = record

    # Dispatch a regular event — should be stored
    await svc._dispatch_event("g1", {"type": "step_started", "step": "Do something"}, tenant_ctx)
    assert any(e.get("type") == "step_started" for e in record.events)

    events_before = len(record.events)

    # Dispatch a token_chunk event — must NOT be stored
    await svc._dispatch_event(
        "g1",
        {"type": "token_chunk", "step": "Do something", "token": "Hello", "cumulative": "Hello"},
        tenant_ctx,
    )
    assert len(record.events) == events_before, (
        "token_chunk events must not be appended to record.events"
    )
