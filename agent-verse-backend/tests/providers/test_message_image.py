"""Tests for Message image_data field."""
from __future__ import annotations

from app.providers.base import Message


def test_message_accepts_image_data() -> None:
    msg = Message(role="user", content="What is this?", image_data="base64data==")
    assert msg.image_data == "base64data=="


def test_message_image_data_optional() -> None:
    msg = Message(role="user", content="Hello")
    assert msg.image_data is None


def test_message_system_no_image() -> None:
    msg = Message(role="system", content="You are an assistant")
    assert msg.image_data is None


def test_message_image_data_does_not_affect_text_only() -> None:
    """image_data=None is the default and does not interfere with normal messages."""
    msg = Message(role="assistant", content="Here is my answer.")
    assert msg.image_data is None
    assert msg.content == "Here is my answer."
