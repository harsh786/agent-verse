"""Unit tests for multimodal perception models."""
from __future__ import annotations

import base64

import pytest

from app.perception.multimodal import ImageAttachment, PerceptionInput, resize_image_b64


def test_perception_input_no_visual_context():
    pi = PerceptionInput(goal_text="Fix the bug")
    assert pi.has_visual_context() is False
    assert pi.to_prompt_context() == ""


def test_perception_input_with_image():
    img = ImageAttachment(data_b64="abc123", description="screenshot")
    pi = PerceptionInput(goal_text="What is on screen?", images=[img])
    assert pi.has_visual_context() is True
    ctx = pi.to_prompt_context()
    assert "Visual Context" in ctx
    assert "screenshot" in ctx


def test_perception_input_with_url():
    pi = PerceptionInput(goal_text="Analyze page", urls=["https://example.com"])
    assert pi.has_visual_context() is True
    ctx = pi.to_prompt_context()
    assert "https://example.com" in ctx


def test_image_attachment_from_data_uri_png():
    data_uri = "data:image/png;base64,abc123=="
    img = ImageAttachment.from_data_uri(data_uri, description="test")
    assert img.mime_type == "image/png"
    assert img.data_b64 == "abc123=="
    assert img.description == "test"


def test_image_attachment_from_data_uri_jpeg():
    data_uri = "data:image/jpeg;base64,xyz789"
    img = ImageAttachment.from_data_uri(data_uri)
    assert img.mime_type == "image/jpeg"
    assert img.data_b64 == "xyz789"


def test_image_attachment_to_data_uri():
    img = ImageAttachment(data_b64="abc123", mime_type="image/png")
    uri = img.to_data_uri()
    assert uri == "data:image/png;base64,abc123"


def test_image_attachment_round_trip():
    original_uri = "data:image/png;base64,iVBORw0KGgo="
    img = ImageAttachment.from_data_uri(original_uri)
    assert img.to_data_uri() == original_uri


def test_image_attachment_byte_size():
    raw = b"Hello, World!"
    b64 = base64.b64encode(raw).decode()
    img = ImageAttachment(data_b64=b64)
    assert img.byte_size() == len(raw)


def test_image_attachment_byte_size_invalid_b64():
    img = ImageAttachment(data_b64="not valid base64 !!!!")
    # Should return 0 for invalid b64, not raise
    assert img.byte_size() == 0


def test_resize_image_b64_small_image_unchanged():
    raw = b"x" * 100  # tiny, well under 1MB
    b64 = base64.b64encode(raw).decode()
    result = resize_image_b64(b64, max_size=1_000_000)
    assert result == b64  # unchanged


def test_resize_image_b64_handles_invalid():
    result = resize_image_b64("invalid!!", max_size=1)
    # Should not raise, returns original
    assert result == "invalid!!"


def test_perception_input_multiple_images():
    imgs = [
        ImageAttachment(data_b64="img1", description="first"),
        ImageAttachment(data_b64="img2", description="second"),
    ]
    pi = PerceptionInput(goal_text="Compare these", images=imgs)
    assert pi.has_visual_context() is True
    ctx = pi.to_prompt_context()
    assert "Image 1" in ctx
    assert "Image 2" in ctx
    assert "first" in ctx
    assert "second" in ctx
