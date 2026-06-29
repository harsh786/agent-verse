"""Comprehensive tests for app/collab/store.py — targets the 21% baseline."""
from __future__ import annotations

import pytest

from app.collab.store import (
    CollaborationStore,
    VersionConflictError,
    _now_iso,
    _operation_to_dict,
    _session_to_dict,
)
from app.tenancy.context import PlanTier, TenantContext

T1 = TenantContext(tenant_id="tenant-cs-1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
T2 = TenantContext(tenant_id="tenant-cs-2", plan=PlanTier.FREE, api_key_id="k2")


# ── VersionConflictError ──────────────────────────────────────────────────────

def test_version_conflict_error_attributes() -> None:
    err = VersionConflictError("msg", current_version=7, expected_version=3)
    assert str(err) == "msg"
    assert err.current_version == 7
    assert err.expected_version == 3


def test_version_conflict_error_is_exception() -> None:
    assert issubclass(VersionConflictError, Exception)


def test_version_conflict_error_defaults() -> None:
    err = VersionConflictError("plain")
    assert err.current_version == 0
    assert err.expected_version == 0


# ── _now_iso helper ───────────────────────────────────────────────────────────

def test_now_iso_returns_string() -> None:
    ts = _now_iso()
    assert isinstance(ts, str)
    assert "T" in ts  # ISO format includes "T" separator


# ── _session_to_dict helper ───────────────────────────────────────────────────

def test_session_to_dict_with_metadata() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="s1",
        tenant_id="t1",
        name="Session A",
        mode="review",
        status="active",
        content="Hello",
        metadata_json={"participants": ["alice", "bob"], "goal_id": "g1", "agent_id": "a1"},
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 2, tzinfo=UTC),
    )
    d = _session_to_dict(row)
    assert d["session_id"] == "s1"
    assert d["name"] == "Session A"
    assert d["participants"] == ["alice", "bob"]
    assert d["participant_count"] == 2
    assert d["goal_id"] == "g1"
    assert d["agent_id"] == "a1"


def test_session_to_dict_explicit_participants() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="s2",
        tenant_id="t1",
        name="Session B",
        mode=None,
        status=None,
        content=None,
        metadata_json={},
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    d = _session_to_dict(row, participants=["charlie"])
    assert d["participants"] == ["charlie"]
    assert d["participant_count"] == 1
    assert d["mode"] == "suggest"  # default
    assert d["status"] == "active"  # default


def test_session_to_dict_none_timestamps() -> None:
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="s3",
        tenant_id="t1",
        name="Session C",
        mode="co-write",
        status="closed",
        content="xyz",
        metadata_json=None,
        created_at=None,
        updated_at=None,
    )
    d = _session_to_dict(row)
    assert d["created_at"] == ""
    assert d["updated_at"] == ""


# ── _operation_to_dict helper ─────────────────────────────────────────────────

def test_operation_to_dict() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="op1",
        session_id="s1",
        tenant_id="t1",
        version=3,
        operation={"type": "insert"},
        author="alice",
        created_at=datetime(2025, 3, 1, tzinfo=UTC),
    )
    d = _operation_to_dict(row)
    assert d["operation_id"] == "op1"
    assert d["version"] == 3
    assert d["author"] == "alice"
    assert "2025-03-01" in d["created_at"]


def test_operation_to_dict_null_author_and_timestamp() -> None:
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="op2",
        session_id="s2",
        tenant_id="t2",
        version=1,
        operation={},
        author=None,
        created_at=None,
    )
    d = _operation_to_dict(row)
    assert d["author"] == ""
    assert d["created_at"] == ""


# ── CollaborationStore in-memory ──────────────────────────────────────────────

async def test_list_sessions_empty() -> None:
    store = CollaborationStore()
    result = await store.list_sessions(tenant_ctx=T1)
    assert result == []


