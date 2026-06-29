"""Additional tests for collab/store.py — DB paths, _operation_to_dict,
close_session DB, append_operation DB, list_operations DB.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.collab.store import (
    CollaborationStore,
    VersionConflictError,
    _operation_to_dict,
    _session_to_dict,
)
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k1")


def _mock_rls():
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=None)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


def _db_factory(mock_session):
    def factory():
        return mock_session
    return factory


def _full_db_session():
    mock_session = AsyncMock()
    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    return mock_session


def _make_session_row(session_id="s1", tenant_id="t1", name="Test Session",
                       mode="suggest", status="active", content=""):
    row = MagicMock()
    row.id = session_id
    row.tenant_id = tenant_id
    row.name = name
    row.mode = mode
    row.status = status
    row.content = content
    row.metadata_json = {"participants": ["u1"], "goal_id": "g1", "agent_id": None}
    ts = MagicMock()
    ts.isoformat.return_value = "2026-01-01T00:00:00"
    row.created_at = ts
    row.updated_at = ts
    return row


# ── _operation_to_dict ────────────────────────────────────────────────────────

def test_operation_to_dict_basic():
    op = MagicMock()
    op.id = "op1"
    op.session_id = "s1"
    op.tenant_id = "t1"
    op.version = 3
    op.operation = {"type": "insert"}
    op.author = "user1"
    ts = MagicMock()
    ts.isoformat.return_value = "2026-01-01T00:00:00"
    op.created_at = ts

    result = _operation_to_dict(op)
    assert result["operation_id"] == "op1"
    assert result["version"] == 3
    assert result["author"] == "user1"
    assert result["operation"] == {"type": "insert"}


def test_operation_to_dict_null_author():
    op = MagicMock()
    op.id = "op2"
    op.session_id = "s1"
    op.tenant_id = "t1"
    op.version = 1
    op.operation = {}
    op.author = None
    op.created_at = None

    result = _operation_to_dict(op)
    assert result["author"] == ""
    assert result["created_at"] == ""


def test_operation_to_dict_null_created_at():
    op = MagicMock()
    op.id = "op3"
    op.session_id = "s1"
    op.tenant_id = "t1"
    op.version = 2
    op.operation = {"type": "delete"}
    op.author = "u2"
    op.created_at = None

    result = _operation_to_dict(op)
    assert result["created_at"] == ""


# ── DB path: create_session ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_db_path():
    mock_session = _full_db_session()
    mock_session.execute = AsyncMock()

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.create_session(
            tenant_ctx=_ctx(),
            name="DB Session",
            mode="suggest",
            participants=["u1"],
            goal_id="g1",
            agent_id="a1",
            content="initial",
        )

    assert result["name"] == "DB Session"
    assert result["mode"] == "suggest"
    assert result["participants"] == ["u1"]


# ── DB path: get_session ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_db_path_found():
    row = _make_session_row()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.get_session(tenant_ctx=_ctx(), session_id="s1")

    assert result is not None
    assert result["name"] == "Test Session"


@pytest.mark.asyncio
async def test_get_session_db_path_not_found():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.get_session(tenant_ctx=_ctx(), session_id="notfound")

    assert result is None


# ── DB path: close_session ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_session_db_path():
    row = _make_session_row()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.close_session(tenant_ctx=_ctx(), session_id="s1")

    assert result is not None
    assert row.status == "closed"


@pytest.mark.asyncio
async def test_close_session_db_path_not_found():
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.close_session(tenant_ctx=_ctx(), session_id="notfound")

    assert result is None


# ── DB path: list_operations ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_operations_db_path():
    op1 = MagicMock()
    op1.id = "op1"
    op1.session_id = "s1"
    op1.tenant_id = "t1"
    op1.version = 1
    op1.operation = {"type": "insert"}
    op1.author = "u1"
    ts = MagicMock()
    ts.isoformat.return_value = "2026-01-01T00:00:00"
    op1.created_at = ts

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [op1]

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.list_operations(tenant_ctx=_ctx(), session_id="s1")

    assert len(result) == 1
    assert result[0]["version"] == 1


@pytest.mark.asyncio
async def test_list_operations_db_path_empty():
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.list_operations(tenant_ctx=_ctx(), session_id="s-empty")

    assert result == []


# ── DB path: append_operation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_append_operation_db_path_success():
    session_row = MagicMock()
    session_row.first.return_value = ("s1", "", MagicMock())

    inserted_row = MagicMock()
    ts = MagicMock()
    ts.isoformat.return_value = "2026-01-01T00:00:00"
    inserted_row.__iter__ = MagicMock(return_value=iter(["op-id", 1, ts]))

    mock_insert_result = MagicMock()
    mock_insert_result.fetchone.return_value = ("op-id", 1, ts)

    call_count = [0]

    async def side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # session exists check
            result = MagicMock()
            result.first.return_value = ("s1", "", MagicMock())
            return result
        elif call_count[0] == 2:
            # insert
            return mock_insert_result
        else:
            # update content
            return MagicMock()

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(side_effect=side_effect)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.append_operation(
            tenant_ctx=_ctx(),
            session_id="s1",
            operation={"type": "insert", "text": "hello"},
            author="user1",
        )

    assert result["version"] == 1
    assert result["author"] == "user1"


@pytest.mark.asyncio
async def test_append_operation_db_path_session_not_found():
    async def side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        result.first.return_value = None  # session not found
        return result

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(side_effect=side_effect)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        with pytest.raises(KeyError):
            await store.append_operation(
                tenant_ctx=_ctx(),
                session_id="not-found",
                operation={"type": "insert"},
                author="u1",
            )


@pytest.mark.asyncio
async def test_append_operation_db_path_version_conflict():
    # Session exists but INSERT returns None (HAVING clause rejected)
    call_count = [0]
    max_version_result = MagicMock()
    max_version_result.scalar.return_value = 5

    async def side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # session exists
            result = MagicMock()
            result.first.return_value = ("s1", "", MagicMock())
            return result
        elif call_count[0] == 2:
            # INSERT returned None (version conflict)
            result = MagicMock()
            result.fetchone.return_value = None
            return result
        else:
            # MAX version query
            return max_version_result

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(side_effect=side_effect)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        with pytest.raises(VersionConflictError) as exc_info:
            await store.append_operation(
                tenant_ctx=_ctx(),
                session_id="s1",
                operation={"type": "insert"},
                author="u1",
                expected_version=3,  # wrong version
            )
    assert exc_info.value.current_version == 5
    assert exc_info.value.expected_version == 3


@pytest.mark.asyncio
async def test_append_operation_db_content_update():
    """content_update op also updates the session content column."""
    inserted_ts = MagicMock()
    inserted_ts.isoformat.return_value = "2026-01-01T00:00:00"

    call_count = [0]

    async def side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            result = MagicMock()
            result.first.return_value = ("s1", "old content", MagicMock())
            return result
        elif call_count[0] == 2:
            result = MagicMock()
            result.fetchone.return_value = ("op-id", 1, inserted_ts)
            return result
        else:
            return MagicMock()

    mock_session = _full_db_session()
    mock_session.execute = AsyncMock(side_effect=side_effect)

    with patch("app.collab.store.sqlalchemy_rls_context", return_value=_mock_rls()):
        store = CollaborationStore(db_session_factory=_db_factory(mock_session))
        result = await store.append_operation(
            tenant_ctx=_ctx(),
            session_id="s1",
            operation={"type": "content_update", "content": "new content"},
            author="user1",
        )

    assert result["version"] == 1
    # Third execute call should have been made (content update)
    assert call_count[0] >= 3
