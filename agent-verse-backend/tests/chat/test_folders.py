"""Tests for session folders CRUD — 6 cases."""

from __future__ import annotations

import pytest

from app.chat.service import ChatService

TENANT = "t1"
OTHER = "t2"


@pytest.fixture()
def svc() -> ChatService:
    return ChatService()


def test_create_folder(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "Work", "#ff0000")
    assert f.id
    assert f.name == "Work"
    assert f.color == "#ff0000"
    assert f.tenant_id == TENANT


def test_list_folders_isolated(svc: ChatService) -> None:
    svc.create_folder(TENANT, "A")
    svc.create_folder(TENANT, "B")
    svc.create_folder(OTHER, "Other")
    folders = svc.list_folders(TENANT)
    assert len(folders) == 2
    assert all(f.tenant_id == TENANT for f in folders)


def test_delete_folder(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "Del")
    ok = svc.delete_folder(f.id, TENANT)
    assert ok
    folders = svc.list_folders(TENANT)
    assert all(fld.id != f.id for fld in folders)


def test_delete_folder_cascades_to_null(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "ToDelete")
    s = svc.create_session(TENANT, folder_id=f.id)
    svc.delete_folder(f.id, TENANT)
    session = svc.get_session(s.id, TENANT)
    assert session is not None
    assert session.folder_id is None


def test_move_session_to_folder(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "Work")
    s = svc.create_session(TENANT)
    updated = svc.move_session_to_folder(s.id, TENANT, f.id)
    assert updated is not None
    assert updated.folder_id == f.id


def test_session_count_in_folder(svc: ChatService) -> None:
    f = svc.create_folder(TENANT, "Project")
    svc.create_session(TENANT, folder_id=f.id)
    svc.create_session(TENANT, folder_id=f.id)
    sessions = [s for s in svc.list_sessions(TENANT) if s.folder_id == f.id]
    assert len(sessions) == 2