async def test_create_session_basic() -> None:
    store = CollaborationStore()
    rec = await store.create_session(
        tenant_ctx=T1,
        name="My Session",
        mode="review",
        participants=["alice", "bob"],
        goal_id="g1",
        agent_id="a1",
        content="initial",
    )
    assert rec["name"] == "My Session"
    assert rec["mode"] == "review"
    assert rec["status"] == "active"
    assert rec["participants"] == ["alice", "bob"]
    assert rec["participant_count"] == 2
    assert rec["goal_id"] == "g1"
    assert rec["agent_id"] == "a1"
    assert rec["content"] == "initial"
    assert len(rec["session_id"]) == 32


async def test_create_session_defaults() -> None:
    store = CollaborationStore()
    rec = await store.create_session(
        tenant_ctx=T1,
        name="Empty Session",
        mode="suggest",
        participants=[],
    )
    assert rec["participants"] == []
    assert rec["participant_count"] == 0
    assert rec["goal_id"] is None
    assert rec["agent_id"] is None
    assert rec["content"] == ""


async def test_list_sessions_tenant_isolation() -> None:
    store = CollaborationStore()
    await store.create_session(tenant_ctx=T1, name="T1-A", mode="review", participants=[])
    await store.create_session(tenant_ctx=T1, name="T1-B", mode="review", participants=[])
    await store.create_session(tenant_ctx=T2, name="T2-A", mode="review", participants=[])

    t1_sessions = await store.list_sessions(tenant_ctx=T1)
    t2_sessions = await store.list_sessions(tenant_ctx=T2)

    assert len(t1_sessions) == 2
    assert len(t2_sessions) == 1
    assert all(s["tenant_id"] == T1.tenant_id for s in t1_sessions)


async def test_get_session_found() -> None:
    store = CollaborationStore()
    created = await store.create_session(
        tenant_ctx=T1, name="Find Me", mode="co-write", participants=["x"]
    )
    found = await store.get_session(tenant_ctx=T1, session_id=created["session_id"])
    assert found is not None
    assert found["name"] == "Find Me"
    assert found["session_id"] == created["session_id"]


async def test_get_session_not_found() -> None:
    store = CollaborationStore()
    result = await store.get_session(tenant_ctx=T1, session_id="nonexistent-id")
    assert result is None


async def test_get_session_wrong_tenant_returns_none() -> None:
    store = CollaborationStore()
    created = await store.create_session(
        tenant_ctx=T1, name="T1 Only", mode="suggest", participants=[]
    )
    result = await store.get_session(tenant_ctx=T2, session_id=created["session_id"])
    assert result is None


async def test_close_session_found() -> None:
    store = CollaborationStore()
    created = await store.create_session(
        tenant_ctx=T1, name="Close Me", mode="review", participants=[]
    )
    closed = await store.close_session(tenant_ctx=T1, session_id=created["session_id"])
    assert closed is not None
    assert closed["status"] == "closed"


async def test_close_session_not_found_returns_none() -> None:
    store = CollaborationStore()
    result = await store.close_session(tenant_ctx=T1, session_id="ghost")
    assert result is None


async def test_close_session_wrong_tenant_returns_none() -> None:
    store = CollaborationStore()
    created = await store.create_session(
        tenant_ctx=T1, name="T1 Sess", mode="suggest", participants=[]
    )
    result = await store.close_session(tenant_ctx=T2, session_id=created["session_id"])
    assert result is None


async def test_close_session_updates_timestamp() -> None:
    store = CollaborationStore()
    created = await store.create_session(
        tenant_ctx=T1, name="Time Check", mode="suggest", participants=[]
    )
    orig_updated = created["updated_at"]
    import asyncio
    await asyncio.sleep(0.01)
    closed = await store.close_session(tenant_ctx=T1, session_id=created["session_id"])
    assert closed is not None
    assert closed["updated_at"] >= orig_updated


# ── append_operation ──────────────────────────────────────────────────────────

