"""Prompted tool-calling fallback for OpenAI-compatible servers.

When a server has no native tool parser (e.g. a vLLM started without
--enable-auto-tool-choice/--tool-call-parser), complete() falls back to prompting
the tools and parsing the tool call from the text. These tests cover the pure
parser and the end-to-end fallback (native path raises → prompted path used).
"""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import CompletionRequest, Message, ToolDefinition
from app.providers.openai_compatible import (
    _extract_json_objects,
    parse_prompted_tool_calls,
)

_TOOLS = {"get_weather", "search"}


# ── pure parser ───────────────────────────────────────────────────────────────


def test_parse_plain_tool_json() -> None:
    calls = parse_prompted_tool_calls('{"tool": "get_weather", "arguments": {"city": "Paris"}}', _TOOLS)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_weather"
    assert calls[0]["input"] == {"city": "Paris"}
    assert calls[0]["id"].startswith("call_")


def test_parse_strips_think_block() -> None:
    text = '<think>The user wants weather. I will call get_weather.</think>\n\n{"tool":"get_weather","arguments":{"city":"Paris"}}'
    calls = parse_prompted_tool_calls(text, _TOOLS)
    assert calls[0]["input"] == {"city": "Paris"}


def test_parse_accepts_name_and_input_aliases() -> None:
    calls = parse_prompted_tool_calls('{"name":"search","input":{"q":"cats"}}', _TOOLS)
    assert calls[0]["name"] == "search"
    assert calls[0]["input"] == {"q": "cats"}


def test_parse_ignores_unknown_tool_names() -> None:
    assert parse_prompted_tool_calls('{"tool":"delete_everything","arguments":{}}', _TOOLS) == []


def test_parse_no_tool_returns_empty() -> None:
    assert parse_prompted_tool_calls("The weather in Paris is sunny today.", _TOOLS) == []


def test_parse_handles_prose_around_json() -> None:
    text = 'Sure! ```json\n{"tool": "get_weather", "arguments": {"city": "Rome"}}\n``` done'
    calls = parse_prompted_tool_calls(text, _TOOLS)
    assert calls[0]["input"] == {"city": "Rome"}


def test_extract_json_handles_nested_and_braces_in_strings() -> None:
    objs = _extract_json_objects('{"a": {"b": 1}, "c": "has } brace"}')
    assert objs == [{"a": {"b": 1}, "c": "has } brace"}]


# ── end-to-end fallback via complete() ────────────────────────────────────────


def _make_openai_module(fail_native: bool, text_response: str) -> tuple[Any, Any]:
    """A fake openai module whose chat.create raises for tool kwargs (no parser)
    and returns *text_response* for the plain prompted call."""
    mock_client = MagicMock()

    async def _create(**kwargs: Any) -> Any:
        if fail_native and ("tools" in kwargs or "tool_choice" in kwargs):
            raise RuntimeError('400 "auto" tool choice requires --tool-call-parser to be set')
        msg = MagicMock()
        msg.content = text_response
        msg.tool_calls = None
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp = MagicMock()
        resp.choices = [choice]
        resp.model = "Qwen/Qwen3.5-4B"
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        return resp

    mock_client.chat.completions.create = _create
    mock_openai = MagicMock()
    mock_openai.AsyncOpenAI = MagicMock(return_value=mock_client)
    return mock_openai, mock_client


@pytest.mark.asyncio
async def test_complete_uses_prompted_fallback_when_native_tools_unavailable() -> None:
    mock_openai, _ = _make_openai_module(
        fail_native=True,
        text_response='<think>need weather</think>\n{"tool":"get_weather","arguments":{"city":"Paris"}}',
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="k", default_model="Qwen/Qwen3.5-4B")
        resp = await provider.complete(
            CompletionRequest(
                model="Qwen/Qwen3.5-4B",
                messages=[Message(role="user", content="weather in Paris?")],
                max_tokens=256,
                tools=[
                    ToolDefinition(
                        name="get_weather",
                        description="weather for a city",
                        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                    )
                ],
            )
        )
    assert resp.tool_calls
    assert resp.tool_calls[0]["name"] == "get_weather"
    assert resp.tool_calls[0]["input"] == {"city": "Paris"}
    assert resp.stop_reason == "tool_use"
    assert resp.content == ""  # a tool call, not a text answer


@pytest.mark.asyncio
async def test_complete_prompted_fallback_no_tool_returns_text() -> None:
    mock_openai, _ = _make_openai_module(
        fail_native=True, text_response="It is sunny in Paris today."
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="k", default_model="Qwen/Qwen3.5-4B")
        resp = await provider.complete(
            CompletionRequest(
                model="Qwen/Qwen3.5-4B",
                messages=[Message(role="user", content="hi")],
                max_tokens=256,
                tools=[
                    ToolDefinition(
                        name="get_weather", description="w", input_schema={"type": "object"}
                    )
                ],
            )
        )
    assert resp.tool_calls == []
    assert "sunny" in resp.content
    assert resp.stop_reason == "stop"
