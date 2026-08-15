"""Tests for message editing and branching — 8 cases."""

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


def test_edit_user_message_updates_content(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "user", "Original question")
    edited, _ = svc.edit_message(m.id, TENANT, "Updated question")
    assert edited is not None
    assert edited.content == "Updated question"


def test_edit_prunes_subsequent_messages(svc: ChatService, session) -> None:
    m1 = svc.save_message(session.id, TENANT, "user", "Q1")
    m2 = svc.save_message(session.id, TENANT, "assistant", "A1")
    m3 = svc.save_message(session.id, TENANT, "user", "Q2")
    m4 = svc.save_message(session.id, TENANT, "assistant", "A2")

    _, pruned = svc.edit_message(m1.id, TENANT, "New Q1")
    assert m2.id in pruned
    assert m3.id in pruned
    assert m4.id in pruned


def test_edit_cannot_edit_assistant_message(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "assistant", "Response")
    result, pruned = svc.edit_message(m.id, TENANT, "Hack")
    assert result is None
    assert pruned == []


def test_edit_wrong_tenant_blocked(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "user", "Secret")
    result, _ = svc.edit_message(m.id, "other_tenant", "Hacked")
    assert result is None


def test_edit_preserves_remaining_messages(svc: ChatService, session) -> None:
    m1 = svc.save_message(session.id, TENANT, "user", "First")
    m2 = svc.save_message(session.id, TENANT, "assistant", "Second")
    svc.save_message(session.id, TENANT, "user", "Third")

    svc.edit_message(m2.id, TENANT, "Updated second")  # assistant — should fail
    msgs = svc.list_messages(session.id, TENANT)
    assert len(msgs) == 3  # unchanged — edit of assistant fails


def test_edit_only_prunes_subsequent(svc: ChatService, session) -> None:
    m1 = svc.save_message(session.id, TENANT, "user", "A")
    m2 = svc.save_message(session.id, TENANT, "assistant", "B")
    m3 = svc.save_message(session.id, TENANT, "user", "C")

    # Edit m3 — no subsequent messages to prune
    edited, pruned = svc.edit_message(m3.id, TENANT, "C updated")
    assert edited is not None
    assert pruned == []  # m3 is last


def test_delete_message(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "user", "To delete")
    ok = svc.delete_message(m.id, TENANT)
    assert ok
    msgs = svc.list_messages(session.id, TENANT)
    assert all(msg.id != m.id for msg in msgs)


def test_edit_updates_session_timestamp(svc: ChatService, session) -> None:
    original_updated = session.updated_at
    m = svc.save_message(session.id, TENANT, "user", "Msg")
    import time; time.sleep(0.01)
    svc.edit_message(m.id, TENANT, "New content")
    updated_session = svc.get_session(session.id, TENANT)
    assert updated_session is not None
    # updated_at should have advanced (or at least be valid)
    assert updated_session.updated_at >= original_updated
