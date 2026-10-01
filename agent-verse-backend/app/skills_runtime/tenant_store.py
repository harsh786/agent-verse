"""Durable tenant custom skills (OPS-34).

Postgres (the 0074 ``skills`` table, FORCE RLS) is the single source of truth:
every read goes to the database under the tenant's RLS GUC, so a skill created
or edited on one replica is visible on every other replica immediately and
survives a restart. There is no per-process cache to hydrate or invalidate.

Every DB failure raises :class:`SkillStoreUnavailableError` (callers answer
503): a write that did not commit is never reported as a success, and an
unreadable store is never presented as "this tenant has no skills".

The DB-less dev/test build (no session factory) keeps skills in this process.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from app.db.rls import sqlalchemy_rls_context

# Upper bound on one tenant's listing (the skills page is not paginated).
MAX_TENANT_SKILLS = 500

# DB-less build only: tenant_id -> {skill_id: skill dict}
_local_skills: dict[str, dict[str, dict[str, Any]]] = {}

_COLUMNS = (
    "id, tenant_id, name, version, description, trigger_hints, instructions, "
    "allowed_tools, is_active, created_at, updated_at"
)


class SkillStoreUnavailableError(RuntimeError):
    """The tenant skill store could not be read or written (callers answer 503)."""


def _db_id(skill_id: str) -> str | None:
    """API skill id (UUID) -> the String(32) primary key; None when not a UUID."""
    try:
        return uuid.UUID(str(skill_id)).hex
    except (ValueError, AttributeError):
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _parse_ts(value: Any) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        return value
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_skill(row: Any) -> dict[str, Any]:
    try:
        skill_id = str(uuid.UUID(hex=row.id))
    except (ValueError, TypeError):
        skill_id = str(row.id)
    return {
        "skill_id": skill_id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "description": row.description,
        "trigger_hints": row.trigger_hints if isinstance(row.trigger_hints, list) else [],
        "instructions": row.instructions or "",
        "allowed_tools": row.allowed_tools if isinstance(row.allowed_tools, list) else [],
        "permissions_required": [],
        "version": row.version or "1.0.0",
        "scope": "tenant",
        "status": "active",
        "is_builtin": False,
        "author": row.tenant_id[:12] if row.tenant_id else "system",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(getattr(row, "updated_at", None)),
    }


def _params(skill: dict[str, Any]) -> dict[str, Any]:
    tenant_id = str(skill["tenant_id"])
    return {
        "id": _db_id(skill["skill_id"]),
        "tenant_id": tenant_id,
        "name": skill["name"],
        "version": skill.get("version", "1.0.0"),
        "description": skill.get("description", ""),
        "trigger_hints": json.dumps(skill.get("trigger_hints", [])),
        "instructions": skill.get("instructions", ""),
        "allowed_tools": json.dumps(skill.get("allowed_tools", [])),
        "created_by": tenant_id,
        "created_at": _parse_ts(skill.get("created_at")),
    }


def bump_patch(version: str) -> str:
    parts = (version or "1.0.0").split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts.append("1")
    return ".".join(parts)


async def list_tenant_skills(db_factory: Any, tenant_id: str) -> list[dict[str, Any]]:
    """The tenant's active custom skills, newest first (bounded)."""
    if db_factory is None:
        return list(_local_skills.get(tenant_id, {}).values())[:MAX_TENANT_SKILLS]
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
                        f"SELECT {_COLUMNS} FROM skills "
                        "WHERE tenant_id = :tid AND is_active = true "
                        "ORDER BY created_at DESC, id LIMIT :lim"
                    ),
                    {"tid": tenant_id, "lim": MAX_TENANT_SKILLS},
                )
            ).fetchall()
    except Exception as exc:
        raise SkillStoreUnavailableError(str(exc)) from exc
    return [_row_to_skill(r) for r in rows]


