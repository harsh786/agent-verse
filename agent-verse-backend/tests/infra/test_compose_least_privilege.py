"""LEAST-PRIVILEGE-LOCAL: the dev compose stack runs the app as a NOBYPASSRLS role.

It used to give the API and every worker ``agentverse:agentverse`` — the
Postgres SUPERUSER — so RLS was bypassed for every table locally and
``GET /api/v1/runs`` returned every tenant's runs. Now:

* the app services connect as ``${APP_DB_USER}`` (NOSUPERUSER, NOBYPASSRLS);
* ``agentverse`` is only the migration owner (``db-migrate``) and the
  cross-tenant MAINTENANCE_DATABASE_URL;
* the role is created on a fresh volume by an initdb script and, for existing
  volumes, by ``db-migrate`` (alembic + app/db/app_role.py) before anything
  connects;
* pgbouncer knows the app role's credentials.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

INFRA = Path(__file__).resolve().parents[2] / "infra"
COMPOSE = INFRA / "docker-compose.yml"
APP_SERVICES = ("backend", "worker", "subgoal-worker", "beat", "workflow-worker")
_USER = re.compile(r"^postgresql\+asyncpg://([^:@]+):")


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text())


def _env(svc: dict[str, Any]) -> dict[str, str]:
    env = svc.get("environment", {})
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


def _user(dsn: str) -> str:
    """The user part of a DSN, which may itself be a ``${VAR:-default}``."""
    assert dsn.startswith("postgresql+asyncpg://"), dsn
    userinfo = dsn.removeprefix("postgresql+asyncpg://").rsplit("@", 1)[0]
    if userinfo.startswith("${"):
        return userinfo[: userinfo.index("}") + 1]
    m = _USER.match(dsn)
    assert m, f"unexpected DSN shape: {dsn}"
    return m.group(1)


def test_app_services_connect_as_the_least_privilege_role() -> None:
    services = _compose()["services"]
    for name in APP_SERVICES:
        env = _env(services[name])
        assert _user(env["DATABASE_URL"]) == "${APP_DB_USER:-agentverse_app}", name
        assert "agentverse:agentverse@" not in env["DATABASE_URL"], name
        assert "${APP_DB_PASSWORD:-agentverse_app}" in env["DATABASE_URL"], name
        # Cross-tenant system jobs keep the BYPASSRLS owner, explicitly.
        assert _user(env["MAINTENANCE_DATABASE_URL"]) == "agentverse", name
        # Nothing at runtime migrates: the app role cannot run DDL.
        assert "MIGRATION_DATABASE_URL" not in env, name


def test_app_services_wait_for_the_migrate_and_role_bootstrap() -> None:
    services = _compose()["services"]
    for name in APP_SERVICES:
        deps = services[name]["depends_on"]
        assert deps["db-migrate"]["condition"] == "service_completed_successfully", name
    # The backend must not run alembic itself any more (Dockerfile CMD does).
    assert "alembic" not in " ".join(services["backend"]["command"])


def test_db_migrate_runs_alembic_as_the_owner_and_provisions_the_app_role() -> None:
    svc = _compose()["services"]["db-migrate"]
    env = _env(svc)
    assert "alembic upgrade head" in " ".join(svc["command"])
    assert _user(env["MIGRATION_DATABASE_URL"]) == "agentverse"
    assert env["APP_DB_USER"] == "${APP_DB_USER:-agentverse_app}"
    assert env["APP_DB_PASSWORD"] == "${APP_DB_PASSWORD:-agentverse_app}"
    assert svc["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert svc.get("restart") == "on-failure"


def test_fresh_volume_initdb_creates_the_role() -> None:
    pg = _compose()["services"]["postgres"]
    assert "./postgres/init:/docker-entrypoint-initdb.d:ro" in pg["volumes"]
    env = _env(pg)
    assert env["APP_DB_USER"] == "${APP_DB_USER:-agentverse_app}"
    assert env["APP_DB_PASSWORD"] == "${APP_DB_PASSWORD:-agentverse_app}"
    script = (INFRA / "postgres/init/10-agentverse-app-role.sh").read_text()
    assert "NOBYPASSRLS" in script and "NOSUPERUSER" in script
    assert "ALTER DEFAULT PRIVILEGES" in script
    assert os.access(INFRA / "postgres/init/10-agentverse-app-role.sh", os.X_OK)


def test_pgbouncer_knows_the_app_role() -> None:
    pgb = _compose()["services"]["pgbouncer"]
    env = _env(pgb)
    assert env["APP_DB_USER"] == "${APP_DB_USER:-agentverse_app}"
    assert env["APP_DB_PASSWORD"] == "${APP_DB_PASSWORD:-agentverse_app}"
    wrapper = "./pgbouncer/add-app-user.sh"
    assert any(str(v).startswith(wrapper + ":") for v in pgb["volumes"])
    assert pgb["entrypoint"][-1].endswith("add-app-user.sh")
    assert pgb["command"] == ["/usr/bin/pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
    script = (INFRA / "pgbouncer/add-app-user.sh").read_text()
    assert "exec /entrypoint.sh" in script
    # Two (db, user) pools now share Postgres's 100 connections.
    assert int(env["MAX_DB_CONNECTIONS"]) <= 90
