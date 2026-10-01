"""Alembic environment — async engine, DB URL sourced from application Settings."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DSN (async driver) so we never hardcode credentials in alembic.ini.
# Migrations run as the schema owner: MIGRATION_DATABASE_URL when the app itself
# connects as the least-privilege application role (DATABASE_URL), which cannot
# run DDL. ``%`` is escaped because ConfigParser interpolates it.
_settings = get_settings()
_migration_url = (_settings.migration_database_url or "").strip() or _settings.database_url
config.set_main_option("sqlalchemy.url", _migration_url.replace("%", "%%"))

# target_metadata stays None until ORM models are introduced (Phase 1+); migrations are
# authored explicitly to keep full control over RLS policies and pgvector index types.
target_metadata = None


def _run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)
        await _ensure_app_role(connection)
    await connectable.dispose()


async def _ensure_app_role(connection: object) -> None:
    """Provision the least-privilege application role (APP_DB_USER), if configured.

    Runs after every upgrade so fresh and existing databases converge on the
    same NOSUPERUSER/NOBYPASSRLS role with grants on every table — including
    the ones the migrations just created. No APP_DB_USER = no-op.
    """
    from sqlalchemy.engine import make_url

    from app.db.app_role import app_role_spec_from_env, ensure_app_role

    spec = app_role_spec_from_env(owner_role=make_url(_migration_url).username)
    if spec is None:
        return
    await connection.run_sync(ensure_app_role, spec)  # type: ignore[attr-defined]
    await connection.commit()  # type: ignore[attr-defined]


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async())
