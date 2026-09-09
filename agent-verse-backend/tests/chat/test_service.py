"""Tests for ChatService — 32 cases."""

from __future__ import annotations

import pytest

from app.chat.intent import Intent
from app.chat.service import ChatService

TENANT = "tenant_abc"
TENANT2 = "tenant_xyz"


@pytest.fixture()
def svc() -> ChatService:
    return ChatService()


@pytest.fixture()
def session(svc: ChatService):
    return svc.create_session(TENANT, title="Test Session")


# ── Session CRUD ───────────────────────────────────────────────────────────────

def test_create_session(svc: ChatService) -> None:
    s = svc.create_session(TENANT, title="Hello")
    assert s.id
    assert s.title == "Hello"
    assert s.tenant_id == TENANT


def test_get_session(svc: ChatService, session) -> None:
    found = svc.get_session(session.id, TENANT)
    assert found is not None
    assert found.id == session.id


def test_get_session_wrong_tenant(svc: ChatService, session) -> None:
    found = svc.get_session(session.id, TENANT2)
    assert found is None


def test_list_sessions(svc: ChatService) -> None:
    svc.create_session(TENANT, title="A")
    svc.create_session(TENANT, title="B")
    svc.create_session(TENANT2, title="Other")
    sessions = svc.list_sessions(TENANT)
    assert len(sessions) == 2
    assert all(s.tenant_id == TENANT for s in sessions)


def test_update_session(svc: ChatService, session) -> None:
    updated = svc.update_session(session.id, TENANT, title="Updated")
    assert updated is not None
    assert updated.title == "Updated"


def test_update_session_wrong_tenant(svc: ChatService, session) -> None:
    result = svc.update_session(session.id, TENANT2, title="Bad")
    assert result is None


def test_delete_session(svc: ChatService, session) -> None:
    ok = svc.delete_session(session.id, TENANT)
    assert ok
    assert svc.get_session(session.id, TENANT) is None


def test_delete_session_cascades_messages(svc: ChatService, session) -> None:
    svc.save_message(session.id, TENANT, "user", "hello")
    svc.delete_session(session.id, TENANT)
    msgs = svc.list_messages(session.id, TENANT)
    assert msgs == []


def test_pin_session(svc: ChatService, session) -> None:
    svc.pin_session(session.id, TENANT, True)
    found = svc.get_session(session.id, TENANT)
    assert found is not None
    assert found.pinned is True


# ── Message CRUD ───────────────────────────────────────────────────────────────

def test_save_message(svc: ChatService, session) -> None:
    msg = svc.save_message(session.id, TENANT, "user", "Hello world")
    assert msg.id
    assert msg.role == "user"
    assert msg.content == "Hello world"


def test_list_messages(svc: ChatService, session) -> None:
    svc.save_message(session.id, TENANT, "user", "msg 1")
    svc.save_message(session.id, TENANT, "assistant", "msg 2")
    msgs = svc.list_messages(session.id, TENANT)
    assert len(msgs) == 2


def test_list_messages_tenant_isolation(svc: ChatService, session) -> None:
    svc.save_message(session.id, TENANT, "user", "real")
    msgs = svc.list_messages(session.id, TENANT2)
    assert msgs == []


def test_edit_message_prunes_subsequent(svc: ChatService, session) -> None:
    m1 = svc.save_message(session.id, TENANT, "user", "original")
    m2 = svc.save_message(session.id, TENANT, "assistant", "response")
    edited, pruned = svc.edit_message(m1.id, TENANT, "new content")
    assert edited is not None
    assert m2.id in pruned
    msgs = svc.list_messages(session.id, TENANT)
    assert all(m.id != m2.id for m in msgs)


def test_edit_message_updates_content(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "user", "old")
    svc.edit_message(m.id, TENANT, "new")
    found = next(msg for msg in svc.list_messages(session.id, TENANT) if msg.id == m.id)
    assert found.content == "new"


def test_edit_assistant_message_fails(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "assistant", "response")
    result, pruned = svc.edit_message(m.id, TENANT, "trying to edit")
    assert result is None


def test_delete_message(svc: ChatService, session) -> None:
    m = svc.save_message(session.id, TENANT, "user", "delete me")
    ok = svc.delete_message(m.id, TENANT)
    assert ok
    msgs = svc.list_messages(session.id, TENANT)
    assert all(msg.id != m.id for msg in msgs)


