"""PROV-07: Gemini runs executor tool steps (function calling) and accepts images.

GeminiProvider raised NotImplementedError on tool-bearing requests and images,
so a Gemini-configured tenant could not run tool steps.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

from app.providers.base import CompletionRequest, Message, ToolDefinition


class _Obj:
    """A generic stand-in for a google-genai types class: keeps its kwargs."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _Part(_Obj):
    @classmethod
    def from_bytes(cls, *, data: bytes, mime_type: str) -> _Part:
        return cls(inline_data={"data": data, "mime_type": mime_type})

    @classmethod
    def from_function_response(cls, *, name: str, response: dict[str, Any]) -> _Part:
        return cls(function_response={"name": name, "response": response})


class _Types:
    GenerateContentConfig = _Obj
    FunctionDeclaration = _Obj
    Tool = _Obj
    Content = _Obj
    FunctionCall = _Obj
    Part = _Part


class _Models:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _provider(response: Any) -> tuple[Any, _Models]:
    from app.providers.gemini_provider import GeminiProvider

    models = _Models(response)
    provider = GeminiProvider.__new__(GeminiProvider)
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    provider._types = _Types
    provider._default_model = "gemini-2.5-pro"
    provider._embed_model = "e"
    return provider, models


_USAGE = SimpleNamespace(prompt_token_count=5, candidates_token_count=2)
_TOOL = ToolDefinition(
    name="jira_search",
    description="Search Jira issues",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


async def test_tool_definitions_are_sent_and_function_calls_returned() -> None:
    response = SimpleNamespace(
        text="",
        usage_metadata=_USAGE,
        function_calls=[SimpleNamespace(name="jira_search", args={"query": "bug"}, id="c1")],
    )
    provider, models = _provider(response)
    out = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="find the bug")],
            model="gemini-2.5-pro",
            system="be precise",
            tools=[_TOOL],
        )
    )
    assert out.tool_calls == [{"name": "jira_search", "input": {"query": "bug"}, "id": "c1"}]
    assert out.stop_reason == "tool_use"
    config = models.calls[0]["config"]
    [tool] = config.tools
    [decl] = tool.function_declarations
    assert decl.name == "jira_search" and decl.description == "Search Jira issues"
    assert "additionalProperties" not in decl.parameters  # unsupported by Gemini schemas
    assert config.system_instruction == "be precise"


async def test_tool_results_round_trip_as_function_responses() -> None:
    response = SimpleNamespace(text="done", usage_metadata=_USAGE, function_calls=None)
    provider, models = _provider(response)
    await provider.complete(
        CompletionRequest(
            messages=[
                Message(role="user", content="find the bug"),
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[{"id": "c1", "name": "jira_search", "input": {"query": "bug"}}],
                ),
                Message(role="tool", content='{"issues": 3}', tool_call_id="c1"),
            ],
            model="gemini-2.5-pro",
            tools=[_TOOL],
        )
    )
    contents = models.calls[0]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].function_call.name == "jira_search"
    assert contents[2].parts[0].function_response["name"] == "jira_search"


async def test_images_are_sent_as_inline_parts() -> None:
    response = SimpleNamespace(text="a cat", usage_metadata=_USAGE, function_calls=None)
    provider, models = _provider(response)
    img = base64.b64encode(b"\x89PNG fake").decode()
    out = await provider.complete(
        CompletionRequest(
            messages=[Message(role="user", content="what is this?", image_data=img)],
            model="gemini-2.5-pro",
        )
    )
    assert out.content == "a cat"
    parts = models.calls[0]["contents"][0].parts
    assert any(getattr(p, "inline_data", None) for p in parts)


def test_capabilities_are_advertised() -> None:
    provider, _ = _provider(None)
    assert provider.supports_tool_use() is True
    assert provider.supports_vision() is True