async def test_append_operation_basic() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Ops Test", mode="co-write", participants=[]
    )
    op = await store.append_operation(
        tenant_ctx=T1,
        session_id=sess["session_id"],
        operation={"type": "insert", "text": "hello"},
        author="alice",
    )
    assert op["version"] == 1
    assert op["author"] == "alice"
    assert op["operation"]["type"] == "insert"
    assert len(op["operation_id"]) == 32


async def test_append_operation_increments_version() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Version Test", mode="co-write", participants=[]
    )
    sid = sess["session_id"]
    op1 = await store.append_operation(
        tenant_ctx=T1, session_id=sid, operation={"type": "a"}, author="alice"
    )
    op2 = await store.append_operation(
        tenant_ctx=T1, session_id=sid, operation={"type": "b"}, author="bob"
    )
    assert op2["version"] == op1["version"] + 1


async def test_append_operation_content_update_updates_session() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Content Test", mode="co-write", participants=[]
    )
    sid = sess["session_id"]
    await store.append_operation(
        tenant_ctx=T1,
        session_id=sid,
        operation={"type": "content_update", "content": "new content here"},
        author="alice",
    )
    updated = await store.get_session(tenant_ctx=T1, session_id=sid)
    assert updated is not None
    assert updated["content"] == "new content here"


async def test_append_operation_key_error_for_missing_session() -> None:
    store = CollaborationStore()
    with pytest.raises(KeyError):
        await store.append_operation(
            tenant_ctx=T1,
            session_id="nonexistent",
            operation={"type": "insert"},
            author="alice",
        )


async def test_append_operation_version_conflict() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Conflict Test", mode="co-write", participants=[]
    )
    sid = sess["session_id"]
    # Append one op so current version is 1
    await store.append_operation(
        tenant_ctx=T1, session_id=sid, operation={"type": "a"}, author="alice"
    )
    # Now try to append with expected_version=0 (stale)
    with pytest.raises(VersionConflictError) as exc_info:
        await store.append_operation(
            tenant_ctx=T1,
            session_id=sid,
            operation={"type": "b"},
            author="bob",
            expected_version=0,
        )
    assert exc_info.value.current_version == 1
    assert exc_info.value.expected_version == 0


async def test_append_operation_no_version_check_succeeds() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="NoCheck", mode="co-write", participants=[]
    )
    sid = sess["session_id"]
    # Three ops without version check
    for i in range(3):
        op = await store.append_operation(
            tenant_ctx=T1,
            session_id=sid,
            operation={"type": f"op{i}"},
            author="alice",
            expected_version=None,
        )
    assert op["version"] == 3


# ── list_operations ───────────────────────────────────────────────────────────

async def test_list_operations_empty() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Empty Ops", mode="suggest", participants=[]
    )
    result = await store.list_operations(tenant_ctx=T1, session_id=sess["session_id"])
    assert result == []


async def test_list_operations_returns_all_in_order() -> None:
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Ordered Ops", mode="co-write", participants=[]
    )
    sid = sess["session_id"]
    for i in range(5):
        await store.append_operation(
            tenant_ctx=T1, session_id=sid, operation={"type": f"op{i}"}, author="alice"
        )
    ops = await store.list_operations(tenant_ctx=T1, session_id=sid)
    assert len(ops) == 5
    versions = [op["version"] for op in ops]
    assert versions == list(range(1, 6))


async def test_list_operations_returns_copy() -> None:
    """Mutation of returned list should not affect stored operations."""
    store = CollaborationStore()
    sess = await store.create_session(
        tenant_ctx=T1, name="Copy Test", mode="suggest", participants=[]
    )
    sid = sess["session_id"]
    await store.append_operation(
        tenant_ctx=T1, session_id=sid, operation={"type": "x"}, author="x"
    )
    ops = await store.list_operations(tenant_ctx=T1, session_id=sid)
    ops.clear()  # mutate returned copy
    ops2 = await store.list_operations(tenant_ctx=T1, session_id=sid)
    assert len(ops2) == 1
