"""RV-07 (integration): at fire time the Celery task re-reads the creating API key
from Postgres under the tenant's RLS (least-privilege NOBYPASSRLS role) and runs
the goal as it; a revoked key or one whose scopes no longer grant goals:write is
skipped (intention ``failed``) and the denial lands in ``audit_log``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_prospective_principal_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine

pytestmark = pytest.mark.integration

TENANT = "pm-principal-tenant"
OTHER = "pm-principal-other"
KEYS = {
    # key id: (tenant, is_active, roles, scopes)
    "key-active": (TENANT, True, '["operator"]', []),
    "key-revoked": (TENANT, False, '["operator"]', []),
    "key-narrowed": (TENANT, True, '["operator"]', []),
    "key-viewer": (TENANT, True, '["viewer"]', []),
    "key-scoped": (TENANT, True, '["operator"]', ["memory:write"]),
    "key-other-tenant": (OTHER, True, '["operator"]', []),
}


@pytest.fixture(scope="module")
def urls() -> Iterator[tuple[str, str]]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin = pg.get_connection_url()
        alembic_upgrade(admin)
        loop = asyncio.new_event_loop()
        engine = loop.run_until_complete(
            app_role_engine(
                admin,
                [
                    "prospective_memory",
                    "goals",
                    "api_keys",
                    "api_key_scopes",
                    "role_assignments",
                    "custom_roles",
                    "audit_log",
                ],
            )
        )
        app_url = engine.url.render_as_string(hide_password=False)
        loop.run_until_complete(engine.dispose())

        async def _seed() -> None:
            eng = create_async_engine(admin, poolclass=NullPool)
            async with eng.begin() as c:
                for t in (TENANT, OTHER):
                    await c.execute(
                        text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                        {"t": t, "e": f"{t}@example.test"},
                    )
                for kid, (tid, active, roles, scopes) in KEYS.items():
                    await c.execute(
                        text(
                            "INSERT INTO api_keys (id, tenant_id, name, key_hash, roles, scopes, "
                            "is_active) VALUES (:id, :tid, :id, :h, CAST(:roles AS jsonb), "
                            ":scopes, :active)"
                        ),
                        {
                            "id": kid,
                            "tid": tid,
                            "h": hashlib.sha256(kid.encode()).hexdigest(),
                            "roles": roles,
                            "scopes": scopes,
                            "active": active,
                        },
                    )
                # Explicit api_key_scopes rows REPLACE the role fallback: this key
                # was narrowed to memory-only after the intention was scheduled.
                await c.execute(
                    text(
                        "INSERT INTO api_key_scopes (api_key_id, tenant_id, scope) "
                        "VALUES ('key-narrowed', :t, 'memory:write')"
                    ),
                    {"t": TENANT},
                )
            await eng.dispose()

        loop.run_until_complete(_seed())
        loop.close()
        yield admin, app_url


def _factory(url: str) -> Any:
    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


class _GoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"goal_id": f"goal-{len(self.calls)}"}


def test_fire_rechecks_principal_against_postgres(
    urls: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.memory.prospective_auth import IntentionPrincipal
    from app.memory.prospective_postgres import PostgresProspectiveMemoryService
    from app.memory.prospective_runtime import create_intention
    from app.scaling import tasks

    admin_url, app_url = urls

    async def _allow(**_kw: Any) -> dict[str, Any]:
        return {"blocked": False}

    async def _seed() -> dict[str, str]:
        svc = PostgresProspectiveMemoryService(_factory(app_url))
        now = datetime.now(UTC)
        ids: dict[str, str] = {}
        with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow):
            for kid in (*KEYS, None):
                item = await create_intention(
                    svc,
                    tenant_id=TENANT,
                    intention=f"intention of {kid}",
                    due_at=now - timedelta(minutes=1),
                    now=now - timedelta(minutes=2),
                    principal=IntentionPrincipal(kid, ("operator",), ()) if kid else None,
                )
                ids[str(kid)] = item.memory_id
        return ids

    ids = asyncio.run(_seed())
    goal_svc = _GoalService()
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: _factory(admin_url))
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _factory(app_url))
    monkeypatch.setattr(tasks, "_build_worker_goal_service", lambda: (goal_svc, None))

    result = tasks.process_due_prospective_memories.run()

    assert result["fired"] == 1, result
    (call,) = goal_svc.calls
    ctx = call["tenant_ctx"]
    assert ctx.api_key_id == "key-active" and ctx.roles == ("operator",)
    assert call["goal"] == "Deferred intention: intention of key-active"
    assert call["execution_context"]["submitted_by_api_key_id"] == "key-active"

    async def _states() -> tuple[dict[str, Any], list[Any]]:
        svc = PostgresProspectiveMemoryService(_factory(app_url))
        states = {k: await svc.get(TENANT, mid) for k, mid in ids.items()}
        eng = create_async_engine(admin_url, poolclass=NullPool)
        async with eng.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT step_id, api_key_id, outcome, note FROM audit_log "
                        "WHERE tenant_id = :t AND tool_name = 'prospective_memory.fire'"
                    ),
                    {"t": TENANT},
                )
            ).fetchall()
        await eng.dispose()
        return states, list(rows)

    states, audit = asyncio.run(_states())
    assert states["key-active"].state == "completed"
    denied_keys = (
        "key-revoked",
        "key-narrowed",
        "key-viewer",
        "key-scoped",
        "key-other-tenant",
        "None",
    )
    for denied in denied_keys:
        assert states[denied].state == "failed", denied
        assert "not authorized" in (states[denied].result or {}).get("error", ""), denied
    audited = {r[0]: r for r in audit}
    assert set(audited) == {ids[k] for k in denied_keys}
    assert all(r[2] == "denied" for r in audit)
    assert audited[ids["key-revoked"]][1] == "key-revoked"

    # Nothing re-fires: the denials are terminal.
    assert tasks.process_due_prospective_memories.run()["fired"] == 0
    assert len(goal_svc.calls) == 1
