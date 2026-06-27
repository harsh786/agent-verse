"""Tests for optimistic concurrency control in CollaborationStore."""
from __future__ import annotations

import pytest

from app.collab.store import CollaborationStore, VersionConflictError
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="collab-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.mark.asyncio
async def test_version_conflict_error_is_raised():
    """VersionConflictError must be importable and raised on conflict."""
    from app.collab.store import VersionConflictError

    exc = VersionConflictError("conflict", current_version=5, expected_version=3)
    assert exc.current_version == 5
    assert exc.expected_version == 3


@pytest.mark.asyncio
async def test_append_operation_in_memory_version_conflict():
    """In-memory mode raises VersionConflictError when expected_version is wrong."""
    store = CollaborationStore()
    store._sessions[("collab-t1", "s1")] = {
        "session_id": "s1",
        "tenant_id": "collab-t1",
        "name": "test",
        "mode": "suggest",
        "status": "active",
        "content": "",
        "goal_id": None,
        "agent_id": None,
        "participants": [],
        "participant_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    store._operations[("collab-t1", "s1")] = [
        {"version": 1, "operation": {"type": "test"}, "author": "alice"},
        {"version": 2, "operation": {"type": "test"}, "author": "bob"},
    ]  # current version is 2

    # Client thinks it's at version 1 — should conflict
    with pytest.raises(VersionConflictError) as exc_info:
        await store.append_operation(
            tenant_ctx=T,
            session_id="s1",
            operation={"type": "content_update", "content": "new content"},
            author="carol",
            expected_version=1,  # wrong — current is 2
        )

    assert exc_info.value.current_version == 2
    assert exc_info.value.expected_version == 1


@pytest.mark.asyncio
async def test_append_operation_in_memory_correct_version():
    """In-memory mode succeeds when expected_version matches current."""
    store = CollaborationStore()
    store._sessions[("collab-t1", "s2")] = {
        "session_id": "s2",
        "tenant_id": "collab-t1",
        "name": "test",
        "mode": "suggest",
        "status": "active",
        "content": "",
        "goal_id": None,
        "agent_id": None,
        "participants": [],
        "participant_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    store._operations[("collab-t1", "s2")] = [
        {"version": 1, "operation": {}, "author": "alice"},
    ]  # current version is 1

    result = await store.append_operation(
        tenant_ctx=T,
        session_id="s2",
        operation={"type": "test"},
        author="bob",
        expected_version=1,  # correct
    )
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_append_operation_in_memory_no_version_check():
    """No expected_version means no conflict check — always succeeds."""
    store = CollaborationStore()
    store._sessions[("collab-t1", "s3")] = {
        "session_id": "s3",
        "tenant_id": "collab-t1",
        "name": "test",
        "mode": "suggest",
        "status": "active",
        "content": "",
        "goal_id": None,
        "agent_id": None,
        "participants": [],
        "participant_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    store._operations[("collab-t1", "s3")] = []

    result = await store.append_operation(
        tenant_ctx=T,
        session_id="s3",
        operation={"type": "test"},
        author="alice",
        expected_version=None,  # no check
    )
    assert result["version"] == 1


def test_no_with_for_update_in_collab_store():
    """CollaborationStore must NOT use SELECT ... FOR UPDATE (pessimistic lock)."""
    import inspect

    from app.collab import store

    src = inspect.getsource(store)
    assert "with_for_update()" not in src, (
        "CollaborationStore must not use pessimistic SELECT FOR UPDATE; use optimistic concurrency"
    )


def test_collab_store_has_version_conflict_error():
    """VersionConflictError must be defined in collab.store."""
    from app.collab.store import VersionConflictError

    assert issubclass(VersionConflictError, Exception)
    assert hasattr(VersionConflictError, "__init__")


def test_append_operation_signature_has_expected_version():
    """append_operation must accept expected_version parameter."""
    import inspect

    from app.collab.store import CollaborationStore

    sig = inspect.signature(CollaborationStore.append_operation)
    assert "expected_version" in sig.parameters, (
        "append_operation must accept expected_version for optimistic concurrency"
    )


def test_collab_api_handles_409_conflict():
    """API endpoint returns 409 on VersionConflictError."""
    import inspect

    from app.api import collab

    src = inspect.getsource(collab)
    assert "VersionConflictError" in src or "version_conflict" in src, (
        "collab.py API must handle VersionConflictError with HTTP 409"
    )
    assert "409" in src, "HTTP 409 Conflict must be returned on version conflict"
