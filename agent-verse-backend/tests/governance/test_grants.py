"""Grantex G2/G3 — grants (scope/TTL/revocation) + enforcement decision."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.governance.grants import (
    Grant,
    InMemoryGrantStore,
    check_grant,
    scope_matches,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _grant(**over) -> Grant:
    base = dict(
        grant_id="g1",
        tenant_id="t1",
        grantor="user:alice",
        grantee_agent_id="agent-1",
        scopes=("jira.*",),
        not_before=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(hours=1),
    )
    base.update(over)
    return Grant(**base)  # type: ignore[arg-type]


def test_scope_matching_globs() -> None:
    assert scope_matches(("jira.*",), "jira.search") is True
    assert scope_matches(("jira.*",), "github.search") is False
    assert scope_matches(("*",), "anything.at_all") is True
    assert scope_matches(("jira.search",), "jira.create") is False


def test_grant_covers_within_window_and_scope() -> None:
    g = _grant()
    assert g.covers("jira.search", _NOW) is True
    assert g.covers("github.search", _NOW) is False


def test_expired_and_revoked_grants_do_not_cover() -> None:
    assert _grant(expires_at=_NOW - timedelta(minutes=1)).covers("jira.search", _NOW) is False
    assert _grant(not_before=_NOW + timedelta(minutes=1)).covers("jira.search", _NOW) is False
    assert _grant(revoked=True).covers("jira.search", _NOW) is False


def test_cost_cap_enforced() -> None:
    g = _grant(max_cost_usd=5.0)
    assert g.covers("jira.search", _NOW, cost_usd=4.0) is True
    assert g.covers("jira.search", _NOW, cost_usd=6.0) is False


async def test_enforcer_allows_covered_and_denies_out_of_scope() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant())
    allow = await check_grant(
        store, tenant_id="t1", agent_id="agent-1", tool_name="jira.search", now=_NOW
    )
    assert allow.allowed is True and allow.grant_id == "g1"
    deny = await check_grant(
        store, tenant_id="t1", agent_id="agent-1", tool_name="stripe.charge", now=_NOW
    )
    assert deny.allowed is False and deny.reason == "tool_out_of_scope"


async def test_enforcer_fail_closed_without_grant() -> None:
    store = InMemoryGrantStore()
    d = await check_grant(
        store, tenant_id="t1", agent_id="ghost", tool_name="jira.search", now=_NOW
    )
    assert d.allowed is False and d.reason == "no_grant_for_agent"
    # opt-in easing: require_grant=False allows when no grants exist yet
    d2 = await check_grant(
        store, tenant_id="t1", agent_id="ghost", tool_name="jira.search",
        now=_NOW, require_grant=False,
    )
    assert d2.allowed is True


async def test_enforcer_revocation_takes_effect() -> None:
    store = InMemoryGrantStore()
    await store.issue(_grant())
    await store.revoke("t1", "g1")
    d = await check_grant(
        store, tenant_id="t1", agent_id="agent-1", tool_name="jira.search", now=_NOW
    )
    assert d.allowed is False and d.reason == "all_grants_expired_or_revoked"


async def test_enforcer_noops_without_store() -> None:
    d = await check_grant(None, tenant_id="t1", agent_id="a", tool_name="x", now=_NOW)
    assert d.allowed is True and d.reason == "grants_not_configured"
