# tests/ingestion/test_vision_parser.py
"""Vision parser describes images with the Model Registry's vision model."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.ai_router.resolve import ModelNotConfiguredError, Resolution
from app.ingestion.parsers.vision_parser import VisionParser, VisionParseResult


@pytest.fixture
def tiny_png_bytes():
    """1x1 white PNG for testing."""
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAAABJRU5ErkJggg=="
    return base64.b64decode(b64)


def _registry_vlm(*fallbacks: str):
    res = Resolution(
        capability="vision", model="vision-primary", source="registry_preference",
        fallbacks=tuple(fallbacks),
    )
    return patch("app.ingestion.parsers.vision_parser._resolve_vision", lambda: res)


async def test_vision_parser_describes_image(tiny_png_bytes):
    """VisionParser must call the vision model and return its description."""
    parser = VisionParser()
    with _registry_vlm(), patch.object(
        parser, "_describe_with_provider", AsyncMock(return_value="A white 1x1 pixel image.")
    ):
        result = await parser.parse_image_bytes(
            image_bytes=tiny_png_bytes,
            source_name="test.png",
            prompt="Describe this image in detail.",
        )
    assert isinstance(result, VisionParseResult)
    assert result.description == "A white 1x1 pixel image."
    assert result.model_used == "vision-primary"


async def test_vision_parser_returns_structured_chunks(tiny_png_bytes):
    """VisionParseResult must produce chunks with image metadata."""
    parser = VisionParser()
    with _registry_vlm(), patch.object(
        parser, "_describe_with_provider", AsyncMock(return_value="White pixel image")
    ):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    chunks = result.to_chunks()
    assert len(chunks) == 1
    assert chunks[0]["content_type"] == "image"
    assert chunks[0]["source_name"] == "test.png"


async def test_vision_parser_degrades_when_the_model_fails(tiny_png_bytes):
    """A failing vision model degrades to a placeholder with the error recorded."""
    parser = VisionParser()
    with _registry_vlm(), patch.object(
        parser, "_describe_with_provider", AsyncMock(side_effect=Exception("down"))
    ), patch("app.ingestion.parsers.vision_parser._VISION_MAX_ATTEMPTS", 1):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    assert result.description == "[Image: test.png]"
    assert result.error == "down"
    assert result.model_used == "fallback"


async def test_vision_parser_without_a_vision_model_is_honest(tiny_png_bytes):
    """No vision model in the registry (nor a VISION_MODEL pin): an honest error,
    never a vendor default model."""

    def _none():
        raise ModelNotConfiguredError("vision", "add one")

    with patch("app.ingestion.parsers.vision_parser._resolve_vision", _none):
        result = await VisionParser().parse_image_bytes(tiny_png_bytes, "test.png")
    assert result.model_used == "fallback"
    assert result.error and "'vision'" in result.error


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
    provider = _RoutingVisionProvider(broken={"vision-primary"})

    with (
        _registry_vlm("vision-backup"),
        patch("app.providers.guarded_completion._require_attribution", lambda *a: None),
        patch("app.providers.guarded_completion._preflight", _vision_noop),
        patch("app.providers.guarded_completion._charge", _vision_noop),
    ):
        result = await VisionParser(provider=provider).parse_image_bytes(
            tiny_png_bytes, "img.png"
        )

    assert result.error is None
    assert result.description == "described by vision-backup"
    assert result.model_used == "vision-backup"
    assert provider.models_called == ["vision-primary", "vision-backup"]
