"""a10-F230-01/02 on real Postgres, as the least-privilege (NOBYPASSRLS) app role.

* deleting a seeded built-in records a ``goal_template_tombstones`` row in the
  same transaction; a fresh store (a restart / another replica) seeds again and
  the deleted built-in does not come back, the others are untouched;
* the list is paged in SQL and ``search`` matches before the page is cut (``%``
  and ``_`` in the search are literals).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.api.templates as tmpl
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration

_PACK = [
    {"name": f"PG Pack {i}", "description": "d", "goal_text": f"Run step {i}", "domain": "ops"}
    for i in range(4)
] + [{"name": "Rate 100%", "description": "d", "goal_text": "x", "domain": "ops"}]


async def test_deleted_builtin_stays_deleted_across_restarts(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmpl, "_content_cache", {"templates": list(_PACK), "failed_at": None})
    tenant = f"tmpl-tomb-{uuid.uuid4().hex[:8]}"
    app_eng = await app_role_engine(pg_url, ["goal_templates", "goal_template_tombstones"])
    db = sessionmaker_for(app_eng)
    admin = create_async_engine(pg_url)
    try:
        store = tmpl._TemplateStore()
        store.set_db(db)
        listed = await store.list(tenant)
        assert {t["name"] for t in listed} == {t["name"] for t in _PACK}
        victim = next(t for t in listed if t["name"] == "PG Pack 1")
        custom = await store.create(tenant, "Mine", "", "custom goal", "general", [])
        assert await store.delete(tenant, victim["id"]) is True
        assert await store.delete(tenant, custom["id"]) is True

        async with admin.begin() as c:
            tombstones = (
                await c.execute(
                    text("SELECT template_id FROM goal_template_tombstones WHERE tenant_id = :t"),
                    {"t": tenant},
                )
            ).scalars().all()
        assert list(tombstones) == [victim["id"]]  # only the built-in

        restarted = tmpl._TemplateStore()
        restarted.set_db(db)
        names = {t["name"] for t in await restarted.list(tenant)}
        assert "PG Pack 1" not in names
        assert names == {t["name"] for t in _PACK} - {"PG Pack 1"}

        page: list[dict[str, Any]] = await restarted.list(tenant, limit=2, offset=1)
        assert len(page) == 2
        hits = await restarted.list(tenant, search="100%")
        assert [t["name"] for t in hits] == ["Rate 100%"]
        assert await restarted.list(tenant, search="_ack") == []  # '_' is literal
    finally:
        await app_eng.dispose()
        await admin.dispose()
