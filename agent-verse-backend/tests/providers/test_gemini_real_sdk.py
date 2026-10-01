"""GEMINI-SDK: the Gemini provider against the REAL ``google-genai`` SDK, offline.

``google-genai`` was only an optional extra, and every Gemini test replaced the
SDK with a fake module, so nothing showed that the request the provider builds
is one the real SDK accepts and serialises. Here the real client builds and sends
the request, and the HTTP transport is intercepted (respx; any request that is
not mocked raises), so the tool-call wire format is checked without a network.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx

genai = pytest.importorskip("google.genai")  # a runtime dependency: never skipped in CI

from app.providers.base import CompletionRequest, Message, ToolDefinition
from app.providers.gemini_provider import GeminiProvider

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()

_TOOL = ToolDefinition(
    name="get_weather",
    description="Weather for a city",
    input_schema={
        "type": "object",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "additionalProperties": False,
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
)


def _request() -> CompletionRequest:
    return CompletionRequest(
        model="gemini-2.5-pro",
        system="You are terse.",
        tools=[_TOOL],
        messages=[
            Message(role="user", content="Weather in Paris?", image_data=_PNG),
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}],
            ),
            Message(role="tool", content="18C and sunny", tool_call_id="call_1"),
        ],
    )


def test_the_real_sdk_is_installed() -> None:
    assert hasattr(genai, "Client") and hasattr(genai.types, "FunctionDeclaration")


def test_request_objects_are_real_sdk_types() -> None:
    provider = GeminiProvider(api_key="offline-test-key")
    request = _request()
    contents = provider._contents(request)
    config = provider._config(request)

    assert all(isinstance(c, genai.types.Content) for c in contents)
    assert isinstance(config, genai.types.GenerateContentConfig)
    decl = config.tools[0].function_declarations[0]
    assert decl.name == "get_weather"
    # Unsupported JSON-schema keys are stripped before the SDK validates them.
    schema = decl.parameters.model_dump(exclude_none=True)
    assert "additionalProperties" not in json.dumps(schema)
    assert contents[1].parts[0].function_call.args == {"city": "Paris"}
    assert contents[2].parts[0].function_response.name == "get_weather"


@respx.mock(assert_all_called=True)
async def test_tool_call_round_trip_over_the_real_sdk_transport(
    respx_mock: respx.MockRouter,
) -> None:
    captured: dict[str, Any] = {}

    def _reply(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "id": "fc-9",
                                        "name": "get_weather",
                                        "args": {"city": "Lyon"},
                                    }
                                }
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3},
            },
        )

    respx_mock.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(
        side_effect=_reply
    )
    provider = GeminiProvider(api_key="offline-test-key")
    response = await provider.complete(_request())

    assert "models/gemini-2.5-pro:generateContent" in captured["url"]
    body = captured["body"]
    decl = body["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "get_weather" and decl["description"] == "Weather for a city"
    assert "additionalProperties" not in json.dumps(decl) and "$schema" not in json.dumps(decl)
    assert body["systemInstruction"]["parts"][0]["text"] == "You are terse."
    user, model, tool = body["contents"]
    assert user["role"] == "user" and user["parts"][0]["text"] == "Weather in Paris?"
    assert user["parts"][1]["inlineData"]["mimeType"] == "image/png"
    assert model["role"] == "model"
    assert model["parts"][0]["functionCall"] == {"name": "get_weather", "args": {"city": "Paris"}}
    assert tool["parts"][0]["functionResponse"] == {
        "name": "get_weather",
        "response": {"result": "18C and sunny"},
    }

    assert response.stop_reason == "tool_use"
    assert response.tool_calls == [{"name": "get_weather", "input": {"city": "Lyon"}, "id": "fc-9"}]
    assert (response.input_tokens, response.output_tokens) == (11, 3)
