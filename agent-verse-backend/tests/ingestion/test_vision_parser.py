# tests/ingestion/test_vision_parser.py
"""Vision parser must describe images using GPT-4V or Claude Vision."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.parsers.vision_parser import VisionParser, VisionParseResult


@pytest.fixture
def tiny_png_bytes():
    """1x1 white PNG for testing."""
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAAABJRU5ErkJggg=="
    return base64.b64decode(b64)


async def test_vision_parser_describes_image(tiny_png_bytes):
    """VisionParser must call vision model and return description."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(return_value="A white 1x1 pixel image.")):
        result = await parser.parse_image_bytes(
            image_bytes=tiny_png_bytes,
            source_name="test.png",
            prompt="Describe this image in detail.",
        )
    assert isinstance(result, VisionParseResult)
    assert result.description
    assert len(result.description) > 0


async def test_vision_parser_returns_structured_chunks(tiny_png_bytes):
    """VisionParseResult must produce chunks with image metadata."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(return_value="White pixel image")):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    chunks = result.to_chunks()
    assert len(chunks) == 1
    assert chunks[0]["content_type"] == "image"
    assert chunks[0]["source_name"] == "test.png"


async def test_vision_parser_fallback_on_no_key(tiny_png_bytes):
    """Vision parser must degrade gracefully without API key."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(side_effect=Exception("No API key"))), \
         patch.object(parser, "_describe_with_anthropic",
                      AsyncMock(side_effect=Exception("No API key"))):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    assert isinstance(result, VisionParseResult)
    assert result.description or result.error


async def test_vision_parser_uses_anthropic_fallback(tiny_png_bytes):
    """Vision parser tries Anthropic Claude when OpenAI is unavailable."""
    parser = VisionParser(prefer_provider="anthropic")
    with patch.object(parser, "_describe_with_anthropic",
                      AsyncMock(return_value="Image described by Claude.")):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    assert result.description


# ── Vision model failover (Model Registry vision order) ─────────────────────


class _VisionResp:
    def __init__(self, content: str, model: str) -> None:
        self.content = content
        self.model = model
        self.input_tokens = 1
        self.output_tokens = 1


class _RoutingVisionProvider:
    """The primary vision model is down; the backup answers."""

    def __init__(self, broken: set[str]) -> None:
        self.broken = broken
        self.models_called: list[str] = []

    async def complete(self, request):
        self.models_called.append(request.model)
        if request.model in self.broken:
            raise RuntimeError(f"{request.model} is down")
        return _VisionResp(f"described by {request.model}", request.model)


async def _vision_noop(*_a, **_k) -> None:
    return None


async def test_vision_provider_path_fails_over_to_the_next_vision_model(tiny_png_bytes):
    from app.ingestion.parsers import vision_parser as vp

    provider = _RoutingVisionProvider(broken={"vision-primary"})
    seen_primary: list[str] = []

    def _fallbacks(primary: str) -> list[str]:
        seen_primary.append(primary)
        return ["vision-backup"]

    with (
        patch("app.ai_router.selection.resolve_vision_model", lambda f="": "vision-primary"),
        patch.object(vp, "_vision_fallback_models", _fallbacks),
        patch("app.providers.guarded_completion._require_attribution", lambda *a: None),
        patch("app.providers.guarded_completion._preflight", _vision_noop),
        patch("app.providers.guarded_completion._charge", _vision_noop),
    ):
        result = await VisionParser(provider=provider).parse_image_bytes(
            tiny_png_bytes, "img.png"
        )

    assert result.error is None
    assert result.description == "described by vision-backup"
    assert provider.models_called == ["vision-primary", "vision-backup"]
    assert seen_primary == ["vision-primary"]


async def test_vision_fallback_list_comes_from_the_registry_vision_order():
    from app.ingestion.parsers import vision_parser as vp

    with patch(
        "app.ai_router.selection.resolve_vision_fallback_models",
        lambda primary, limit=3: ["v2", "v3"] if primary == "v1" else [],
    ):
        assert vp._vision_fallback_models("v1") == ["v2", "v3"]


async def test_legacy_openai_path_fails_over_across_vision_models(tiny_png_bytes):
    from types import SimpleNamespace

    from app.ingestion.parsers import vision_parser as vp

    called: list[str] = []

    async def _create(*, model, **_kw):
        called.append(model)
        if model == "vision-primary":
            raise RuntimeError("timed out")
        msg = SimpleNamespace(content=f"described by {model}")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    with (
        patch("app.providers.openai_client.async_openai_client", lambda: client),
        patch("app.ai_router.selection.resolve_vision_model", lambda f="": "vision-primary"),
        patch.object(vp, "_vision_fallback_models", lambda primary: ["vision-backup"]),
    ):
        text = await VisionParser()._describe_with_openai("aGk=", "image/png", "describe")

    assert text == "described by vision-backup"
    assert called == ["vision-primary", "vision-backup"]
