"""Behavioral tests for the skills CRUD API endpoint functions
(app.api.skills) — list/create/delete against platform + tenant-custom
skills, including DB-unavailable and DB-error fallback paths.

Static-shape tests for the router already live in tests/api/test_skills_api.py;
this file exercises the actual endpoint coroutines.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agent.skill_selector import PLATFORM_SKILLS
from app.api.skills import SkillCreateRequest, create_skill, delete_skill, list_skills


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


def _fake_session(execute_side_effect=None):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(
        side_effect=execute_side_effect or (lambda *a, **k: _FakeResult())
    )
    return session


def _request(*, tenant=None, db=None):
    request = MagicMock()
    request.state = SimpleNamespace(tenant=tenant)
    request.app.state = SimpleNamespace(db_session_factory=db)
    return request


def _tenant(tenant_id="t1", api_key_id="k1"):
    return SimpleNamespace(tenant_id=tenant_id, api_key_id=api_key_id)


# ── list_skills ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_skills_no_db_returns_platform_only():
    result = await list_skills(_request(tenant=None, db=None))
    assert result["total"] == len(PLATFORM_SKILLS)
    assert all(s["is_platform"] for s in result["skills"])


@pytest.mark.asyncio
async def test_list_skills_no_tenant_skips_db_even_if_present():
    session = _fake_session()

    def db():
        return session

    result = await list_skills(_request(tenant=None, db=db))
    assert result["total"] == len(PLATFORM_SKILLS)
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_skills_merges_tenant_rows():
    row = (
        "sk1", "custom-skill", "desc", ["hint1"], "do stuff",
        ["tool1"], 150, "tenant",
    )

    async def fake_execute(*a, **k):
        return _FakeResult([row])

    session = _fake_session(fake_execute)

    def db():
        return session

    result = await list_skills(_request(tenant=_tenant(), db=db))
    assert result["total"] == len(PLATFORM_SKILLS) + 1
    custom = next(s for s in result["skills"] if s["id"] == "sk1")
    assert custom["name"] == "custom-skill"
    assert custom["is_platform"] is False
    assert custom["trigger_hints"] == ["hint1"]


@pytest.mark.asyncio
async def test_list_skills_parses_json_string_columns():
    """trigger_hints/allowed_tools may come back as JSON strings, not lists."""
    row = (
        "sk2", "custom2", None, '["a", "b"]', None,
        '["tool-x"]', None, None,
    )

    async def fake_execute(*a, **k):
        return _FakeResult([row])

    session = _fake_session(fake_execute)

    def db():
        return session

    result = await list_skills(_request(tenant=_tenant(), db=db))
    custom = next(s for s in result["skills"] if s["id"] == "sk2")
    assert custom["trigger_hints"] == ["a", "b"]
    assert custom["allowed_tools"] == ["tool-x"]
    assert custom["token_estimate"] == 100
    assert custom["visibility"] == "tenant"


@pytest.mark.asyncio
async def test_list_skills_db_error_falls_back_to_platform_only():
    session = _fake_session(RuntimeError("db down"))

    def db():
        return session

    result = await list_skills(_request(tenant=_tenant(), db=db))
    assert result["total"] == len(PLATFORM_SKILLS)


# ── create_skill ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_skill_requires_tenant():
    body = SkillCreateRequest(
        name="new-skill", description="d", trigger_hints=["h"], instructions="i"
    )
    with pytest.raises(HTTPException) as exc:
        await create_skill(body, _request(tenant=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_create_skill_name_conflicts_with_platform_skill():
    conflicting_name = PLATFORM_SKILLS[0]["name"]
    body = SkillCreateRequest(
        name=conflicting_name, description="d", trigger_hints=["h"], instructions="i"
    )
    with pytest.raises(HTTPException) as exc:
        await create_skill(body, _request(tenant=_tenant()))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_skill_no_db_returns_created_without_persisting():
    body = SkillCreateRequest(
        name="ephemeral-skill", description="d", trigger_hints=["h"], instructions="i"
    )
    result = await create_skill(body, _request(tenant=_tenant(), db=None))
    assert result["name"] == "ephemeral-skill"
    assert result["status"] == "created"
    assert "id" in result


@pytest.mark.asyncio
async def test_create_skill_persists_to_db():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO skills" in sql:
            captured["params"] = params
        return _FakeResult()

    session = _fake_session(fake_execute)

    def db():
        return session

    body = SkillCreateRequest(
        name="persisted-skill",
        description="d",
        trigger_hints=["h1", "h2"],
        instructions="do the thing",
        allowed_tools=["tool-a"],
        token_estimate=250,
        visibility="marketplace",
    )
    result = await create_skill(body, _request(tenant=_tenant("t1", "k1"), db=db))

    assert result["status"] == "created"
    assert captured["params"]["tid"] == "t1"
    assert captured["params"]["name"] == "persisted-skill"
    assert captured["params"]["creator"] == "k1"
    assert captured["params"]["vis"] == "marketplace"


@pytest.mark.asyncio
async def test_create_skill_db_error_raises_503():
    session = _fake_session(RuntimeError("db unavailable"))

    def db():
        return session

    body = SkillCreateRequest(
        name="failing-skill", description="d", trigger_hints=["h"], instructions="i"
    )
    with pytest.raises(HTTPException) as exc:
        await create_skill(body, _request(tenant=_tenant(), db=db))
    assert exc.value.status_code == 503


# ── delete_skill ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_skill_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await delete_skill("sk1", _request(tenant=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_delete_skill_cannot_delete_platform_skill():
    platform_id = PLATFORM_SKILLS[0]["id"]
    with pytest.raises(HTTPException) as exc:
        await delete_skill(platform_id, _request(tenant=_tenant()))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_skill_no_db_still_returns_deleted():
    result = await delete_skill("custom-sk", _request(tenant=_tenant(), db=None))
    assert result == {"id": "custom-sk", "status": "deleted"}


@pytest.mark.asyncio
async def test_delete_skill_soft_deletes_in_db():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "UPDATE skills" in sql:
            captured["params"] = params
        return _FakeResult()

    session = _fake_session(fake_execute)

    def db():
        return session

    result = await delete_skill("custom-sk", _request(tenant=_tenant("t1"), db=db))
    assert result["status"] == "deleted"
    assert captured["params"] == {"id": "custom-sk", "tid": "t1"}


@pytest.mark.asyncio
async def test_delete_skill_db_error_is_swallowed():
    session = _fake_session(RuntimeError("db down"))

    def db():
        return session

    result = await delete_skill("custom-sk", _request(tenant=_tenant(), db=db))
    assert result == {"id": "custom-sk", "status": "deleted"}
