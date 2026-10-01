"""Postgres-backed Voyager skill library (``voyager_skills``, FORCE RLS).

The in-process :class:`~app.memory.voyager_skills.VoyagerSkillStore` lost every
skill on restart and was invisible to other replicas. This store keeps the same
contract — validate the :class:`ProcedureContract` against the caller's tools,
capabilities, connectors and policy before publishing; a published
``(tenant, procedure, version)`` is immutable (re-publishing the identical skill
is idempotent, a different one with the same version is refused) — but in
Postgres, tenant-scoped by RLS, so every replica and every restart shares it.
Database errors propagate (fail closed): a skill is never reported published
when it was not stored.
"""

from __future__ import annotations

import json
from typing import Any

from app.memory.procedural_validator import ProcedureContract, validate_procedure


def _contract_json(skill: ProcedureContract) -> dict[str, Any]:
    data = skill.model_dump(mode="json")
    # frozensets dump as lists in arbitrary order; sort for a stable comparison.
    for key in ("required_capabilities", "connector_ids"):
        data[key] = sorted(data[key])
    return data


def _contract_from_json(data: Any) -> ProcedureContract:
    raw = json.loads(data) if isinstance(data, str) else dict(data)
    return ProcedureContract.model_validate(raw)


class PostgresVoyagerSkillStore:
    def __init__(self, db_factory: Any) -> None:
        self._db = db_factory

    async def publish(
        self,
        skill: ProcedureContract,
        *,
        available_tools: dict[str, str],
        allowed_capabilities: frozenset[str],
        ready_connectors: frozenset[str],
        policy_fingerprint: str,
        provenance: dict[str, Any] | None = None,
    ) -> ProcedureContract:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        validate_procedure(
            skill,
            tenant_id=skill.tenant_id,
            available_tools=available_tools,
            allowed_capabilities=allowed_capabilities,
            ready_connectors=ready_connectors,
            policy_fingerprint=policy_fingerprint,
        )
        meta = provenance or {}
        contract = _contract_json(skill)
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, skill.tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO voyager_skills "
                    "(tenant_id, procedure_id, skill_version, contract, steps, "
                    " evidence_refs, goal_id) "
                    "VALUES (CAST(:tid AS uuid), :pid, :ver, CAST(:contract AS jsonb), "
                    " CAST(:steps AS jsonb), CAST(:evidence AS jsonb), :goal) "
                    "ON CONFLICT (tenant_id, procedure_id, skill_version) DO NOTHING"
                ),
                {
                    "tid": skill.tenant_id,
                    "pid": skill.procedure_id,
                    "ver": skill.skill_version,
                    "contract": json.dumps(contract),
                    "steps": json.dumps(list(meta.get("steps") or [])),
                    "evidence": json.dumps(list(meta.get("evidence_refs") or [])),
                    "goal": meta.get("goal_id"),
                },
            )
            stored = (
                await session.execute(
                    text(
                        "SELECT contract FROM voyager_skills "
                        "WHERE tenant_id = CAST(:tid AS uuid) AND procedure_id = :pid "
                        "AND skill_version = :ver"
                    ),
                    {"tid": skill.tenant_id, "pid": skill.procedure_id, "ver": skill.skill_version},
                )
            ).scalar_one()
        existing = _contract_from_json(stored)
        if _contract_json(existing) != contract:
            raise ValueError("skill version is immutable")
        return existing

    async def get(
        self, tenant_id: str, procedure_id: str, skill_version: str
    ) -> dict[str, Any] | None:
        rows = await self._select(
            tenant_id,
            "AND procedure_id = :pid AND skill_version = :ver",
            {"pid": procedure_id, "ver": skill_version},
        )
        return rows[0] if rows else None

    async def list(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return await self._select(
            tenant_id, "ORDER BY created_at DESC LIMIT :lim", {"lim": int(limit)}
        )

    async def _select(
        self, tenant_id: str, clause: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT procedure_id, skill_version, contract, steps, evidence_refs, "
                        "goal_id, created_at FROM voyager_skills "
                        "WHERE tenant_id = CAST(:tid AS uuid) " + clause
                    ),
                    {"tid": tenant_id, **params},
                )
            ).mappings().all()
        return [
            {
                "procedure_id": r["procedure_id"],
                "skill_version": r["skill_version"],
                "contract": _contract_from_json(r["contract"]),
                "steps": list(r["steps"] or []),
                "evidence_refs": list(r["evidence_refs"] or []),
                "goal_id": r["goal_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]


__all__ = ["PostgresVoyagerSkillStore"]
