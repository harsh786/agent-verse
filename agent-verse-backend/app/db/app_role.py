"""Idempotent bootstrap of the least-privilege APPLICATION database role.

The API and the Celery workers must connect as a role that is NOSUPERUSER and
NOBYPASSRLS, so Postgres row-level security actually isolates tenants: a
superuser (or BYPASSRLS) connection ignores every RLS policy, which is how the
local stack served one tenant every other tenant's workflow runs.

Three roles, three DSNs:

* ``MIGRATION_DATABASE_URL`` — the schema owner (``agentverse`` locally). Alembic
  runs as it; it owns every table.
* ``MAINTENANCE_DATABASE_URL`` — BYPASSRLS, for the few cross-tenant system jobs
  (``app.db.session.get_system_session_factory``).
* ``DATABASE_URL`` — the application role created/repaired here: DML only on the
  owner's tables, no DDL, no RLS bypass.

``ensure_app_role`` runs after every ``alembic upgrade`` (see
``app/db/migrations/env.py``) when ``APP_DB_USER`` is set, so a fresh volume
(also covered by ``infra/postgres/init/10-agentverse-app-role.sh``) and an
existing one converge on the same role: created if missing, demoted to
NOSUPERUSER/NOBYPASSRLS if it was ever granted more, password rotated to
``APP_DB_PASSWORD``, grants re-applied on every existing object, and default
privileges set so tables a later migration creates are usable too. Unset
``APP_DB_USER`` = no-op (environments that provision roles elsewhere).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# The local-dev default, mirroring the existing ``agentverse:agentverse`` pattern.
# Production refuses it (``app_role_spec_from_env``).
DEV_DEFAULT_APP_DB_PASSWORD = "agentverse_app"

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"


class AppRoleError(RuntimeError):
    """The application role cannot be provisioned safely."""


@dataclass(frozen=True)
class AppRoleSpec:
    role: str
    password: str


def app_role_spec_from_env(
    environ: Mapping[str, str] | None = None, *, owner_role: str | None = None
) -> AppRoleSpec | None:
    """Read ``APP_DB_USER`` / ``APP_DB_PASSWORD``; ``None`` when no role is configured.

    Fails closed: a missing password, an unsafe role name, the migration role
    itself, or the dev-default password in production all raise.
    """
    env = os.environ if environ is None else environ
    role = (env.get("APP_DB_USER") or "").strip()
    if not role:
        return None
    if not _ROLE_NAME.match(role):
        raise AppRoleError(f"APP_DB_USER {role!r} is not a plain lower-case identifier")
    if owner_role is not None and role == owner_role:
        raise AppRoleError(
            "APP_DB_USER must not be the migration/owner role: the owner bypasses "
            "non-FORCEd RLS and can run DDL"
        )
    password = env.get("APP_DB_PASSWORD") or ""
    if not password:
        raise AppRoleError("APP_DB_PASSWORD must be set when APP_DB_USER is")
    environment = (env.get("ENVIRONMENT") or "development").strip().lower()
    if environment == "production" and password == DEV_DEFAULT_APP_DB_PASSWORD:
        raise AppRoleError("refusing the default APP_DB_PASSWORD in production")
    return AppRoleSpec(role=role, password=password)


def ensure_app_role(connection: Any, spec: AppRoleSpec) -> None:
    """Create or repair ``spec.role`` on a SYNC SQLAlchemy connection.

    Run as the schema owner (default privileges are set FOR the current role).
    The caller commits. Raises ``AppRoleError`` if the role still ends up able
    to bypass RLS (e.g. it is the connection's own role).
    """
    from sqlalchemy import text

    if not _ROLE_NAME.match(spec.role):  # identifiers are inlined below
        raise AppRoleError(f"unsafe role name {spec.role!r}")
    current = connection.execute(text("SELECT current_user")).scalar_one()
    if current == spec.role:
        raise AppRoleError("the application role must not be the migration role")

    role = f'"{spec.role}"'
    password = connection.execute(
        text("SELECT quote_literal(:p)"), {"p": spec.password}
    ).scalar_one()
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": spec.role}
    ).first()
    verb = "ALTER" if exists else "CREATE"
    connection.execute(text(f"{verb} ROLE {role} {_ROLE_ATTRS} PASSWORD {password}"))

    database = connection.execute(text("SELECT quote_ident(current_database())")).scalar_one()
    for stmt in (
        f"GRANT CONNECT, TEMPORARY ON DATABASE {database} TO {role}",
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {role}",
        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}",
        # Objects the owner creates later (new migrations, runtime partitions).
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {role}",
    ):
        connection.execute(text(stmt))

    row = connection.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": spec.role}
    ).one()
    if row[0] or row[1]:
        raise AppRoleError(f"role {spec.role!r} can still bypass row-level security")


__all__ = [
    "DEV_DEFAULT_APP_DB_PASSWORD",
    "AppRoleError",
    "AppRoleSpec",
    "app_role_spec_from_env",
    "ensure_app_role",
]
