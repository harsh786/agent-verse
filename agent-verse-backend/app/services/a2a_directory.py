"""Public A2A agent directory (owner decision D3, 2026-10-05).

Off by default. An agent is publicly listed only when ALL hold:

* its tenant switched the directory on (``tenants.a2a_directory_enabled``,
  admin-only ``PUT /tenants/me/a2a-directory``) and the tenant is active;
* the agent opted in (``agents.a2a_public``, ``PUT /agents/{id}``);
* the agent is active (not deleted, not archived).

What is public is only the card: agent id, name, a description and a skills
summary the owner wrote for the directory, and the endpoint. Never tools,
prompts, model, connector names or any other internals.

Storage: ``a2a_public_agents`` is a projection holding exactly those card
fields, one row per opted-in active agent, written in the same transaction as
every agent change (``sync_public_agent``) and removed with the agent. It holds
nothing private by construction, so the unauthenticated directory reads it with
the application role (a permissive SELECT policy on that table only) — no
BYPASSRLS connection on a request path, and no read of the ``agents`` table
across tenants. The tenant switch is joined at read time, so turning it off
hides every card at once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

A2A_DESCRIPTION_MAX = 500
A2A_SKILLS_MAX = 20
A2A_SKILL_MAX_LEN = 80
DIRECTORY_PAGE_MAX = 100


class DirectoryUnavailableError(RuntimeError):
    """The directory state could not be read or written (never a fake empty answer)."""


@dataclass(frozen=True)
class PublicAgent:
    agent_id: str
    tenant_id: str
    name: str
    description: str
    skills: tuple[str, ...]


def agent_card(agent: PublicAgent, base_url: str) -> dict[str, Any]:
    """The minimal public card. The tenant id is deliberately not part of it."""
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "description": agent.description,
        "skills": list(agent.skills),
        "endpoint": base_url.rstrip("/") + "/a2a/tasks",
    }


def normalize_skills(skills: list[str]) -> list[str]:
    """Strip, drop empties and duplicates; raise ValueError past the bounds."""
    out: list[str] = []
    for raw in skills:
        skill = str(raw).strip()
        if not skill or skill in out:
            continue
        if len(skill) > A2A_SKILL_MAX_LEN:
            raise ValueError(f"each skill is at most {A2A_SKILL_MAX_LEN} characters")
        out.append(skill)
    if len(out) > A2A_SKILLS_MAX:
        raise ValueError(f"at most {A2A_SKILLS_MAX} skills")
    return out


def _is_listable(record: dict[str, Any]) -> bool:
    return (
        record.get("a2a_public") is True
        and record.get("is_active", True) is not False
        and not record.get("is_archived", False)
    )


def _public_from_record(record: dict[str, Any]) -> PublicAgent:
    return PublicAgent(
        agent_id=str(record["agent_id"]),
        tenant_id=str(record["tenant_id"]),
        name=str(record.get("name") or ""),
        description=str(record.get("a2a_description") or ""),
        skills=tuple(str(s) for s in record.get("a2a_skills") or []),
    )


async def sync_public_agent(session: Any, *, agent_id: str, tenant_id: str) -> None:
    """Make the projection row of one agent match the agent, inside the caller's
    transaction (the agent write and its card change commit together)."""
    from sqlalchemy import text

    await session.execute(
        text("DELETE FROM a2a_public_agents WHERE agent_id = :id AND tenant_id = :tid"),
        {"id": agent_id, "tid": tenant_id},
    )
    await session.execute(
        text(
            "INSERT INTO a2a_public_agents (agent_id, tenant_id, name, description, skills, "
            "updated_at) "
            "SELECT id, tenant_id, name, a2a_description, "
            "CAST(CAST(a2a_skills AS text) AS jsonb), now() FROM agents "
            "WHERE id = :id AND tenant_id = :tid AND a2a_public IS TRUE "
            "AND is_active IS TRUE AND is_archived IS NOT TRUE"
        ),
        {"id": agent_id, "tid": tenant_id},
    )


class A2ADirectory:
    """Reads and the tenant switch; DB-backed, or in-memory for the no-DB app."""

    def __init__(
        self,
        *,
        db: Any = None,
        agent_store: Any = None,
        memory_settings: dict[str, bool] | None = None,
    ) -> None:
        self._db = db
        self._agents = agent_store
        self._settings = memory_settings if memory_settings is not None else {}

    # ── tenant switch ────────────────────────────────────────────────────────

    async def tenant_enabled(self, tenant_id: str) -> bool:
        if self._db is None:
            return bool(self._settings.get(tenant_id, False))
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                value = (
                    await session.execute(
                        text("SELECT a2a_directory_enabled FROM tenants WHERE id = :tid"),
                        {"tid": tenant_id},
                    )
                ).scalar_one_or_none()
        except Exception as exc:
            raise DirectoryUnavailableError("directory setting unavailable") from exc
        return bool(value)

    async def set_tenant_enabled(self, tenant_id: str, enabled: bool) -> None:
        if self._db is None:
            self._settings[tenant_id] = enabled
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text(
                        "UPDATE tenants SET a2a_directory_enabled = :v, updated_at = now() "
                        "WHERE id = :tid"
                    ),
                    {"v": enabled, "tid": tenant_id},
                )
        except Exception as exc:
            raise DirectoryUnavailableError("directory setting could not be saved") from exc
        if result.rowcount != 1:
            raise DirectoryUnavailableError("tenant not found")

    # ── public reads ─────────────────────────────────────────────────────────

    async def list_public(self, *, after: str | None, limit: int) -> list[PublicAgent]:
        """One keyset page (by agent id) of publicly listed agents, all tenants."""
        limit = max(1, min(limit, DIRECTORY_PAGE_MAX))
        if self._db is None:
            rows = sorted(
                (a for a in self._memory_public() if after is None or a.agent_id > after),
                key=lambda a: a.agent_id,
            )
            return rows[:limit]
        return await self._query(
            "AND (CAST(:after AS text) IS NULL OR p.agent_id > CAST(:after AS text)) "
            "ORDER BY p.agent_id LIMIT :lim",
            {"after": after, "lim": limit},
        )

    async def get_public(self, agent_id: str) -> PublicAgent | None:
        if self._db is None:
            return next((a for a in self._memory_public() if a.agent_id == agent_id), None)
        rows = await self._query("AND p.agent_id = :id", {"id": agent_id})
        return rows[0] if rows else None

    async def get_public_for_tenant(self, tenant_id: str, agent_id: str) -> PublicAgent | None:
        """The agent when it is publicly listed AND belongs to ``tenant_id``."""
        if self._db is None:
            found = await self.get_public(agent_id)
            return found if found is not None and found.tenant_id == tenant_id else None
        rows = await self._query(
            "AND p.agent_id = :id AND p.tenant_id = :tid", {"id": agent_id, "tid": tenant_id}
        )
        return rows[0] if rows else None

    def _memory_public(self) -> list[PublicAgent]:
        data: dict[tuple[str, str], dict[str, Any]] = getattr(self._agents, "_data", {}) or {}
        return [
            _public_from_record(rec)
            for (tenant_id, _agent_id), rec in data.items()
            if self._settings.get(tenant_id, False) and _is_listable(rec)
        ]

    async def _query(self, tail: str, params: dict[str, Any]) -> list[PublicAgent]:
        # Deliberately cross-tenant and without a tenant RLS scope: it reads only the
        # card projection through its permissive public-read policy (see the module
        # docstring), never ``agents``. The tenant switch methods above are scoped.
        from sqlalchemy import text

        sql = (
            "SELECT p.agent_id, p.tenant_id, p.name, p.description, p.skills "
            "FROM a2a_public_agents p JOIN tenants t ON t.id = p.tenant_id "
            "WHERE t.is_active IS TRUE AND t.a2a_directory_enabled IS TRUE " + tail
        )
        try:
            async with self._db() as session, session.begin():
                rows = (await session.execute(text(sql), params)).fetchall()
        except Exception as exc:
            raise DirectoryUnavailableError("agent directory unavailable") from exc
        out = []
        for r in rows:
            skills = r[4]
            if isinstance(skills, str):
                skills = json.loads(skills)
            out.append(
                PublicAgent(
                    agent_id=str(r[0]),
                    tenant_id=str(r[1]),
                    name=str(r[2] or ""),
                    description=str(r[3] or ""),
                    skills=tuple(str(s) for s in skills or []),
                )
            )
        return out


def directory_for(state: Any) -> A2ADirectory:
    """The directory over this app's DB (or its in-memory stores without one)."""
    settings = getattr(state, "a2a_directory_settings", None)
    if settings is None:
        settings = {}
        state.a2a_directory_settings = settings
    return A2ADirectory(
        db=getattr(state, "db_session_factory", None),
        agent_store=getattr(state, "agent_store", None),
        memory_settings=settings,
    )
