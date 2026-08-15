"""Tests for MemoryAPI — 8 cases."""

from __future__ import annotations

import pytest

from app.chat.memory_api import MemoryAPI

TENANT = "t1"
OTHER = "t2"


@pytest.fixture()
def api() -> MemoryAPI:
    return MemoryAPI()


def test_create_memory(api: MemoryAPI) -> None:
    m = api.create_memory(TENANT, "Always use snake_case in Python")
    assert m.id
    assert m.content == "Always use snake_case in Python"
    assert m.tenant_id == TENANT
    assert m.source == "manual"


def test_list_memories(api: MemoryAPI) -> None:
    api.create_memory(TENANT, "Memory A")
    api.create_memory(TENANT, "Memory B")
    api.create_memory(OTHER, "Other tenant memory")
    memories = api.list_memories(TENANT)
    assert len(memories) == 2
    assert all(m.tenant_id == TENANT for m in memories)


def test_update_memory(api: MemoryAPI) -> None:
    m = api.create_memory(TENANT, "Old content")
    updated = api.update_memory(m.id, TENANT, "New content")
    assert updated is not None
    assert updated.content == "New content"


def test_update_memory_wrong_tenant(api: MemoryAPI) -> None:
    m = api.create_memory(TENANT, "Content")
    result = api.update_memory(m.id, OTHER, "Hacked")
    assert result is None


def test_delete_memory_creates_audit_log(api: MemoryAPI) -> None:
    m = api.create_memory(TENANT, "To delete")
    ok = api.delete_memory(m.id, TENANT)
    assert ok
    log = api.get_audit_log(TENANT)
    assert len(log) == 1
    assert log[0]["action"] == "delete_memory"
    assert log[0]["memory_id"] == m.id


def test_delete_memory_rls(api: MemoryAPI) -> None:
    m = api.create_memory(TENANT, "Private")
    ok = api.delete_memory(m.id, OTHER)
    assert not ok
    # Memory should still exist
    memories = api.list_memories(TENANT)
    assert len(memories) == 1


def test_delete_all_memories_gdpr(api: MemoryAPI) -> None:
    api.create_memory(TENANT, "A")
    api.create_memory(TENANT, "B")
    api.create_memory(OTHER, "Other")
    count = api.delete_all_memories(TENANT)
    assert count == 2
    assert api.list_memories(TENANT) == []
    assert len(api.list_memories(OTHER)) == 1


def test_delete_all_creates_audit_log(api: MemoryAPI) -> None:
    api.create_memory(TENANT, "A")
    api.delete_all_memories(TENANT)
    log = api.get_audit_log(TENANT)
    assert any(e["action"] == "delete_all_memories" for e in log)