async def get_tenant_skill(
    db_factory: Any, tenant_id: str, skill_id: str
) -> dict[str, Any] | None:
    """One of the tenant's active custom skills, or None."""
    if db_factory is None:
        return _local_skills.get(tenant_id, {}).get(skill_id)
    db_id = _db_id(skill_id)
    if db_id is None:
        return None
    from sqlalchemy import text

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM skills "
                        "WHERE id = :id AND tenant_id = :tid AND is_active = true"
                    ),
                    {"id": db_id, "tid": tenant_id},
                )
            ).first()
    except Exception as exc:
        raise SkillStoreUnavailableError(str(exc)) from exc
    return _row_to_skill(row) if row is not None else None


async def create_tenant_skill(db_factory: Any, skill: dict[str, Any]) -> None:
    """Insert a new tenant skill. Raises unless the row committed."""
    tenant_id = str(skill.get("tenant_id") or "")
    if not tenant_id:
        raise ValueError("a tenant skill needs a tenant_id")
    if db_factory is None:
        _local_skills.setdefault(tenant_id, {})[skill["skill_id"]] = dict(skill)
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
                    "INSERT INTO skills (id, tenant_id, name, version, description, "
                    "trigger_hints, instructions, few_shot_examples, allowed_tools, "
                    "required_connectors, token_estimate, visibility, is_active, created_by, "
                    "created_at, updated_at) VALUES (:id, :tenant_id, :name, :version, "
                    ":description, CAST(:trigger_hints AS json), :instructions, "
                    "CAST('[]' AS json), CAST(:allowed_tools AS json), CAST('[]' AS json), 0, "
                    "'tenant', true, :created_by, "
                    "COALESCE(CAST(:created_at AS timestamptz), NOW()), NOW())"
                ),
                _params(skill),
            )
    except Exception as exc:
        raise SkillStoreUnavailableError(str(exc)) from exc


_UPDATABLE = ("name", "description", "trigger_hints", "instructions", "allowed_tools")


async def update_tenant_skill(
    db_factory: Any,
    tenant_id: str,
    skill_id: str,
    changes: dict[str, Any],
    *,
    archive: Any = None,
) -> dict[str, Any] | None:
    """Apply *changes* and bump the patch version atomically; None when absent.

    The row is locked (``FOR UPDATE``) for the read-modify-write so two
    replicas editing the same skill cannot both produce the same version.
    *archive*, when given, is awaited as ``archive(session, previous_skill)``
    inside the same transaction (version history, OPS-04).
    """
    fields = {k: v for k, v in changes.items() if k in _UPDATABLE}
    now = datetime.datetime.now(datetime.UTC)
    if db_factory is None:
        current = _local_skills.get(tenant_id, {}).get(skill_id)
        if current is None:
            return None
        if archive is not None:
            await archive(None, dict(current))
        current.update(fields)
        current["version"] = bump_patch(str(current.get("version", "1.0.0")))
        current["updated_at"] = now.isoformat()
        return dict(current)
    db_id = _db_id(skill_id)
    if db_id is None:
        return None
    from sqlalchemy import text

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM skills WHERE id = :id AND tenant_id = :tid "
                        "AND is_active = true FOR UPDATE"
                    ),
                    {"id": db_id, "tid": tenant_id},
                )
            ).first()
            if row is None:
                return None
            previous = _row_to_skill(row)
            if archive is not None:
                await archive(session, previous)
            updated = {**previous, **fields, "version": bump_patch(previous["version"])}
            await session.execute(
                text(
                    "UPDATE skills SET name = :name, version = :version, "
                    "description = :description, trigger_hints = CAST(:trigger_hints AS json), "
                    "instructions = :instructions, allowed_tools = CAST(:allowed_tools AS json), "
                    "updated_at = NOW() WHERE id = :id AND tenant_id = :tenant_id"
                ),
                _params(updated),
            )
    except Exception as exc:
        raise SkillStoreUnavailableError(str(exc)) from exc
    updated["updated_at"] = now.isoformat()
    return updated
