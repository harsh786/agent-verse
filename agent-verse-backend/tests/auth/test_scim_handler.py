"""Comprehensive tests for app/auth/scim_handler.py."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.auth.scim_handler import (
    SCIM_ERROR_SCHEMA,
    SCIM_LIST_RESPONSE,
    SCIM_USER_SCHEMA,
    SCIMHandler,
    _db_row_to_scim_user,
    _scim_error,
    require_scim_auth,
)


# ---------------------------------------------------------------------------
# _scim_error helper
# ---------------------------------------------------------------------------


def test_scim_error_structure():
    err = _scim_error("something went wrong", "invalidValue")
    assert err["schemas"] == [SCIM_ERROR_SCHEMA]
    assert err["detail"] == "something went wrong"
    assert err["scimType"] == "invalidValue"


def test_scim_error_default_type():
    err = _scim_error("bad input")
    assert err["scimType"] == "invalidValue"


# ---------------------------------------------------------------------------
# _db_row_to_scim_user helper
# ---------------------------------------------------------------------------


def _make_mapping_row(**kwargs) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": "uuid-1",
        "email": "user@corp.com",
        "display_name": "User Corp",
        "is_active": True,
        "scim_id": "ext-id-1",
        "created_at": datetime(2024, 1, 1),
        "updated_at": datetime(2024, 6, 1),
    }
    defaults.update(kwargs)
    row._mapping = defaults
    return row


def test_db_row_to_scim_user_with_mapping():
    row = _make_mapping_row()
    result = _db_row_to_scim_user(row)
    assert result["schemas"] == [SCIM_USER_SCHEMA]
    assert result["id"] == "uuid-1"
    assert result["userName"] == "user@corp.com"
    assert result["displayName"] == "User Corp"
    assert result["active"] is True
    assert result["externalId"] == "ext-id-1"
    assert result["meta"]["resourceType"] == "User"
    assert result["meta"]["location"] == "/scim/v2/Users/uuid-1"
    assert len(result["emails"]) == 1
    assert result["emails"][0]["primary"] is True


def test_db_row_to_scim_user_with_tuple():
    # Positional tuple: id, email, display_name, is_active, scim_id, created_at, updated_at
    row = ("uuid-2", "b@corp.com", "B Corp", False, "ext-2",
           datetime(2024, 1, 1), datetime(2024, 6, 1))
    result = _db_row_to_scim_user(row)
    assert result["id"] == "uuid-2"
    assert result["userName"] == "b@corp.com"
    assert result["active"] is False
    assert result["externalId"] == "ext-2"


def test_db_row_to_scim_user_tuple_falls_back_id_for_scim_id():
    row = ("uuid-3", "c@corp.com", "C Corp", True, None,
           datetime(2024, 1, 1), datetime(2024, 6, 1))
    result = _db_row_to_scim_user(row)
    assert result["externalId"] == "uuid-3"


def test_db_row_to_scim_user_display_name_falls_back_to_email():
    # Use positional tuple — tuple path uses `row[2] or email` which handles None
    row = ("uuid-4", "d@corp.com", None, True, "ext-4",
           datetime(2024, 1, 1), datetime(2024, 6, 1))
    result = _db_row_to_scim_user(row)
    assert result["displayName"] == "d@corp.com"


# ---------------------------------------------------------------------------
# require_scim_auth
# ---------------------------------------------------------------------------


def _make_request(auth_header: str = "", db=None) -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": auth_header}
    app_mock = MagicMock()
    app_mock.state.db_session_factory = db
    request.app = app_mock
    return request


async def test_require_scim_auth_missing_bearer_raises_401():
    request = _make_request(auth_header="")
    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 401


async def test_require_scim_auth_wrong_scheme_raises_401():
    request = _make_request(auth_header="Basic abc123")
    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 401


async def test_require_scim_auth_empty_token_raises_401():
    request = _make_request(auth_header="Bearer   ")
    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 401


async def test_require_scim_auth_no_db_raises_503():
    request = _make_request(auth_header="Bearer validtoken")
    request.app.state.db_session_factory = None
    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 503


async def test_require_scim_auth_valid_token_returns_tenant_id():
    raw_token = "supersecrettoken"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    row_mock = MagicMock()
    row_mock.__getitem__ = lambda self, i: "tenant-abc" if i == 0 else None

    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.fetchone.return_value = row_mock
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    db_factory = MagicMock(return_value=session_mock)

    request = _make_request(auth_header=f"Bearer {raw_token}")
    request.app.state.db_session_factory = db_factory

    tenant_id = await require_scim_auth(request)
    assert tenant_id == "tenant-abc"


async def test_require_scim_auth_invalid_token_raises_401():
    session_mock = AsyncMock()
    result_mock = MagicMock()
    result_mock.fetchone.return_value = None  # No matching token
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    db_factory = MagicMock(return_value=session_mock)
    request = _make_request(auth_header="Bearer badtoken")
    request.app.state.db_session_factory = db_factory

    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 401


async def test_require_scim_auth_db_error_raises_503():
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(side_effect=Exception("DB connection failed"))
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    db_factory = MagicMock(return_value=session_mock)
    request = _make_request(auth_header="Bearer sometoken")
    request.app.state.db_session_factory = db_factory

    with pytest.raises(HTTPException) as exc_info:
        await require_scim_auth(request)
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# SCIMHandler helpers
# ---------------------------------------------------------------------------


def _make_handler(config: dict | None = None, db_factory: Any = None) -> SCIMHandler:
    if config is None:
        config = {
            "allow_user_create": True,
            "allow_user_update": True,
            "allow_user_delete": False,
            "default_role": "viewer",
            "group_role_map": {"Admins": "admin"},
        }
    if db_factory is None:
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=session_mock)
    return SCIMHandler(tenant_id="t1", config=config, db_factory=db_factory)


def _session_with_rows(rows: list, total: int = 0) -> tuple[AsyncMock, Any]:
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock = MagicMock()
    result_mock.fetchall.return_value = rows
    result_mock.scalar.return_value = total
    result_mock.fetchone.return_value = rows[0] if rows else None

    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)
    return session_mock, db_factory


# ---------------------------------------------------------------------------
# SCIMHandler._map_groups_to_role
# ---------------------------------------------------------------------------


def test_map_groups_to_role_matches_group():
    handler = _make_handler(config={
        "group_role_map": {"Admins": "admin", "Ops": "operator"},
        "default_role": "viewer",
    })
    role = handler._map_groups_to_role([{"display": "Ops"}])
    assert role == "operator"


def test_map_groups_to_role_no_match_returns_default():
    handler = _make_handler(config={"group_role_map": {}, "default_role": "viewer"})
    role = handler._map_groups_to_role([{"display": "UnknownGroup"}])
    assert role == "viewer"


def test_map_groups_to_role_empty_groups_returns_default():
    handler = _make_handler(config={"group_role_map": {"Admins": "admin"}, "default_role": "viewer"})
    role = handler._map_groups_to_role([])
    assert role == "viewer"


# ---------------------------------------------------------------------------
# SCIMHandler.list_users
# ---------------------------------------------------------------------------


async def test_list_users_returns_list_response_format():
    row = _make_mapping_row()
    session_mock, db_factory = _session_with_rows([row], total=1)
    handler = _make_handler(db_factory=db_factory)

    result = await handler.list_users()
    assert result["schemas"] == [SCIM_LIST_RESPONSE]
    assert result["totalResults"] == 1
    assert result["startIndex"] == 1
    assert len(result["Resources"]) == 1


async def test_list_users_pagination():
    session_mock, db_factory = _session_with_rows([], total=0)
    handler = _make_handler(db_factory=db_factory)

    result = await handler.list_users(start_index=5, count=10)
    assert result["startIndex"] == 5
    assert result["totalResults"] == 0


async def test_list_users_db_error_returns_empty():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("db error"))
    db_factory = MagicMock(return_value=session_mock)
    handler = _make_handler(db_factory=db_factory)

    result = await handler.list_users()
    assert result["totalResults"] == 0
    assert result["Resources"] == []


# ---------------------------------------------------------------------------
# SCIMHandler.get_user
# ---------------------------------------------------------------------------


async def test_get_user_returns_scim_user():
    row = _make_mapping_row()
    _, db_factory = _session_with_rows([row])
    handler = _make_handler(db_factory=db_factory)

    result = await handler.get_user("ext-id-1")
    assert result["schemas"] == [SCIM_USER_SCHEMA]


async def test_get_user_not_found_raises_404():
    _, db_factory = _session_with_rows([])
    handler = _make_handler(db_factory=db_factory)

    with pytest.raises(HTTPException) as exc_info:
        await handler.get_user("nonexistent-id")
    assert exc_info.value.status_code == 404


async def test_get_user_db_error_raises_404():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("db error"))
    db_factory = MagicMock(return_value=session_mock)
    handler = _make_handler(db_factory=db_factory)

    with pytest.raises(HTTPException) as exc_info:
        await handler.get_user("some-id")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# SCIMHandler.create_user
# ---------------------------------------------------------------------------


async def test_create_user_disabled_raises_403():
    handler = _make_handler(config={"allow_user_create": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.create_user({"userName": "user@corp.com"})
    assert exc_info.value.status_code == 403


async def test_create_user_missing_email_raises_400():
    handler = _make_handler()
    with pytest.raises(HTTPException) as exc_info:
        await handler.create_user({})
    assert exc_info.value.status_code == 400


async def test_create_user_success():
    row = _make_mapping_row()
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    # execute returns different results for INSERT vs SELECT
    result_mock_insert = MagicMock()
    result_mock_select = MagicMock()
    result_mock_select.fetchone.return_value = row

    session_mock.execute = AsyncMock(side_effect=[
        result_mock_insert,  # INSERT
        result_mock_select,  # SELECT
    ])
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_create": True, "default_role": "viewer", "group_role_map": {}},
        db_factory=db_factory,
    )

    scim_data = {
        "userName": "newuser@corp.com",
        "name": {"givenName": "New", "familyName": "User"},
        "active": True,
        "externalId": "ext-new",
    }
    result = await handler.create_user(scim_data)
    assert result["schemas"] == [SCIM_USER_SCHEMA]


async def test_create_user_uses_email_from_emails_array():
    row = _make_mapping_row()
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    result_mock_insert = MagicMock()
    result_mock_select = MagicMock()
    result_mock_select.fetchone.return_value = row

    session_mock.execute = AsyncMock(side_effect=[result_mock_insert, result_mock_select])
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_create": True, "default_role": "viewer", "group_role_map": {}},
        db_factory=db_factory,
    )

    scim_data = {"emails": [{"value": "fromarray@corp.com", "primary": True}], "active": True}
    result = await handler.create_user(scim_data)
    assert result["schemas"] == [SCIM_USER_SCHEMA]


async def test_create_user_db_error_raises_500():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("constraint violation"))
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_create": True, "default_role": "viewer", "group_role_map": {}},
        db_factory=db_factory,
    )

    with pytest.raises(HTTPException) as exc_info:
        await handler.create_user({"userName": "user@corp.com"})
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# SCIMHandler.update_user (PUT)
# ---------------------------------------------------------------------------


async def test_update_user_disabled_raises_403():
    handler = _make_handler(config={"allow_user_update": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("ext-1", {"active": True})
    assert exc_info.value.status_code == 403


async def test_update_user_deactivate_without_allow_delete_raises_403():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_update": True, "allow_user_delete": False},
        db_factory=db_factory,
    )
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("ext-1", {"active": False})
    assert exc_info.value.status_code == 403


async def test_update_user_put_success():
    row = _make_mapping_row()
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.fetchone.return_value = row
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_update": True, "allow_user_delete": True},
        db_factory=db_factory,
    )
    result = await handler.update_user(
        "ext-1",
        {"active": True, "name": {"givenName": "Alice", "familyName": "Smith"}},
    )
    assert result["schemas"] == [SCIM_USER_SCHEMA]


async def test_update_user_not_found_raises_404():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.fetchone.return_value = None
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_update": True, "allow_user_delete": True},
        db_factory=db_factory,
    )
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("nonexistent", {"active": True})
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# SCIMHandler.update_user (PATCH)
# ---------------------------------------------------------------------------


async def test_update_user_patch_deactivate():
    row = _make_mapping_row(is_active=False)
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    result_mock = MagicMock()
    result_mock.fetchone.return_value = row
    session_mock.execute = AsyncMock(return_value=result_mock)
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_update": True, "allow_user_delete": True},
        db_factory=db_factory,
    )
    patch_data = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
    result = await handler.update_user("ext-1", patch_data, partial=True)
    assert result["active"] is False


async def test_update_user_patch_deactivate_blocked():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_update": True, "allow_user_delete": False},
        db_factory=db_factory,
    )
    patch_data = {"Operations": [{"op": "replace", "path": "active", "value": False}]}
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("ext-1", patch_data, partial=True)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# SCIMHandler.delete_user
# ---------------------------------------------------------------------------


async def test_delete_user_disabled_raises_403():
    handler = _make_handler(config={"allow_user_delete": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.delete_user("ext-1")
    assert exc_info.value.status_code == 403


async def test_delete_user_success():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(
        config={"allow_user_delete": True},
        db_factory=db_factory,
    )
    await handler.delete_user("ext-1")  # Should not raise
    session_mock.commit.assert_awaited_once()


async def test_delete_user_db_error_raises_500():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("db error"))
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    handler = _make_handler(config={"allow_user_delete": True}, db_factory=db_factory)
    with pytest.raises(HTTPException) as exc_info:
        await handler.delete_user("ext-1")
    assert exc_info.value.status_code == 500
