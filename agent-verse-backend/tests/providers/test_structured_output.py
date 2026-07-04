"""Tests for provider structured output support — Phase 3 Track A."""

import json

import pytest


class TestCompletionRequestResponseSchema:
    def test_response_schema_field_exists(self) -> None:
        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(
            messages=[Message(role="user", content="test")],
            model="test",
            response_schema={"type": "object", "properties": {"steps": {"type": "array"}}},
        )
        assert req.response_schema is not None
        assert "steps" in req.response_schema["properties"]

    def test_response_schema_defaults_none(self) -> None:
        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(messages=[Message(role="user", content="x")], model="m")
        assert req.response_schema is None


class TestFakeProviderStructuredOutput:
    def test_fake_provider_supports_structured_output(self) -> None:
        from app.providers.fake import FakeProvider

        fp = FakeProvider(responses=["test"])
        assert fp.supports_structured_output() is True

    @pytest.mark.asyncio
    async def test_fake_provider_returns_json_when_schema_set(self) -> None:
        from app.providers.base import CompletionRequest, Message
        from app.providers.fake import FakeProvider

        fp = FakeProvider(responses=["not json"])
        req = CompletionRequest(
            messages=[Message(role="user", content="test")],
            model="test",
            response_schema={
                "type": "object",
                "properties": {"success": {"type": "boolean"}},
                "required": ["success"],
            },
        )
        resp = await fp.complete(req)
        data = json.loads(resp.content)
        assert "success" in data

    @pytest.mark.asyncio
    async def test_fake_records_response_schema(self) -> None:
        """Plan spec test: call_history records the request with its schema."""
        from app.providers.base import CompletionRequest, Message
        from app.providers.fake import FakeProvider

        fake = FakeProvider(responses=['{"steps": []}'])
        schema = {"type": "object", "properties": {"steps": {"type": "array"}}}
        await fake.complete(
            CompletionRequest(
                messages=[Message(role="user", content="plan it")],
                model="x",
                response_schema=schema,
            )
        )
        assert fake.call_history[-1].response_schema == schema
        assert fake.supports_structured_output() is True

    @pytest.mark.asyncio
    async def test_fake_passthrough_when_already_json(self) -> None:
        """If the scripted response is already JSON, it should pass through unchanged."""
        from app.providers.base import CompletionRequest, Message
        from app.providers.fake import FakeProvider

        scripted = '{"steps": ["do x", "do y"]}'
        fp = FakeProvider(responses=[scripted])
        req = CompletionRequest(
            messages=[Message(role="user", content="plan")],
            model="test",
            response_schema={
                "type": "object",
                "properties": {"steps": {"type": "array"}},
            },
        )
        resp = await fp.complete(req)
        assert resp.content == scripted
