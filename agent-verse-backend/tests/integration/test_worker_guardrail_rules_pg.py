"""P8-1 against real Postgres: a tenant PII rule created through the API redacts on a worker.

The rule is created with ``POST /guardrails`` on an "API" engine bound to the
Postgres rule store. A separate "worker" engine — a fresh process singleton,
as in a Celery worker — is bound by the worker binding (lazy, per-task session
factory) and must load and apply that rule. Runs as a least-privilege
(NOSUPERUSER, NOBYPASSRLS) role so RLS binds.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.models import GuardrailLayer
from app.guardrails_v2.repository import PostgresGuardrailRuleRepository
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

pytestmark = pytest.mark.integration

_T = f"t-wpii-{secrets.token_hex(3)}"
_KEY = "ak_worker_pii_pg"
EMAIL = "ravi.menon@bramblewood-freight.example"
CARD = f"Ravi Menon, email {EMAIL}, mobile +91 98450 12345."
_TABLES = "guardrail_rules, guardrail_violations"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    role = f"app_wpii_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)

    async def _run(*stmts: str) -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            for stmt in stmts:
                await conn.execute(stmt)
        finally:
            await conn.close()

    asyncio.run(_run(
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLES} TO {role}",
    ))
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(_run(f"REVOKE ALL ON {_TABLES} FROM {role}", f"DROP ROLE {role}"))


def _factory(url: str) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


def _api_create_rule(monkeypatch: pytest.MonkeyPatch, url: str) -> str:
    from app.api.guardrails import router

    api_engine = GuardrailsEngine()
    api_engine.bind_repository(PostgresGuardrailRuleRepository(_factory(url)))
    monkeypatch.setattr(engine_mod, "guardrails_engine", api_engine)
    ctx = TenantContext(tenant_id=_T, plan=PlanTier.ENTERPRISE, api_key_id="k")
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    resp = TestClient(app).post("/guardrails", headers={"X-API-Key": _KEY}, json={
        "name": "rw-pii-output", "layers": ["final", "output"], "rule_type": "pii",
        "config": {"entities": ["email", "phone"]}, "severity": "high", "action": "redact"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["durable"] is True
    return str(resp.json()["id"])


def test_tenant_pii_rule_from_the_api_redacts_on_a_worker(
    app_role_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.guardrails_v2.worker_binding import bind_worker_guardrail_rules
    from tests._test_backends import reset_db_singletons

    rule_id = _api_create_rule(monkeypatch, app_role_url)

    # A fresh worker process: its engine has no repository until bound.
    worker = GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", worker)
    monkeypatch.setenv("DATABASE_URL", app_role_url)
    reset_db_singletons()

    async def _on_worker() -> tuple[dict[str, Any], dict[str, Any], list[Any]]:
        import app.db.session as db_session

        try:
            unbound = await worker.evaluate(CARD, GuardrailLayer.TOOL_OUTPUT, _T, goal_id="g")
            assert bind_worker_guardrail_rules() is True
            bound = await worker.evaluate(CARD, GuardrailLayer.TOOL_OUTPUT, _T, goal_id="g-pii")
            recorded = await worker.aget_violations(_T, limit=10)
        finally:
            if db_session._engine is not None:
                await db_session._engine.dispose()
        return unbound, bound, recorded

    try:
        unbound, bound, recorded = asyncio.run(_on_worker())
    finally:
        reset_db_singletons()

    # Before the binding the tenant's rule did not exist on the worker.
    assert unbound["violation_count"] == 0 and unbound["redacted_content"] == CARD
    # Bound: the rule written through the API applies, and the violation is durable.
    assert bound["blocked"] is False
    assert [v["rule_name"] for v in bound["violations"]] == ["rw-pii-output"]
    text = str(bound["redacted_content"])
    assert EMAIL not in text and "98450" not in text
    assert any(v.goal_id == "g-pii" and v.rule_id == rule_id for v in recorded)
