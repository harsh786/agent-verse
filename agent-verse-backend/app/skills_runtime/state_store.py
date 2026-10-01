"""Durable per-tenant skill enable/disable state (OPS-03).

One source for both skills-runtime toggle APIs (``/permissions/{enable,disable}``
and ``/{skill_id}/{enable,disable}``) and every execute path. With a DB wired the
state lives in ``skill_runtime_tenant_state`` (FORCE RLS, read under the tenant
GUC on every check), so a disable binds on every replica and survives a
restart. The DB-less dev/test build keeps it in this process.

No row means "never toggled" — the skill is allowed.
"""

from __future__ import annotations

from typing import Any

from app.db.rls import sqlalchemy_rls_context

# DB-less build only: tenant_id -> {skill_id: enabled}
_local_state: dict[str, dict[str, bool]] = {}


class SkillStateUnavailableError(RuntimeError):
    """The durable skill state could not be read or written (callers fail closed)."""


async def set_skill_enabled(db_factory: Any, tenant_id: str, skill_id: str, enabled: bool) -> None:
    """Persist a tenant's toggle for one skill. Raises on a DB failure."""
    if db_factory is None:
        _local_state.setdefault(tenant_id, {})[skill_id] = enabled
        return
    from sqlalchemy import text

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO skill_runtime_tenant_state (tenant_id, skill_id, enabled, "
                    "updated_at) VALUES (:tid, :sid, :en, NOW()) "
                    "ON CONFLICT (tenant_id, skill_id) DO UPDATE SET "
                    "enabled = EXCLUDED.enabled, updated_at = NOW()"
                ),
                {"tid": tenant_id, "sid": skill_id, "en": enabled},
            )
    except Exception as exc:
        raise SkillStateUnavailableError(str(exc)) from exc


async def disabled_skills(db_factory: Any, tenant_id: str) -> set[str]:
    """The tenant's disabled skill ids. Raises on a DB failure (fail closed)."""
    if db_factory is None:
        return {sid for sid, en in _local_state.get(tenant_id, {}).items() if not en}
    from sqlalchemy import text

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT skill_id FROM skill_runtime_tenant_state "
                        "WHERE tenant_id = :tid AND enabled IS FALSE"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
    except Exception as exc:
        raise SkillStateUnavailableError(str(exc)) from exc
    return {str(r[0]) for r in rows}
