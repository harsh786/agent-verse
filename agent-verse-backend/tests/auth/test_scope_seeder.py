"""Comprehensive tests for app/auth/scope_seeder.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.auth.scope_seeder import (
    BUILTIN_ROLES,
    BUILTIN_SCOPES,
    seed_builtin_scopes,
    seed_scope_definitions,
)


# ---------------------------------------------------------------------------
# Static data integrity
# ---------------------------------------------------------------------------


def test_builtin_scopes_non_empty():
    assert len(BUILTIN_SCOPES) > 0


def test_builtin_scopes_required_fields():
    required = {"scope", "resource", "action", "description", "risk_level"}
    for s in BUILTIN_SCOPES:
        missing = required - set(s.keys())
        assert not missing, f"Missing fields in scope {s}: {missing}"


def test_builtin_scopes_valid_risk_levels():
    valid_levels = {"low", "medium", "high", "critical"}
    for s in BUILTIN_SCOPES:
        assert s["risk_level"] in valid_levels, (
            f"Scope {s['scope']} has invalid risk level: {s['risk_level']}"
        )


def test_builtin_scopes_contains_goals_read():
    scopes = [s["scope"] for s in BUILTIN_SCOPES]
    assert "goals:read" in scopes


def test_builtin_scopes_contains_governance_approve():
    scopes = [s["scope"] for s in BUILTIN_SCOPES]
    assert "governance:approve" in scopes


def test_builtin_scopes_no_duplicates():
    scopes = [s["scope"] for s in BUILTIN_SCOPES]
    assert len(scopes) == len(set(scopes)), "Duplicate scope definitions found"


def test_builtin_roles_non_empty():
    assert len(BUILTIN_ROLES) > 0


def test_builtin_roles_required_fields():
    required = {"id", "name", "permissions", "is_template", "system_role"}
    for r in BUILTIN_ROLES:
        missing = required - set(r.keys())
        assert not missing, f"Missing fields in role {r}: {missing}"


def test_builtin_roles_unique_ids():
    ids = [r["id"] for r in BUILTIN_ROLES]
    assert len(ids) == len(set(ids)), "Duplicate role IDs found"


def test_builtin_roles_all_are_templates():
    for r in BUILTIN_ROLES:
        assert r["is_template"] is True


def test_builtin_role_admin_has_all_scopes():
    all_scopes = {s["scope"] for s in BUILTIN_SCOPES}
    admin_role = next(r for r in BUILTIN_ROLES if r["name"] == "admin")
    # Admin should have all scopes
    admin_perms = set(admin_role["permissions"])
    assert admin_perms == all_scopes


def test_builtin_role_viewer_has_only_read_scopes():
    viewer_role = next(r for r in BUILTIN_ROLES if r["name"] == "viewer")
    for perm in viewer_role["permissions"]:
        # Viewer role only has read scopes
        action = perm.split(":")[1]
        assert action in ("read",), f"Viewer has non-read scope: {perm}"


def test_builtin_role_approver_has_governance_approve():
    approver = next(r for r in BUILTIN_ROLES if r["name"] == "approver")
    assert "governance:approve" in approver["permissions"]


def test_builtin_role_agent_service_minimal_permissions():
    agent_svc = next(r for r in BUILTIN_ROLES if r["name"] == "agent_service")
    perms = set(agent_svc["permissions"])
    # Should not have admin or delete capabilities
    assert "tenancy:write" not in perms
    assert "goals:delete" not in perms


# ---------------------------------------------------------------------------
# seed_scope_definitions
# ---------------------------------------------------------------------------


async def test_seed_scope_definitions_inserts_all_scopes():
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock()
    db_mock.commit = AsyncMock()

    await seed_scope_definitions(db_mock)

    assert db_mock.execute.call_count == len(BUILTIN_SCOPES)
    db_mock.commit.assert_awaited_once()


async def test_seed_scope_definitions_passes_correct_params():
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock()
    db_mock.commit = AsyncMock()

    await seed_scope_definitions(db_mock)

    # Check first call has the right scope value
    first_call_kwargs = db_mock.execute.call_args_list[0][0][1]
    assert "scope" in first_call_kwargs
    assert "resource" in first_call_kwargs
    assert "action" in first_call_kwargs
    assert "risk_level" in first_call_kwargs


async def test_seed_scope_definitions_handles_db_error_gracefully():
    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(side_effect=Exception("DB connection failed"))

    # Should not raise — handles exception gracefully
    await seed_scope_definitions(db_mock)


# ---------------------------------------------------------------------------
# seed_builtin_scopes
# ---------------------------------------------------------------------------


async def test_seed_builtin_scopes_calls_both_seeders():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    await seed_builtin_scopes(db_factory)

    # Should have been called for scopes + roles
    assert session_mock.execute.call_count > 0
    assert session_mock.commit.call_count >= 1


async def test_seed_builtin_scopes_inserts_builtin_roles():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    await seed_builtin_scopes(db_factory)

    # Total calls = len(BUILTIN_SCOPES) + len(BUILTIN_ROLES)
    expected_calls = len(BUILTIN_SCOPES) + len(BUILTIN_ROLES)
    assert session_mock.execute.call_count == expected_calls


async def test_seed_builtin_scopes_handles_db_factory_error():
    db_factory = MagicMock(side_effect=Exception("cannot connect"))

    # Should not raise — non-fatal startup error
    await seed_builtin_scopes(db_factory)


async def test_seed_builtin_scopes_handles_execute_error():
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("syntax error"))
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    # Should not raise — non-fatal startup error
    await seed_builtin_scopes(db_factory)


async def test_seed_builtin_scopes_permissions_serialized_as_json():
    """Verify that permissions list is passed as JSON-serialized string."""
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    await seed_builtin_scopes(db_factory)

    # Find a call with 'permissions' in kwargs (role insert calls)
    role_calls = [
        c for c in session_mock.execute.call_args_list
        if len(c[0]) > 1 and "permissions" in c[0][1]
    ]
    assert len(role_calls) > 0
    # The permissions value should be a JSON string
    import json
    for c in role_calls:
        perms = c[0][1]["permissions"]
        # Should be a valid JSON string (list)
        parsed = json.loads(perms)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Regression: CAST(:permissions AS jsonb) SQL syntax fix
# ---------------------------------------------------------------------------

def test_seed_builtin_scopes_sql_uses_cast_not_double_colon():
    """seed_builtin_scopes must use CAST(:permissions AS jsonb), not :permissions::jsonb.

    Regression: SQLAlchemy's text() binder misparses '::' cast syntax after a
    named parameter (e.g. ':permissions::jsonb'), treating the '::' as part of
    the param name.  The fix replaces it with standard SQL CAST() syntax.
    """
    import inspect
    from app.auth import scope_seeder

    source = inspect.getsource(scope_seeder)

    assert ":permissions::jsonb" not in source, (
        "scope_seeder still uses :permissions::jsonb — SQLAlchemy text() cannot "
        "parse '::' cast after a named parameter; use CAST(:permissions AS jsonb) instead"
    )
    assert "CAST(:permissions AS jsonb)" in source, (
        "scope_seeder must use CAST(:permissions AS jsonb) for the permissions column"
    )
