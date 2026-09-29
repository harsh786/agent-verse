"""Integration: marketplace v2 under enforced RLS and a NOBYPASSRLS role.

Pins, against a real Postgres at ``alembic upgrade head``:

* system built-ins are visible to (and installable by) other tenants;
* ``install_count`` / ``rating_*`` move when a tenant installs / reviews a
  template it does not own — the counter functions are re-owned by the
  NOBYPASSRLS application role here, the worst case for a SECURITY DEFINER
  function under FORCE ROW LEVEL SECURITY;
* a tenant cannot UPDATE or DELETE another tenant's review;
* ``list_installs`` returns the tenant's own installs only.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/enterprise/test_marketplace_rls_integration.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.enterprise.marketplace_v2 import _BUILTIN_TEMPLATES, MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_BUILTIN = next(t for t in _BUILTIN_TEMPLATES if t["template_id"] == "tpl-bug-fix")


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any, str, str]]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_mkt_{secrets.token_hex(4)}"
    tenant_a = f"tenant-a-{secrets.token_hex(4)}"
    tenant_b = f"tenant-b-{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        for table in (
            "marketplace_templates",
            "marketplace_installs",
            "marketplace_reviews",
            "agents",
        ):
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
            )
        await conn.execute(text(f"GRANT SELECT ON tenants TO {role}"))
        # Worst case: the definer functions owned by a NOBYPASSRLS role, so
        # FORCE RLS applies inside them too.
        for fn in (
            "marketplace_bump_install_count(TEXT)",
            "marketplace_refresh_template_rating(TEXT)",
        ):
            await conn.execute(text(f"ALTER FUNCTION {fn} OWNER TO {role}"))
        for tid in (tenant_a, tenant_b):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :email) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tid, "email": f"{tid}@example.test"},
            )

    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
        tenant_a,
        tenant_b,
    )
    await app_engine.dispose()
    async with admin_engine.begin() as conn:
        for fn in (
            "marketplace_bump_install_count(TEXT)",
            "marketplace_refresh_template_rating(TEXT)",
        ):
            await conn.execute(text(f"ALTER FUNCTION {fn} OWNER TO CURRENT_USER"))
        await conn.execute(text(f"DROP OWNED BY {role}"))
        await conn.execute(text(f"DROP ROLE {role}"))
    await admin_engine.dispose()


async def _template_row(admin: Any, template_id: str) -> Any:
    async with admin() as s:
        return (
            await s.execute(
                text(
                    "SELECT install_count, rating_avg, rating_count, review_status "
                    "FROM marketplace_templates WHERE id = :id"
                ),
                {"id": template_id},
            )
        ).one()


async def _seed_builtin(svc: MarketplaceV2) -> None:
    await svc.publish_template(
        data=_BUILTIN, tenant_ctx=_ctx("system"), run_security_review=False
    )


@pytest.mark.asyncio
async def test_builtin_visible_and_install_counted_for_other_tenant(
    factories: tuple[Any, Any, str, str],
) -> None:
    admin, app, _tenant_a, tenant_b = factories
    svc = MarketplaceV2(db_factory=app)
    await _seed_builtin(svc)

    listed = await svc.list_templates(tenant_id=tenant_b, page_size=100)
    assert "tpl-bug-fix" in {t["id"] for t in listed["templates"]}

    before = (await _template_row(admin, "tpl-bug-fix")).install_count
    result = await svc.install(
        template_id="tpl-bug-fix", params={"repo": "acme/api"}, tenant_ctx=_ctx(tenant_b)
    )
    assert result["success"] is True, result
    row = await _template_row(admin, "tpl-bug-fix")
    assert row.review_status == "approved"
    assert row.install_count == before + 1

    installs_b = await svc.list_installs(tenant_id=tenant_b)
    assert [(i["template_id"], i["agent_id"]) for i in installs_b] == [
        ("tpl-bug-fix", result["agent_id"])
    ]
    assert await svc.list_installs(tenant_id=_tenant_a) == []
    counts_b = await svc.count_by_domain(tenant_id=tenant_b)
    assert counts_b.get("software", 0) >= 1


@pytest.mark.asyncio
async def test_review_of_foreign_template_updates_rating(
    factories: tuple[Any, Any, str, str],
) -> None:
    admin, app, tenant_a, tenant_b = factories
    svc = MarketplaceV2(db_factory=app)
    record = await svc.publish_template(
        data={
            "name": "Public A",
            "slug": f"pub-{secrets.token_hex(4)}",
            "description": "summarise tickets",
            "visibility": "public",
        },
        tenant_ctx=_ctx(tenant_a),
        run_security_review=True,
    )
    assert record["review_status"] == "approved"

    result = await svc.add_review(template_id=record["id"], tenant_ctx=_ctx(tenant_b), rating=4)
    assert result["success"] is True, result
    row = await _template_row(admin, record["id"])
    assert row.rating_count == 1
    assert row.rating_avg == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_counter_function_refuses_template_invisible_to_caller(
    factories: tuple[Any, Any, str, str],
) -> None:
    admin, app, tenant_a, tenant_b = factories
    svc = MarketplaceV2(db_factory=app)
    private = await svc.publish_template(
        data={"name": "Private A", "slug": f"priv-{secrets.token_hex(4)}"},
        tenant_ctx=_ctx(tenant_a),
        run_security_review=False,
    )
    async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_b):
        updated = (
            await s.execute(
                text("SELECT marketplace_refresh_template_rating(:id)"), {"id": private["id"]}
            )
        ).scalar_one()
        bumped = (
            await s.execute(
                text("SELECT marketplace_bump_install_count(:id)"), {"id": private["id"]}
            )
        ).scalar_one()
    assert updated == 0
    assert bumped == 0
    assert (await _template_row(admin, private["id"])).install_count == 0


@pytest.mark.asyncio
async def test_tenant_cannot_update_or_delete_another_tenants_review(
    factories: tuple[Any, Any, str, str],
) -> None:
    admin, app, tenant_a, tenant_b = factories
    svc = MarketplaceV2(db_factory=app)
    await _seed_builtin(svc)
    assert (
        await svc.add_review(template_id="tpl-bug-fix", tenant_ctx=_ctx(tenant_b), rating=5)
    )["success"] is True

    async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_a):
        # A can read B's review of a public template ...
        seen = (
            await s.execute(
                text(
                    "SELECT count(*) FROM marketplace_reviews "
                    "WHERE reviewer_tenant_id = :b AND template_id = 'tpl-bug-fix'"
                ),
                {"b": tenant_b},
            )
        ).scalar_one()
        # ... but cannot rewrite or delete it.
        upd = await s.execute(
            text("UPDATE marketplace_reviews SET rating = 1 WHERE reviewer_tenant_id = :b"),
            {"b": tenant_b},
        )
        dele = await s.execute(
            text("DELETE FROM marketplace_reviews WHERE reviewer_tenant_id = :b"),
            {"b": tenant_b},
        )
    assert seen == 1
    assert upd.rowcount == 0
    assert dele.rowcount == 0

    async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_b):
        own = await s.execute(
            text(
                "UPDATE marketplace_reviews SET title = 'edited' "
                "WHERE reviewer_tenant_id = :b AND template_id = 'tpl-bug-fix'"
            ),
            {"b": tenant_b},
        )
    assert own.rowcount == 1

    async with admin() as s:
        rating = (
            await s.execute(
                text(
                    "SELECT rating FROM marketplace_reviews "
                    "WHERE reviewer_tenant_id = :b AND template_id = 'tpl-bug-fix'"
                ),
                {"b": tenant_b},
            )
        ).scalar_one()
    assert rating == 5
