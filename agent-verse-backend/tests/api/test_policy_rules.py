"""Policy-as-code CRUD API — tenant-managed declarative rules (app.api.policy_rules)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.policy_rules import (
    PolicyRuleUpsert,
    create_policy_rule,
    delete_policy_rule,
    evaluate_rules_dry_run,
    list_policy_rules,
)
from app.tenancy.context import PlanTier, TenantContext


def _tenant(tenant_id="t1"):
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _fake_session(execute_side_effect=None):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=execute_side_effect or (lambda *a, **k: _FakeResult()))
    session.commit = AsyncMock()
    return session


def _request(*, tenant=None, db=None):
    request = MagicMock()
    request.state = SimpleNamespace(tenant=tenant)
    request.app.state = SimpleNamespace(db_session_factory=db)
    return request


# ── auth guard ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await list_policy_rules(_request(tenant=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_create_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await create_policy_rule(
            PolicyRuleUpsert(name="r1", rule_json={"conditions": []}), _request(tenant=None)
        )
    assert exc.value.status_code == 401


# ── list ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_no_db_returns_empty():
    result = await list_policy_rules(_request(tenant=_tenant(), db=None))
    assert result == []


@pytest.mark.asyncio
async def test_list_returns_mapped_rows():
    rows = [("id1", "rule-a", "desc", {"a": 1}, True, 1, "2026-01-01T00:00:00")]
    session = _fake_session(lambda *a, **k: _FakeResult(rows))

    def db():
        return session

    result = await list_policy_rules(_request(tenant=_tenant(), db=db))
    assert result == [
        {
            "id": "id1",
            "name": "rule-a",
            "description": "desc",
            "rule_json": {"a": 1},
            "is_active": True,
            "version": 1,
            "created_at": "2026-01-01T00:00:00",
        }
    ]


# ── create ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_no_db_returns_503():
    with pytest.raises(HTTPException) as exc:
        await create_policy_rule(
            PolicyRuleUpsert(name="r1", rule_json={"conditions": []}),
            _request(tenant=_tenant(), db=None),
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_create_inserts_and_commits():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO policy_rules" in sql:
            captured["params"] = params
        return _FakeResult()

    session = _fake_session(fake_execute)

    def db():
        return session

    body = PolicyRuleUpsert(name="my-rule", description="d", rule_json={"conditions": []})
    result = await create_policy_rule(body, _request(tenant=_tenant("t1"), db=db))

    assert result["name"] == "my-rule"
    assert result["status"] == "created"
    assert "id" in result
    assert captured["params"]["tid"] == "t1"
    assert captured["params"]["name"] == "my-rule"
    session.commit.assert_awaited_once()


# ── delete ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_no_db_returns_503():
    with pytest.raises(HTTPException) as exc:
        await delete_policy_rule("rid", _request(tenant=_tenant(), db=None))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_delete_requires_tenant():
    with pytest.raises(HTTPException) as exc:
        await delete_policy_rule("rid", _request(tenant=None))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_delete_executes_scoped_delete_and_commits():
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "DELETE FROM policy_rules" in sql:
            captured["params"] = params
        return _FakeResult()

    session = _fake_session(fake_execute)

    def db():
        return session

    await delete_policy_rule("rule-id-1", _request(tenant=_tenant("t1"), db=db))
    assert captured["params"] == {"id": "rule-id-1", "tid": "t1"}
    session.commit.assert_awaited_once()


# ── evaluate (dry-run) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_no_db_is_permissive():
    result = await evaluate_rules_dry_run(_request(tenant=_tenant(), db=None), {})
    assert result["allowed"] is True
    assert "No DB" in result["message"]


@pytest.mark.asyncio
async def test_evaluate_no_active_rules_allows():
    session = _fake_session(lambda *a, **k: _FakeResult([]))

    def db():
        return session

    result = await evaluate_rules_dry_run(
        _request(tenant=_tenant(), db=db), {"tool_name": "search"}
    )
    assert result["allowed"] is True


@pytest.mark.asyncio
async def test_evaluate_denies_on_matching_active_rule():
    rule_json = {
        "name": "block-delete",
        "conditions": [{"field": "tool_name", "op": "contains", "value": "delete"}],
        "logic": "AND",
        "action": "deny",
        "message": "destructive tool blocked",
    }
    rows = [(rule_json,)]
    session = _fake_session(lambda *a, **k: _FakeResult(rows))

    def db():
        return session

    result = await evaluate_rules_dry_run(
        _request(tenant=_tenant(), db=db), {"tool_name": "delete_account", "arguments": {}}
    )
    assert result["allowed"] is False
    assert result["rule_name"] == "block-delete"
    assert "destructive" in result["message"]


@pytest.mark.asyncio
async def test_evaluate_skips_null_rule_json_rows():
    rows = [(None,)]
    session = _fake_session(lambda *a, **k: _FakeResult(rows))

    def db():
        return session

    result = await evaluate_rules_dry_run(_request(tenant=_tenant(), db=db), {"tool_name": "x"})
    assert result["allowed"] is True
