"""Comprehensive tests for CollaborationStore — in-memory and DB paths."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.collab.store import CollaborationStore, VersionConflictError
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k1")


# ── 1. VersionConflictError ──────────────────────────────────────────────────

def test_version_conflict_error_fields():
    exc = VersionConflictError("conflict", current_version=3, expected_version=2)
    assert exc.current_version == 3
    assert exc.expected_version == 2
    assert str(exc) == "conflict"


def test_version_conflict_error_defaults():
    exc = VersionConflictError("conflict")
    assert exc.current_version == 0
    assert exc.expected_version == 0


# ── 2. In-memory: list_sessions ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions_empty_initially():
    store = CollaborationStore()
    result = await store.list_sessions(tenant_ctx=_ctx())
    assert result == []


@pytest.mark.asyncio
async def test_list_sessions_returns_own_tenant_only():
    store = CollaborationStore()
    ctx1 = _ctx("t1")
    ctx2 = _ctx("t2")

    await store.create_session(tenant_ctx=ctx1, name="S1", mode="suggest", participants=["u1"])
    await store.create_session(tenant_ctx=ctx2, name="S2", mode="suggest", participants=["u2"])

    sessions_t1 = await store.list_sessions(tenant_ctx=ctx1)
    sessions_t2 = await store.list_sessions(tenant_ctx=ctx2)

    assert len(sessions_t1) == 1
    assert len(sessions_t2) == 1
    assert sessions_t1[0]["name"] == "S1"
    assert sessions_t2[0]["name"] == "S2"


# ── 3. In-memory: create_session ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_returns_session_dict():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(
        tenant_ctx=ctx, name="Test Session", mode="suggest", participants=["user1", "user2"]
    )
    assert session["name"] == "Test Session"
    assert session["mode"] == "suggest"
    assert session["status"] == "active"
    assert session["tenant_id"] == "t1"
    assert session["participant_count"] == 2
    assert "session_id" in session


@pytest.mark.asyncio
async def test_create_session_with_goal_and_agent():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(
        tenant_ctx=ctx,
        name="Collab",
        mode="edit",
        participants=["u1"],
        goal_id="g1",
        agent_id="a1",
        content="initial content",
    )
    assert session["goal_id"] == "g1"
    assert session["agent_id"] == "a1"
    assert session["content"] == "initial content"


@pytest.mark.asyncio
async def test_create_session_unique_ids():
    store = CollaborationStore()
    ctx = _ctx()
    s1 = await store.create_session(tenant_ctx=ctx, name="S1", mode="suggest", participants=[])
    s2 = await store.create_session(tenant_ctx=ctx, name="S2", mode="suggest", participants=[])
    assert s1["session_id"] != s2["session_id"]


@pytest.mark.asyncio
async def test_create_session_timestamps_present():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="suggest", participants=[])
    assert "created_at" in session
    assert "updated_at" in session
    assert "T" in session["created_at"]


# ── 4. In-memory: get_session ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_returns_correct_session():
    store = CollaborationStore()
    ctx = _ctx()
    created = await store.create_session(tenant_ctx=ctx, name="My Session", mode="suggest", participants=["u1"])
    sid = created["session_id"]

    fetched = await store.get_session(tenant_ctx=ctx, session_id=sid)
    assert fetched is not None
    assert fetched["session_id"] == sid
    assert fetched["name"] == "My Session"


@pytest.mark.asyncio
async def test_get_session_not_found_returns_none():
    store = CollaborationStore()
    ctx = _ctx()
    result = await store.get_session(tenant_ctx=ctx, session_id="nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_session_wrong_tenant_returns_none():
    store = CollaborationStore()
    ctx1 = _ctx("t1")
    ctx2 = _ctx("t2")
    created = await store.create_session(tenant_ctx=ctx1, name="S", mode="suggest", participants=[])
    sid = created["session_id"]
    result = await store.get_session(tenant_ctx=ctx2, session_id=sid)
    assert result is None


# ── 5. In-memory: close_session ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_session_changes_status():
    store = CollaborationStore()
    ctx = _ctx()
    created = await store.create_session(tenant_ctx=ctx, name="S", mode="suggest", participants=[])
    sid = created["session_id"]

    closed = await store.close_session(tenant_ctx=ctx, session_id=sid)
    assert closed is not None
    assert closed["status"] == "closed"


@pytest.mark.asyncio
async def test_close_session_not_found_returns_none():
    store = CollaborationStore()
    ctx = _ctx()
    result = await store.close_session(tenant_ctx=ctx, session_id="notexist")
    assert result is None


# ── 6. In-memory: append_operation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_append_operation_basic():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=["u1"])
    sid = session["session_id"]

    op = await store.append_operation(
        tenant_ctx=ctx,
        session_id=sid,
        operation={"type": "insert", "pos": 0, "text": "Hello"},
        author="user1",
    )
    assert op["version"] == 1
    assert op["author"] == "user1"
    assert op["session_id"] == sid
    assert "operation_id" in op


@pytest.mark.asyncio
async def test_append_operation_increments_version():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    op1 = await store.append_operation(tenant_ctx=ctx, session_id=sid, operation={"type": "a"}, author="u1")
    op2 = await store.append_operation(tenant_ctx=ctx, session_id=sid, operation={"type": "b"}, author="u1")
    assert op1["version"] == 1
    assert op2["version"] == 2


@pytest.mark.asyncio
async def test_append_operation_version_conflict():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    await store.append_operation(tenant_ctx=ctx, session_id=sid, operation={"type": "a"}, author="u1")

    with pytest.raises(VersionConflictError) as exc_info:
        await store.append_operation(
            tenant_ctx=ctx,
            session_id=sid,
            operation={"type": "b"},
            author="u1",
            expected_version=0,  # wrong — current is 1
        )
    assert exc_info.value.current_version == 1
    assert exc_info.value.expected_version == 0


@pytest.mark.asyncio
async def test_append_operation_no_version_check():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    await store.append_operation(tenant_ctx=ctx, session_id=sid, operation={}, author="u1")
    # Without expected_version, no conflict check
    op = await store.append_operation(tenant_ctx=ctx, session_id=sid, operation={}, author="u1", expected_version=None)
    assert op["version"] == 2


@pytest.mark.asyncio
async def test_append_operation_content_update():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    await store.append_operation(
        tenant_ctx=ctx,
        session_id=sid,
        operation={"type": "content_update", "content": "Updated content"},
        author="u1",
    )

    fetched = await store.get_session(tenant_ctx=ctx, session_id=sid)
    assert fetched["content"] == "Updated content"


@pytest.mark.asyncio
async def test_append_operation_missing_session_raises():
    store = CollaborationStore()
    ctx = _ctx()
    with pytest.raises(KeyError):
        await store.append_operation(
            tenant_ctx=ctx, session_id="nosuchsession", operation={}, author="u1"
        )


# ── 7. In-memory: list_operations ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_operations_empty_initially():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    ops = await store.list_operations(tenant_ctx=ctx, session_id=sid)
    assert ops == []


@pytest.mark.asyncio
async def test_list_operations_returns_all():
    store = CollaborationStore()
    ctx = _ctx()
    session = await store.create_session(tenant_ctx=ctx, name="S", mode="edit", participants=[])
    sid = session["session_id"]

    for i in range(3):
        await store.append_operation(
            tenant_ctx=ctx, session_id=sid,
            operation={"type": "op", "i": i}, author=f"user{i}"
        )

    ops = await store.list_operations(tenant_ctx=ctx, session_id=sid)
    assert len(ops) == 3


@pytest.mark.asyncio
async def test_list_operations_unknown_session_returns_empty():
    store = CollaborationStore()
    ctx = _ctx()
    ops = await store.list_operations(tenant_ctx=ctx, session_id="unknown")
    assert ops == []


# ── 8. DB path: list_sessions ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions_db_path():
    """DB path: sessions returned from SQLAlchemy result."""
    from unittest.mock import AsyncMock, MagicMock

    mock_row = MagicMock()
    mock_row.id = "sess1"
    mock_row.tenant_id = "t1"
    mock_row.name = "DB Session"
    mock_row.mode = "suggest"
    mock_row.status = "active"
    mock_row.content = ""
    mock_row.metadata_json = {"participants": ["u1"], "goal_id": None, "agent_id": None}
    mock_row.created_at = MagicMock()
    mock_row.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    mock_row.updated_at = MagicMock()
    mock_row.updated_at.isoformat.return_value = "2026-01-01T00:00:00"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_row]

    mock_db_session = AsyncMock()
    mock_db_session.execute = AsyncMock(return_value=mock_result)
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_db_session.begin = MagicMock(return_value=mock_begin)
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_db_session

    with MagicMock() as _rls_mock:
        from unittest.mock import patch
        with patch("app.collab.store.sqlalchemy_rls_context") as mock_rls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=None)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_rls.return_value = mock_ctx

            store = CollaborationStore(db_session_factory=db_factory)
            sessions = await store.list_sessions(tenant_ctx=_ctx("t1"))

    assert len(sessions) == 1
    assert sessions[0]["name"] == "DB Session"


# ── 9. _session_to_dict helper ───────────────────────────────────────────────

def test_session_to_dict_uses_participants_arg():
    from app.collab.store import _session_to_dict
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_session.tenant_id = "t1"
    mock_session.name = "Test"
    mock_session.mode = "suggest"
    mock_session.status = "active"
    mock_session.content = ""
    mock_session.metadata_json = {}
    mock_session.created_at = None
    mock_session.updated_at = None

    result = _session_to_dict(mock_session, participants=["user1", "user2"])
    assert result["participants"] == ["user1", "user2"]
    assert result["participant_count"] == 2


def test_session_to_dict_null_timestamps():
    from app.collab.store import _session_to_dict
    mock_session = MagicMock()
    mock_session.id = "s1"
    mock_session.tenant_id = "t1"
    mock_session.name = "Test"
    mock_session.mode = None
    mock_session.status = None
    mock_session.content = None
    mock_session.metadata_json = {}
    mock_session.created_at = None
    mock_session.updated_at = None

    result = _session_to_dict(mock_session)
    assert result["created_at"] == ""
    assert result["updated_at"] == ""
    assert result["mode"] == "suggest"  # default
    assert result["status"] == "active"  # default