# ── Dispatch ───────────────────────────────────────────────────────────────────

def test_dispatch_qa(svc: ChatService, session) -> None:
    result = svc.dispatch(session.id, TENANT, "What is Python?")
    assert result["intent"] == Intent.QA.value
    assert result["message_id"]


def test_dispatch_goal(svc: ChatService, session) -> None:
    result = svc.dispatch(session.id, TENANT, "Deploy the API service to production")
    assert result["intent"] == Intent.GOAL.value


def test_dispatch_clarify(svc: ChatService, session) -> None:
    result = svc.dispatch(session.id, TENANT, "Deploy it")
    assert result["intent"] == Intent.CLARIFY.value
    assert result["clarify_request"] is not None


def test_dispatch_schedule(svc: ChatService, session) -> None:
    result = svc.dispatch(session.id, TENANT, "Run the backup every day at 2 AM")
    assert result["intent"] == Intent.SCHEDULE.value
    assert result["schedule_confirmation"] is not None


def test_dispatch_invalid_session(svc: ChatService) -> None:
    with pytest.raises(ValueError):
        svc.dispatch("nonexistent", TENANT, "hello")


# ── Usage ─────────────────────────────────────────────────────────────────────

def test_record_usage(svc: ChatService, session) -> None:
    msg = svc.save_message(session.id, TENANT, "user", "hi")
    u = svc.record_usage(msg.id, session.id, TENANT, 100, 200, 0.001, "gpt-4o")
    assert u.tokens_in == 100
    assert u.tokens_out == 200


def test_session_usage_summary(svc: ChatService, session) -> None:
    msg = svc.save_message(session.id, TENANT, "user", "hi")
    svc.record_usage(msg.id, session.id, TENANT, 100, 200, 0.001, "gpt-4o")
    summary = svc.session_usage_summary(session.id, TENANT)
    assert summary["total_tokens"] == 300
    assert summary["llm_calls"] == 1


# ── Folders ────────────────────────────────────────────────────────────────────

def test_create_folder(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "Work", "#ff0000")
    assert f.id
    assert f.name == "Work"


def test_list_folders(svc: ChatService) -> None:
    svc.create_folder(TENANT, "A")
    svc.create_folder(TENANT, "B")
    svc.create_folder(TENANT2, "Other")
    folders = svc.list_folders(TENANT)
    assert len(folders) == 2


def test_move_session_to_folder(svc: ChatService, session) -> None:
    f = svc.create_folder(TENANT, "Work")
    s = svc.move_session_to_folder(session.id, TENANT, f.id)
    assert s is not None
    assert s.folder_id == f.id


# ── Artifacts ─────────────────────────────────────────────────────────────────

def test_create_and_list_artifact(svc: ChatService, session) -> None:
    a = svc.create_artifact(session.id, TENANT, "hello.py", "python", "print('hi')")
    artifacts = svc.list_artifacts(session.id, TENANT)
    assert len(artifacts) == 1
    assert artifacts[0].id == a.id


# ── Search ─────────────────────────────────────────────────────────────────────

def test_search_messages(svc: ChatService, session) -> None:
    svc.save_message(session.id, TENANT, "user", "Deploy the FastAPI service")
    svc.save_message(session.id, TENANT, "user", "What is Redis?")
    results = svc.search_messages(TENANT, "FastAPI")
    assert len(results) == 1


def test_search_cross_session(svc: ChatService) -> None:
    s1 = svc.create_session(TENANT, "Session 1")
    s2 = svc.create_session(TENANT, "Session 2")
    svc.save_message(s1.id, TENANT, "user", "Deploy Kubernetes")
    svc.save_message(s2.id, TENANT, "user", "Deploy Docker")
    results = svc.search_messages(TENANT, "Deploy")
    assert len(results) == 2


# ── Summary ────────────────────────────────────────────────────────────────────

def test_summarize_session(svc: ChatService, session) -> None:
    svc.save_message(session.id, TENANT, "user", "How do I deploy a Python app?")
    svc.save_message(session.id, TENANT, "assistant", "You can use Docker or Kubernetes.")
    summary = svc.summarize_session(session.id, TENANT)
    assert len(summary) > 10
