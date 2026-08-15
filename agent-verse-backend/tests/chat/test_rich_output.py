"""Tests for rich output routing — 10 cases."""

from __future__ import annotations

import pytest

from app.chat.service import ChatService

TENANT = "t1"


@pytest.fixture()
def svc() -> ChatService:
    return ChatService()


@pytest.fixture()
def session(svc: ChatService):
    return svc.create_session(TENANT)


def test_message_with_table_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Here is the data:",
        metadata={"output_type": "table", "data": [{"a": 1}]}
    )
    assert msg.metadata["output_type"] == "table"


def test_message_with_chart_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Chart:",
        metadata={"output_type": "chart", "spec": {"mark": "bar"}}
    )
    assert msg.metadata["output_type"] == "chart"


def test_message_with_diff_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Diff:",
        metadata={"output_type": "diff", "diff_content": "--- a\n+++ b"}
    )
    assert msg.metadata["output_type"] == "diff"


def test_message_with_image_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Image:",
        metadata={"output_type": "image", "image_url": "https://example.com/img.png"}
    )
    assert msg.metadata["output_type"] == "image"
    assert "image_url" in msg.metadata


def test_message_text_output_type(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Plain text response",
        metadata={"output_type": "text"}
    )
    assert msg.metadata["output_type"] == "text"


def test_artifact_output_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Artifact content",
        metadata={"output_type": "artifact", "artifact_id": "art_123"}
    )
    assert msg.metadata["output_type"] == "artifact"


def test_message_metadata_goal_id(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Goal complete",
        goal_id="goal_abc"
    )
    assert msg.goal_id == "goal_abc"


def test_message_metadata_intent_stored(svc: ChatService, session) -> None:
    result = svc.dispatch(session.id, TENANT, "What is Docker?")
    msgs = svc.list_messages(session.id, TENANT)
    user_msg = next(m for m in msgs if m.role == "user")
    assert user_msg.intent is not None


def test_reasoning_metadata(svc: ChatService, session) -> None:
    msg = svc.save_message(
        session.id, TENANT, "assistant", "Response",
        metadata={"reasoning": "Thought about it step by step"}
    )
    assert "reasoning" in msg.metadata


def test_multiple_output_types_in_session(svc: ChatService, session) -> None:
    for output_type in ["table", "chart", "text", "diff"]:
        svc.save_message(
            session.id, TENANT, "assistant", f"{output_type} response",
            metadata={"output_type": output_type}
        )
    msgs = svc.list_messages(session.id, TENANT)
    output_types = {m.metadata.get("output_type") for m in msgs if m.metadata.get("output_type")}
    assert len(output_types) == 4
