"""MEM-16 (integration): a due intention stored in Postgres is fired by the
Celery task (tenant scan on the maintenance factory, lease + complete under the
tenant's RLS on the app role) and marked done with the submitted goal id.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_prospective_fire_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine

pytestmark = pytest.mark.integration

TENANT = "pm-fire-tenant"


@pytest.fixture(scope="module")
def urls() -> Iterator[tuple[str, str]]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin = pg.get_connection_url()
        alembic_upgrade(admin)
        loop = asyncio.new_event_loop()
        engine = loop.run_until_complete(app_role_engine(admin, ["prospective_memory"]))
        app_url = engine.url.render_as_string(hide_password=False)
        loop.run_until_complete(engine.dispose())

        async def _tenant() -> None:
            eng = create_async_engine(admin, poolclass=NullPool)
            async with eng.begin() as c:
                await c.execute(
                    text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                    {"t": TENANT, "e": f"{TENANT}@example.test"},
                )
            await eng.dispose()

        loop.run_until_complete(_tenant())
        loop.close()
        yield admin, app_url


def test_due_intention_fires_once_and_is_completed(
    urls: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.memory.prospective_postgres import PostgresProspectiveMemoryService
    from app.memory.prospective_runtime import create_intention
    from app.scaling import tasks

    admin_url, app_url = urls

    def _factory(url: str) -> Any:
        return async_sessionmaker(create_async_engine(url, poolclass=NullPool),
                                  expire_on_commit=False)

    async def _allow(**_kw: Any) -> dict[str, Any]:
        return {"blocked": False}

    async def _seed() -> str:
        from unittest.mock import patch

        now = datetime.now(UTC)
        with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow):
            item = await create_intention(
                PostgresProspectiveMemoryService(_factory(app_url)),
                tenant_id=TENANT,
                intention="post the weekly KPI digest",
                due_at=now - timedelta(minutes=1),
                now=now - timedelta(minutes=2),
            )
        return item.memory_id

    memory_id = asyncio.run(_seed())
    submitted: list[tuple[str, str]] = []

    async def _submit(item: Any, plan: str) -> dict[str, Any]:
        submitted.append((item.intention, plan))
        return {"goal_id": "goal-from-intention"}

    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: _factory(admin_url))
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _factory(app_url))
    monkeypatch.setattr(tasks, "_submit_intention_as_goal", _submit)

    first = tasks.process_due_prospective_memories.run()
    second = tasks.process_due_prospective_memories.run()

    assert first["fired"] == 1 and second["fired"] == 0
    assert submitted == [("post the weekly KPI digest", "free")]

    async def _state() -> Any:
        return await PostgresProspectiveMemoryService(_factory(app_url)).get(TENANT, memory_id)

    done = asyncio.run(_state())
    assert done.state == "completed" and done.result == {"goal_id": "goal-from-intention"}
