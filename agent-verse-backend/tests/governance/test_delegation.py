"""Grantex G4 — delegation can only narrow authority."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.governance.grants import Grant, mint_delegation
from app.governance.grants.delegation import DelegationError

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _parent(**over) -> Grant:
    base = dict(
        grant_id="parent",
        tenant_id="t1",
        grantor="user:alice",
        grantee_agent_id="supervisor",
        scopes=("jira.*", "github.read"),
        not_before=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(hours=2),
        max_cost_usd=10.0,
    )
    base.update(over)
    return Grant(**base)  # type: ignore[arg-type]


def test_narrowing_delegation_is_allowed() -> None:
    child = mint_delegation(
        _parent(),
        grant_id="child",
        grantee_agent_id="sub-1",
        scopes=("jira.search",),
        expires_at=_NOW + timedelta(hours=1),
        max_cost_usd=2.0,
        now=_NOW,
    )
    assert child.parent_grant_id == "parent"
    assert child.covers("jira.search", _NOW) is True
    assert child.covers("jira.delete", _NOW) is False  # narrowed out


def test_widening_scope_is_rejected() -> None:
    with pytest.raises(DelegationError):
        mint_delegation(
            _parent(),
            grant_id="child",
            grantee_agent_id="sub-1",
            scopes=("stripe.charge",),  # not within parent
            expires_at=_NOW + timedelta(hours=1),
            now=_NOW,
        )


def test_extending_expiry_is_rejected() -> None:
    with pytest.raises(DelegationError):
        mint_delegation(
            _parent(),
            grant_id="child",
            grantee_agent_id="sub-1",
            scopes=("jira.search",),
            expires_at=_NOW + timedelta(hours=5),  # beyond parent
            now=_NOW,
        )


def test_raising_cost_cap_is_rejected() -> None:
    with pytest.raises(DelegationError):
        mint_delegation(
            _parent(),
            grant_id="child",
            grantee_agent_id="sub-1",
            scopes=("jira.search",),
            expires_at=_NOW + timedelta(hours=1),
            max_cost_usd=50.0,  # exceeds parent's 10
            now=_NOW,
        )
    # uncapped child under a capped parent is also a widening
    with pytest.raises(DelegationError):
        mint_delegation(
            _parent(),
            grant_id="child",
            grantee_agent_id="sub-1",
            scopes=("jira.search",),
            expires_at=_NOW + timedelta(hours=1),
            max_cost_usd=None,
            now=_NOW,
        )


def test_cannot_delegate_from_inactive_parent() -> None:
    with pytest.raises(DelegationError):
        mint_delegation(
            _parent(revoked=True),
            grant_id="child",
            grantee_agent_id="sub-1",
            scopes=("jira.search",),
            expires_at=_NOW + timedelta(hours=1),
            now=_NOW,
        )


async def test_delegate_active_grants_narrows_and_issues_to_child() -> None:
    from app.governance.grants import InMemoryGrantStore, delegate_active_grants

    store = InMemoryGrantStore()
    await store.issue(_parent())
    minted = await delegate_active_grants(
        store,
        tenant_id="t1",
        parent_agent_id="supervisor",
        child_agent_id="sub-1",
        now=_NOW,
    )
    assert len(minted) == 1
    child = minted[0]
    assert child.grantee_agent_id == "sub-1"
    assert child.parent_grant_id == "parent"
    assert child.expires_at <= _parent().expires_at
    # child is enforceable from the store
    got = await store.list_for_agent("t1", "sub-1")
    assert len(got) == 1 and got[0].covers("jira.search", _NOW)


async def test_delegate_skips_when_parent_has_no_active_grant() -> None:
    from app.governance.grants import InMemoryGrantStore, delegate_active_grants

    store = InMemoryGrantStore()
    await store.issue(_parent(revoked=True))
    minted = await delegate_active_grants(
        store, tenant_id="t1", parent_agent_id="supervisor", child_agent_id="sub-1", now=_NOW
    )
    assert minted == []
