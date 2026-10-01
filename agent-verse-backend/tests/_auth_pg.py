"""Least-privilege Postgres helpers for auth-state integration tests.

``app_role_url(pg_url)`` provisions (idempotently) the same NOSUPERUSER /
NOBYPASSRLS application role production runs as — through the real
``app.db.app_role.ensure_app_role`` bootstrap — and returns its DSN, so RLS is
enforced for real. ``owner_engine`` is for fixture setup/cleanup only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

APP_ROLE = "authtest_app"
APP_PASSWORD = "authtest-app-pw"


async def app_role_url(pg_url: str) -> str:
    from app.db.app_role import AppRoleSpec, ensure_app_role

    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(ensure_app_role, AppRoleSpec(role=APP_ROLE, password=APP_PASSWORD))
            await conn.commit()
    finally:
        await engine.dispose()
    return (
        make_url(pg_url)
        .set(username=APP_ROLE, password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )


def session_factory(url: str) -> tuple[AsyncEngine, Any]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
