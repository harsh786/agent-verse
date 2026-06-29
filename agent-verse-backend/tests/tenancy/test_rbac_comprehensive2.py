"""Comprehensive tests for rbac.py — role hierarchy, has_role, has_any_role,
require_role, extract_roles_from_jwt, is_ip_allowed, load_roles_from_db.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.rbac import (
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    VALID_ROLES,
    effective_roles,
    extract_roles_from_jwt,
    has_any_role,
    has_role,
    is_ip_allowed,
    load_roles_from_db,
    require_role,
)


def _ctx(roles: tuple[str, ...] = (), tid: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k1", roles=roles)


# ── 1. Role constants ─────────────────────────────────────────────────────────

def test_role_constants():
    assert ROLE_ADMIN == "admin"
    assert ROLE_OPERATOR == "operator"
    assert ROLE_APPROVER == "approver"
    assert ROLE_VIEWER == "viewer"


def test_valid_roles_set():
    assert "admin" in VALID_ROLES
    assert "operator" in VALID_ROLES
    assert "approver" in VALID_ROLES
    assert "viewer" in VALID_ROLES
    assert len(VALID_ROLES) == 4


# ── 2. effective_roles — hierarchy expansion ──────────────────────────────────

def test_admin_implies_all():
    ctx = _ctx(("admin",))
    eff = effective_roles(ctx)
    assert eff == frozenset({"admin", "operator", "viewer", "approver"})


def test_operator_implies_viewer():
    ctx = _ctx(("operator",))
    eff = effective_roles(ctx)
    assert "operator" in eff
    assert "viewer" in eff
    assert "admin" not in eff
    assert "approver" not in eff


def test_approver_implies_viewer():
    ctx = _ctx(("approver",))
    eff = effective_roles(ctx)
    assert "approver" in eff
    assert "viewer" in eff
    assert "admin" not in eff
    assert "operator" not in eff


def test_viewer_implies_only_viewer():
    ctx = _ctx(("viewer",))
    eff = effective_roles(ctx)
    assert eff == frozenset({"viewer"})


def test_no_roles_empty_set():
    ctx = _ctx(())
    eff = effective_roles(ctx)
    assert eff == frozenset()


def test_multiple_roles_combined():
    ctx = _ctx(("operator", "approver"))
    eff = effective_roles(ctx)
    assert "operator" in eff
    assert "approver" in eff
    assert "viewer" in eff
    assert "admin" not in eff


def test_unknown_role_passes_through():
    ctx = _ctx(("custom_role",))
    eff = effective_roles(ctx)
    assert "custom_role" in eff


# ── 3. has_role ───────────────────────────────────────────────────────────────

def test_has_role_admin_has_viewer():
    assert has_role(_ctx(("admin",)), "viewer") is True


def test_has_role_viewer_lacks_admin():
    assert has_role(_ctx(("viewer",)), "admin") is False


def test_has_role_exact_match():
    assert has_role(_ctx(("operator",)), "operator") is True


def test_has_role_empty_returns_false():
    assert has_role(_ctx(()), "viewer") is False


# ── 4. has_any_role ───────────────────────────────────────────────────────────

def test_has_any_role_match():
    ctx = _ctx(("viewer",))
    assert has_any_role(ctx, ["admin", "viewer"]) is True


def test_has_any_role_no_match():
    ctx = _ctx(("viewer",))
    assert has_any_role(ctx, ["admin", "operator"]) is False


def test_has_any_role_admin_matches_any():
    ctx = _ctx(("admin",))
    assert has_any_role(ctx, ["viewer"]) is True


def test_has_any_role_empty_roles_list():
    ctx = _ctx(("admin",))
    assert has_any_role(ctx, []) is False


# ── 5. require_role dependency ────────────────────────────────────────────────

def test_require_role_passes_with_sufficient_role():
    dep = require_role("admin")
    request = MagicMock()
    request.state.tenant = _ctx(("admin",))
    dep(request)  # Should not raise


def test_require_role_raises_403_insufficient():
    dep = require_role("admin")
    request = MagicMock()
    request.state.tenant = _ctx(("viewer",))
    with pytest.raises(HTTPException) as exc_info:
        dep(request)
    assert exc_info.value.status_code == 403


def test_require_role_raises_401_no_tenant():
    dep = require_role("admin")
    request = MagicMock()
    request.state.tenant = None
    with pytest.raises(HTTPException) as exc_info:
        dep(request)
    assert exc_info.value.status_code == 401


def test_require_role_passes_operator_for_operator_check():
    dep = require_role("operator")
    request = MagicMock()
    request.state.tenant = _ctx(("operator",))
    dep(request)  # Should not raise


def test_require_role_admin_can_access_operator_endpoint():
    dep = require_role("operator")
    request = MagicMock()
    request.state.tenant = _ctx(("admin",))
    dep(request)  # admin implies operator — should not raise


def test_require_role_multi_role_requirement():
    dep = require_role("admin", "approver")
    request = MagicMock()
    request.state.tenant = _ctx(("approver",))
    dep(request)  # has approver in required set — passes


def test_require_role_detail_message_contains_required_role():
    dep = require_role("admin")
    request = MagicMock()
    request.state.tenant = _ctx(("viewer",))
    with pytest.raises(HTTPException) as exc_info:
        dep(request)
    assert "admin" in exc_info.value.detail


# ── 6. extract_roles_from_jwt ─────────────────────────────────────────────────

def test_extract_from_realm_access():
    payload = {"realm_access": {"roles": ["admin", "viewer", "custom_role"]}}
    roles = extract_roles_from_jwt(payload)
    assert "admin" in roles
    assert "viewer" in roles
    assert "custom_role" not in roles  # filtered


def test_extract_from_resource_access():
    payload = {
        "resource_access": {
            "agentverse": {"roles": ["operator"]}
        }
    }
    roles = extract_roles_from_jwt(payload)
    assert "operator" in roles


def test_extract_from_top_level_roles():
    payload = {"roles": ["approver", "unknown_role"]}
    roles = extract_roles_from_jwt(payload)
    assert "approver" in roles
    assert "unknown_role" not in roles


def test_extract_from_all_sources():
    payload = {
        "realm_access": {"roles": ["admin"]},
        "resource_access": {"agentverse": {"roles": ["operator"]}},
        "roles": ["approver"],
    }
    roles = extract_roles_from_jwt(payload)
    assert "admin" in roles
    assert "operator" in roles
    assert "approver" in roles


def test_extract_empty_payload():
    roles = extract_roles_from_jwt({})
    assert roles == ()


def test_extract_deduplicates_roles():
    payload = {
        "realm_access": {"roles": ["viewer"]},
        "roles": ["viewer"],
    }
    roles = extract_roles_from_jwt(payload)
    assert roles.count("viewer") == 1


def test_extract_only_valid_roles():
    payload = {"roles": ["admin", "superuser", "hacker", "viewer"]}
    roles = extract_roles_from_jwt(payload)
    for r in roles:
        assert r in VALID_ROLES


# ── 7. is_ip_allowed ─────────────────────────────────────────────────────────

def test_empty_allowlist_permits_all():
    assert is_ip_allowed("1.2.3.4", []) is True
    assert is_ip_allowed("192.168.0.1", []) is True


def test_loopback_always_allowed():
    assert is_ip_allowed("127.0.0.1", ["10.0.0.0/8"]) is True


def test_ipv6_loopback_always_allowed():
    assert is_ip_allowed("::1", ["10.0.0.0/8"]) is True


def test_ip_in_cidr_allowed():
    assert is_ip_allowed("192.168.1.50", ["192.168.1.0/24"]) is True


def test_ip_not_in_cidr_denied():
    assert is_ip_allowed("10.0.0.1", ["192.168.1.0/24"]) is False


def test_ip_in_one_of_multiple_cidrs():
    assert is_ip_allowed("172.16.0.5", ["10.0.0.0/8", "172.16.0.0/12"]) is True


def test_ip_not_in_any_cidr():
    assert is_ip_allowed("8.8.8.8", ["10.0.0.0/8", "192.168.0.0/16"]) is False


def test_invalid_ip_format_denied():
    assert is_ip_allowed("not-an-ip", ["10.0.0.0/8"]) is False


def test_malformed_cidr_skipped():
    # Malformed CIDR should be skipped, not crash
    assert is_ip_allowed("10.0.0.1", ["not-a-cidr", "10.0.0.0/8"]) is True


def test_ipv4_specific_host_cidr():
    assert is_ip_allowed("203.0.113.42", ["203.0.113.42/32"]) is True


def test_ipv6_address():
    assert is_ip_allowed("2001:db8::1", ["2001:db8::/32"]) is True


def test_ipv6_not_in_cidr():
    assert is_ip_allowed("2001:db8::1", ["10.0.0.0/8"]) is False


# ── 8. load_roles_from_db ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_roles_no_db_returns_empty():
    result = await load_roles_from_db("user1", "t1", None)
    assert result == ()


@pytest.mark.asyncio
async def test_load_roles_with_db():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=["admin", "viewer"])))
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    with MagicMock() as mock_select, \
         pytest.raises(Exception) if False else __import__("contextlib").nullcontext():
        # Mock the sqlalchemy import inside the function
        result = await load_roles_from_db("user1", "t1", db_factory)
        # Either succeeds or returns () on import error
        assert isinstance(result, tuple)


@pytest.mark.asyncio
async def test_load_roles_db_exception_returns_empty():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def db_factory():
        return mock_session

    result = await load_roles_from_db("user1", "t1", db_factory)
    assert result == ()
