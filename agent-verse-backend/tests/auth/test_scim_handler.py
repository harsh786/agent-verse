"""Comprehensive tests for app/auth/scim_handler.py."""
from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.auth.scim_handler import (
    SCIM_ERROR_SCHEMA,
    SCIMHandler,
    _scim_error,
    require_scim_auth,
)

# ---------------------------------------------------------------------------
# Session fakes
# ---------------------------------------------------------------------------


def _begin_cm() -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _new_session() -> AsyncMock:
    """AsyncMock session usable as ``async with db() as s, s.begin(): ...``.

    Every DB touch in scim_handler now runs inside an explicit transaction (the
    RLS GUCs are ``SET LOCAL``), so ``begin()`` must be an async context manager
    — an AsyncMock child method would return a bare coroutine instead.
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=_begin_cm())
    return session


def _sql(session: AsyncMock) -> list[str]:
    """The SQL text of every statement executed on *session*, in order."""
    return [str(c.args[0]) for c in session.execute.await_args_list]


def _params(session: AsyncMock) -> list[dict]:
    return [
        (c.args[1] if len(c.args) > 1 else {}) for c in session.execute.await_args_list
    ]


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

    session_mock = _new_session()
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


async def test_require_scim_auth_presents_token_hash_guc_before_lookup():
    """Pre-auth lookup under RLS: there is no tenant yet, so the token's hash is
    presented as ``app.scim_token_hash`` (matched by the SELECT-only
    ``scim_tokens_by_presented_hash`` policy) inside the SAME transaction as the
    lookup — ``SET LOCAL`` would be gone otherwise. It must never switch row
    security off (a NOBYPASSRLS role may not) or set a tenant GUC it cannot know.
    """
    raw_token = "scim-bearer-xyz"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    session_mock = _new_session()
    result_mock = MagicMock()
    result_mock.fetchone.return_value = ("tenant-hash",)
    session_mock.execute = AsyncMock(return_value=result_mock)
    begin = session_mock.begin.return_value

    request = _make_request(auth_header=f"Bearer {raw_token}")
    request.app.state.db_session_factory = MagicMock(return_value=session_mock)

    assert await require_scim_auth(request) == "tenant-hash"

    session_mock.begin.assert_called_once()
    begin.__aenter__.assert_awaited_once()
    sql, params = _sql(session_mock), _params(session_mock)
    assert "set_config('app.scim_token_hash'" in sql[0]
    assert params[0] == {"h": token_hash}
    assert "FROM scim_tokens" in sql[1]
    assert params[1] == {"hash": token_hash}
    joined = " ".join(sql).lower()
    assert "row_security" not in joined
    assert "app.tenant_id" not in joined


async def test_require_scim_auth_invalid_token_raises_401():
    session_mock = _new_session()
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
    session_mock = _new_session()
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
        session_mock = _new_session()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=session_mock)
    return SCIMHandler(tenant_id="t1", config=config, db_factory=db_factory)


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
# SCIMHandler user operations — validation / config / error mapping.
#
# The success paths (and the SQL itself) are exercised against the REAL
# migrated schema as a NOBYPASSRLS role in tests/tenancy/test_tenancy_auth_integration.py;
# the old mock tests here asserted SQL against users columns that never existed.
# ---------------------------------------------------------------------------


def _failing_handler(config: dict | None = None) -> tuple[SCIMHandler, AsyncMock]:
    session_mock = _new_session()
    session_mock.execute = AsyncMock(side_effect=Exception("db error"))
    handler = _make_handler(
        config=config or {"allow_user_create": True, "allow_user_delete": True},
        db_factory=MagicMock(return_value=session_mock),
    )
    return handler, session_mock


async def test_list_users_db_error_is_500_not_empty_200():
    handler, _ = _failing_handler()
    with pytest.raises(HTTPException) as exc_info:
        await handler.list_users()
    assert exc_info.value.status_code == 500


async def test_get_user_db_error_is_500_not_404():
    handler, _ = _failing_handler()
    with pytest.raises(HTTPException) as exc_info:
        await handler.get_user("ext-1")
    assert exc_info.value.status_code == 500


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


async def test_create_user_db_error_raises_500():
    handler, _ = _failing_handler()
    with pytest.raises(HTTPException) as exc_info:
        await handler.create_user({"userName": "user@corp.com"})
    assert exc_info.value.status_code == 500


async def test_update_user_disabled_raises_403():
    handler = _make_handler(config={"allow_user_update": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("ext-1", {"active": True})
    assert exc_info.value.status_code == 403


async def test_update_user_deactivate_without_allow_delete_raises_403():
    handler = _make_handler(config={"allow_user_update": True, "allow_user_delete": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user("ext-1", {"active": False})
    assert exc_info.value.status_code == 403


async def test_update_user_patch_deactivate_blocked():
    handler = _make_handler(config={"allow_user_update": True, "allow_user_delete": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.update_user(
            "ext-1",
            {"Operations": [{"op": "replace", "path": "active", "value": False}]},
            partial=True,
        )
    assert exc_info.value.status_code == 403


async def test_delete_user_disabled_raises_403():
    handler = _make_handler(config={"allow_user_delete": False})
    with pytest.raises(HTTPException) as exc_info:
        await handler.delete_user("ext-1")
    assert exc_info.value.status_code == 403


async def test_handler_db_error_rolls_back_transaction_then_maps_to_500():
    """Errors are caught OUTSIDE the transaction block, so the transaction is
    rolled back (begin() sees the exception) instead of committing an aborted
    Postgres transaction — and the tenant GUC was set first."""
    handler, session_mock = _failing_handler()
    with pytest.raises(HTTPException) as exc_info:
        await handler.delete_user("ext-1")
    assert exc_info.value.status_code == 500
    assert "set_config('app.tenant_id'" in _sql(session_mock)[0]
    exit_args = session_mock.begin.return_value.__aexit__.await_args.args
    assert exit_args[0] is not None


async def test_deprovision_whose_session_cache_purge_fails_is_503_not_success():
    """SAML-01: SCIM DELETE revokes the user's sessions in the same transaction
    and then purges their cached contexts; when that purge fails the IdP gets a
    503 (it retries) — never a 204 while a cached session still authenticates."""
    from types import SimpleNamespace

    from app.auth.user_sessions import SessionStoreUnavailableError

    store = MagicMock()
    store.purge_cache = AsyncMock(side_effect=SessionStoreUnavailableError("redis down"))
    session_mock = _new_session()
    handler = SCIMHandler(
        tenant_id="tenant-1",
        config={"allow_user_delete": True},
        db_factory=MagicMock(return_value=session_mock),
        session_store=store,
    )
    member = (SimpleNamespace(id="u1"), SimpleNamespace(status="active"))
    handler._find_member = AsyncMock(return_value=member)  # type: ignore[method-assign]
    handler._revoke_sessions = AsyncMock(return_value=["digest-1"])  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await handler.delete_user("u1")
    assert exc_info.value.status_code == 503
    assert member[1].status == "deactivated"
    handler._revoke_sessions.assert_awaited_once_with(session_mock, "u1")
    store.purge_cache.assert_awaited_once_with(["digest-1"])
