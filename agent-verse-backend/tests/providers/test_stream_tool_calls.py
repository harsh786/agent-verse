"""Streaming must not drop structured tool calls, nor re-run a half-streamed call.

The executor streams every step (including tool steps) through ``stream_tokens``.
``AnthropicProvider.stream_tokens`` used to keep only text deltas, so the
``tool_use`` blocks the model returned were lost and the agent loop never
dispatched a tool on Anthropic. Both OpenAI-compatible and Anthropic also used
to call ``complete()`` after a stream failed part-way, emitting a second answer
after the partial one with no reset.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import CompletionRequest, CompletionResponse, Message, ToolDefinition
from app.providers.openai_compatible import OpenAICompatibleProvider


def _anthropic(stream_factory: object) -> AnthropicProvider:
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._default_model = "claude-x"
    client = MagicMock()
    client.messages.stream = MagicMock(side_effect=stream_factory)
    provider._client = client
    return provider


def _tool_req() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="find the issue")],
        model="",
        tools=[ToolDefinition(name="jira_search", description="d", input_schema={})],
    )


async def test_anthropic_stream_tokens_surfaces_tool_use_blocks() -> None:
    received: list[str] = []

    async def on_token(t: str) -> None:
        received.append(t)

    def _stream(**_kw: object) -> MagicMock:
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)

        async def _text():  # type: ignore[no-untyped-def]
            yield "Searching. "

        s.text_stream = _text()
        final = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Searching. "),
                SimpleNamespace(
                    type="tool_use", id="tu_1", name="jira_search", input={"q": "bug"}
                ),
            ],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            stop_reason="tool_use",
            model="claude-x-served",
        )
        s.get_final_message = AsyncMock(return_value=final)
        return s

    resp = await _anthropic(_stream).stream_tokens(_tool_req(), on_token)

    assert resp.tool_calls == [{"name": "jira_search", "input": {"q": "bug"}, "id": "tu_1"}]
    assert resp.stop_reason == "tool_use"
    assert resp.content == "Searching. "
    assert resp.input_tokens == 11 and resp.output_tokens == 7
    assert resp.usage is not None and resp.usage.total_tokens == 18
    assert received == ["Searching. "]


async def test_anthropic_stream_failure_after_tokens_does_not_rerun_complete() -> None:
    async def on_token(_t: str) -> None:
        return None

    def _stream(**_kw: object) -> MagicMock:
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)

        async def _text():  # type: ignore[no-untyped-def]
            yield "partial"
            raise ConnectionError("stream dropped")

        s.text_stream = _text()
        return s

    provider = _anthropic(_stream)
    complete = AsyncMock(return_value=CompletionResponse(content="second answer", model="m"))
    with patch.object(provider, "complete", complete), pytest.raises(ConnectionError):
        await provider.stream_tokens(_tool_req(), on_token)
    complete.assert_not_awaited()


async def test_anthropic_stream_failure_before_tokens_still_falls_back() -> None:
    async def on_token(_t: str) -> None:
        return None

    def _stream(**_kw: object) -> MagicMock:
        raise ConnectionError("refused")

    provider = _anthropic(_stream)
    fb = CompletionResponse(content="fallback", model="m", tool_calls=[{"name": "x", "input": {}}])
    with patch.object(provider, "complete", AsyncMock(return_value=fb)):
        resp = await provider.stream_tokens(_tool_req(), on_token)
    assert resp.tool_calls == [{"name": "x", "input": {}}]


async def test_openai_stream_failure_after_tokens_does_not_rerun_complete() -> None:
    async def on_token(_t: str) -> None:
        return None

    chunk = MagicMock()
    chunk.choices = [MagicMock(delta=MagicMock(content="partial"))]
    chunk.usage = None

    async def _gen():  # type: ignore[no-untyped-def]
        yield chunk
        raise ConnectionError("stream dropped")

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    provider._vision = True
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_gen())
    provider._client = client

    complete = AsyncMock(return_value=CompletionResponse(content="second answer", model="m"))
    req = CompletionRequest(messages=[Message(role="user", content="hi")], model="gpt-4o")
    with patch.object(provider, "complete", complete), pytest.raises(ConnectionError):
        await provider.stream_tokens(req, on_token)
    complete.assert_not_awaited()


async def test_openai_stream_tokens_with_tools_uses_complete_tool_calls() -> None:
    """Tool steps take the non-streaming path, so tool_calls survive."""

    async def on_token(_t: str) -> None:
        return None

    provider = OpenAICompatibleProvider.__new__(OpenAICompatibleProvider)
    provider._default_model = "gpt-4o"
    fb = CompletionResponse(content="", model="m", tool_calls=[{"name": "jira_search", "input": {}}])
    with patch.object(provider, "complete", AsyncMock(return_value=fb)):
        resp = await provider.stream_tokens(_tool_req(), on_token)
    assert resp.tool_calls == [{"name": "jira_search", "input": {}}]
