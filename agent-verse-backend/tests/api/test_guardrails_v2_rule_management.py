"""Guardrails v2 rule management: admin-only writes (GRD-05), durable create (GRD-02),
update/disable/delete (GRD-01)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.guardrails_v2 import router as g2_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_TID = "tid-grd-mgmt"
_KEYS = {
    "ak_grd_admin": ("admin",),
    "ak_grd_operator": ("operator",),
    "ak_grd_viewer": ("viewer",),
}


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.guardrails_v2 import engine as engine_mod

    fresh = engine_mod.GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        roles = _KEYS.get(key)
        if roles is None:
            return None
        return TenantContext(
            tenant_id=_TID, plan=PlanTier.PROFESSIONAL, api_key_id=key, roles=roles
        )

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(g2_router)
    with TestClient(app) as c:
        c.engine = fresh  # type: ignore[attr-defined]
        yield c


_RULE = {"name": "Block everything", "rule_type": "regex", "config": {"pattern": ".*"}}


@pytest.mark.parametrize("key", ["ak_grd_operator", "ak_grd_viewer"])
def test_rule_and_bundle_writes_require_admin(client: Any, key: str) -> None:
    """GRD-05: any write-capable key could add a tenant-wide BLOCK rule ('.*')."""
    assert client.post("/guardrails-v2/rules", json=_RULE, headers=_headers(key)).status_code == 403
    assert client.post("/guardrails-v2/bundles/soc2", headers=_headers(key)).status_code == 403
    assert client.engine.get_rules(_TID) == []


def test_admin_can_write_rules(client: Any) -> None:
    resp = client.post("/guardrails-v2/rules", json=_RULE, headers=_headers("ak_grd_admin"))
    assert resp.status_code == 200, resp.text
    # Reads stay open to every authenticated role.
    assert client.get("/guardrails-v2/rules", headers=_headers("ak_grd_viewer")).status_code == 200


class _Repo:
    """Minimal repository double: a dict, optionally failing every write."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: dict[str, Any] = {}
        self.fail = fail

    async def upsert(self, rule: Any) -> None:
        if self.fail:
            raise ConnectionError("db down")
        self.rows[rule.rule_id] = rule

    async def insert_if_absent(self, rule: Any) -> None:
        self.rows.setdefault(rule.rule_id, rule)

    async def delete(self, tenant_id: str, rule_id: str) -> None:
        if self.fail:
            raise ConnectionError("db down")
        self.rows.pop(rule_id, None)

    async def load(self, tenant_id: str) -> list[Any]:
        return [r for r in self.rows.values() if r.tenant_id == tenant_id]


ADMIN = _headers("ak_grd_admin")


def test_create_is_persisted_before_it_answers_created(client: Any) -> None:
    """GRD-02: 'created' used to precede a best-effort background flush."""
    repo = _Repo()
    client.engine.bind_repository(repo)
    resp = client.post("/guardrails-v2/rules", json=_RULE, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json()["durable"] is True
    assert resp.json()["rule_id"] in repo.rows


def test_create_that_cannot_be_saved_is_a_503_and_changes_nothing(client: Any) -> None:
    client.engine.bind_repository(_Repo(fail=True))
    resp = client.post("/guardrails-v2/rules", json=_RULE, headers=ADMIN)
    assert resp.status_code == 503
    assert client.engine.all_rules(_TID) == []


def test_update_disable_and_delete_rules(client: Any) -> None:
    """GRD-01: there were no update/delete/disable routes for v2 rules."""
    repo = _Repo()
    client.engine.bind_repository(repo)
    rid = client.post("/guardrails-v2/rules", json=_RULE, headers=ADMIN).json()["rule_id"]

    resp = client.patch(f"/guardrails-v2/rules/{rid}", json={"enabled": False}, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["rule"]["enabled"] is False
    assert resp.json()["rule"]["version"] == 2
    assert repo.rows[rid].enabled is False
    listed = client.get("/guardrails-v2/rules", headers=ADMIN).json()["rules"]
    assert [r["enabled"] for r in listed if r["rule_id"] == rid] == [False]  # still listed

    assert (
        client.patch(f"/guardrails-v2/rules/{rid}", json={"action": "bogus"}, headers=ADMIN)
    ).status_code == 400
    assert (
        client.patch(
            f"/guardrails-v2/rules/{rid}",
            json={"enabled": True},
            headers=_headers("ak_grd_operator"),
        )
    ).status_code == 403

    assert client.delete(f"/guardrails-v2/rules/{rid}", headers=ADMIN).status_code == 200
    assert rid not in repo.rows
    assert client.delete(f"/guardrails-v2/rules/{rid}", headers=ADMIN).status_code == 404


def test_baseline_rules_can_be_disabled_but_not_deleted(client: Any) -> None:
    client.engine.bind_repository(_Repo())
    client.engine.ensure_default_rules(_TID)
    rid = f"gr-default:{_TID}:secret-regex"
    assert client.delete(f"/guardrails-v2/rules/{rid}", headers=ADMIN).status_code == 409
    resp = client.patch(f"/guardrails-v2/rules/{rid}", json={"enabled": False}, headers=ADMIN)
    assert resp.status_code == 200


async def test_a_rule_deleted_on_another_replica_disappears_on_refresh() -> None:
    from app.guardrails_v2.engine import GuardrailsEngine
    from app.guardrails_v2.models import GuardrailRule

    repo = _Repo()
    replica_a = GuardrailsEngine(rule_refresh_s=0.0)
    replica_b = GuardrailsEngine(rule_refresh_s=0.0)
    replica_a.bind_repository(repo)
    replica_b.bind_repository(repo)
    rule = GuardrailRule(rule_id="r-x", tenant_id=_TID, name="x", rule_type="regex")
    await replica_a.add_rule_durable(rule)
    assert [r.rule_id for r in await replica_b.aget_rules(_TID)] == ["r-x"]
    assert await replica_a.delete_rule_durable(_TID, "r-x") is True
    assert await replica_b.aget_rules(_TID) == []


@pytest.mark.integration
async def test_update_and_delete_against_postgres_under_app_role(pg_url: str) -> None:
    from app.guardrails_v2.engine import GuardrailsEngine
    from app.guardrails_v2.models import GuardrailRule
    from app.guardrails_v2.repository import PostgresGuardrailRuleRepository
    from tests.memory._pg import app_role_engine, sessionmaker_for

    engine = await app_role_engine(pg_url, ["guardrail_rules"])
    try:
        repo = PostgresGuardrailRuleRepository(sessionmaker_for(engine))
        a, b = GuardrailsEngine(rule_refresh_s=0.0), GuardrailsEngine(rule_refresh_s=0.0)
        a.bind_repository(repo)
        b.bind_repository(repo)
        tid = "tid-grd-pg"
        await a.add_rule_durable(
            GuardrailRule(rule_id="pg-r", tenant_id=tid, name="n", rule_type="regex")
        )
        await a.update_rule_durable(tid, "pg-r", enabled=False)
        [loaded] = await repo.load(tid)
        assert loaded.enabled is False and loaded.version == 2
        assert await a.delete_rule_durable(tid, "pg-r") is True
        assert await repo.load(tid) == []
        assert b.all_rules(tid) == [] and await b.aget_rules(tid) == []
    finally:
        await engine.dispose()
