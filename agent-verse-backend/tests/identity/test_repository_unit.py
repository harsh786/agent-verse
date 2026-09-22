"""PostgresIdentityStore (app.identity.repository) — unit tests against a
mocked SQLAlchemy AsyncSession. A real Postgres instance is unavailable in
this environment (see tests/identity/test_identity_repo_integration.py for
the full cross-channel integration test, which is skipped without Docker);
these tests exercise the row mapping and query composition directly.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.identity.models import IdentityLink, Principal
from app.identity.repository import PostgresIdentityStore

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, mapping_one=None, mapping_all=None):
        self._mapping_one = mapping_one
        self._mapping_all = mapping_all if mapping_all is not None else []

    def mappings(self):
        return self

    def one_or_none(self):
        return self._mapping_one

    def all(self):
        return self._mapping_all


def _fake_session_factory(execute_side_effect):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(side_effect=execute_side_effect)

    def factory():
        return session

    return factory


def _principal_row(p: Principal):
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "kind": p.kind,
        "display_name": p.display_name,
        "created_at": p.created_at,
    }


def _link_row(link: IdentityLink):
    return {
        "tenant_id": link.tenant_id,
        "channel": link.channel,
        "channel_user_id": link.channel_user_id,
        "principal_id": link.principal_id,
        "created_at": link.created_at,
    }


# ── get_principal ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_principal_returns_none_when_missing():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    assert await store.get_principal("no-such-id", "t1") is None


@pytest.mark.asyncio
async def test_get_principal_maps_row():
    p = Principal(id="p1", tenant_id="t1", kind="individual", display_name="Ann", created_at=_NOW)

    async def fake_execute(query, params=None):
        return _Result(mapping_one=_principal_row(p))

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    result = await store.get_principal("p1", "t1")
    assert result is not None
    assert result.id == "p1"
    assert result.display_name == "Ann"


# ── create_principal ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_principal_inserts_with_expected_params():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO principals" in sql:
            captured["params"] = params
        return _Result()

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    p = Principal(id="p2", tenant_id="t2", kind="individual", display_name="Bob", created_at=_NOW)
    await store.create_principal(p)

    assert captured["params"]["id"] == "p2"
    assert captured["params"]["t"] == "t2"
    assert captured["params"]["dn"] == "Bob"


# ── get_link ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_link_returns_none_when_missing():
    async def fake_execute(query, params=None):
        return _Result(mapping_one=None)

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    assert await store.get_link("t1", "whatsapp", "+1") is None


@pytest.mark.asyncio
async def test_get_link_maps_row():
    link = IdentityLink(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1", principal_id="p1",
        created_at=_NOW,
    )

    async def fake_execute(query, params=None):
        return _Result(mapping_one=_link_row(link))

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    result = await store.get_link("t1", "whatsapp", "+1")
    assert result is not None
    assert result.principal_id == "p1"


@pytest.mark.asyncio
async def test_get_link_defaults_created_at_when_null():
    """A NULL created_at column must not raise — falls back to datetime.now()."""
    row = {
        "tenant_id": "t1",
        "channel": "web",
        "channel_user_id": "u1",
        "principal_id": "p1",
        "created_at": None,
    }

    async def fake_execute(query, params=None):
        return _Result(mapping_one=row)

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    result = await store.get_link("t1", "web", "u1")
    assert result is not None
    assert isinstance(result.created_at, datetime)


# ── create_link ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_link_inserts_with_expected_params():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO identity_links" in sql:
            captured["params"] = params
        return _Result()

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    link = IdentityLink(
        tenant_id="t1", channel="sms", channel_user_id="+15551234", principal_id="p9",
        created_at=_NOW,
    )
    await store.create_link(link)

    assert captured["params"]["t"] == "t1"
    assert captured["params"]["c"] == "sms"
    assert captured["params"]["u"] == "+15551234"
    assert captured["params"]["p"] == "p9"


# ── links_for_principal ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_links_for_principal_returns_ordered_links():
    link1 = IdentityLink(
        tenant_id="t1", channel="web", channel_user_id="u1", principal_id="p1", created_at=_NOW
    )
    link2 = IdentityLink(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1", principal_id="p1",
        created_at=_NOW,
    )

    async def fake_execute(query, params=None):
        return _Result(mapping_all=[_link_row(link1), _link_row(link2)])

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    results = await store.links_for_principal("p1", "t1")
    assert [(r.channel, r.channel_user_id) for r in results] == [
        ("web", "u1"), ("whatsapp", "+1"),
    ]


@pytest.mark.asyncio
async def test_links_for_principal_empty_returns_empty_list():
    async def fake_execute(query, params=None):
        return _Result(mapping_all=[])

    store = PostgresIdentityStore(_fake_session_factory(fake_execute))
    assert await store.links_for_principal("p1", "t1") == []
