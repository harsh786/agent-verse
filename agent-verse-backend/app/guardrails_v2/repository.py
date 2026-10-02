"""Postgres-backed persistence for guardrail rules (P1-4).

Rules were previously held only in ``GuardrailsEngine._rules`` (in-memory) and
were lost on restart. This repository durably stores them, tenant-scoped and
RLS-protected. It is bound to the engine in the app lifespan (two-phase wiring:
in-memory in ``create_app``, DB-backed here).

Every statement runs inside the owning tenant's RLS context (``app.tenant_id``)
and also carries an explicit ``tenant_id`` predicate. There is deliberately no
cross-tenant read: the API connects as a NOBYPASSRLS role, and the engine loads
each tenant's rules lazily instead of warming every tenant's rules at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.guardrail_rule import GuardrailRuleRow
from app.db.rls import sqlalchemy_rls_context
from app.guardrails_v2.models import (
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
    ViolationCategory,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _to_row(rule: GuardrailRule) -> dict[str, object]:
    return {
        "rule_id": rule.rule_id,
        "tenant_id": rule.tenant_id,
        "name": rule.name,
        "rule_type": rule.rule_type,
        "layers": [layer.value for layer in rule.layers],
        "action": rule.action.value,
        "categories": [c.value for c in rule.categories],
        "severity": rule.severity,
        "enabled": rule.enabled,
        "config": dict(rule.config),
        "version": rule.version,
    }


def _from_row(row: GuardrailRuleRow) -> GuardrailRule:
    return GuardrailRule(
        rule_id=row.rule_id,
        tenant_id=row.tenant_id,
        name=row.name,
        rule_type=row.rule_type,
        layers=[GuardrailLayer(x) for x in (row.layers or [])],
        action=GuardrailAction(row.action),
        categories=[ViolationCategory(c) for c in (row.categories or [])],
        severity=row.severity,
        enabled=row.enabled,
        config=dict(row.config or {}),
        version=row.version,
    )


class PostgresGuardrailRuleRepository:
    """Durable, tenant-scoped store for GuardrailRule objects."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert(self, rule: GuardrailRule) -> None:
        values = _to_row(rule)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, rule.tenant_id),
        ):
            stmt = pg_insert(GuardrailRuleRow).values(**values)
            update_cols = {k: v for k, v in values.items() if k not in ("rule_id", "tenant_id")}
            stmt = stmt.on_conflict_do_update(
                index_elements=[GuardrailRuleRow.rule_id],
                set_=update_cols,
                # Defense in depth next to RLS: never rewrite another tenant's row.
                where=GuardrailRuleRow.tenant_id == rule.tenant_id,
            )
            await db.execute(stmt)

    async def insert_if_absent(self, rule: GuardrailRule) -> None:
        """Insert ``rule`` unless a row with its id already exists.

        Used for seeded baseline / compliance-bundle rules. Their ids are
        deterministic per tenant, so an existing row means the tenant already has
        that rule, possibly edited (e.g. disabled). Re-seeding on a fresh process
        must not overwrite it with the pristine default, which an upsert would do.
        """
        values = _to_row(rule)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, rule.tenant_id),
        ):
            stmt = (
                pg_insert(GuardrailRuleRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[GuardrailRuleRow.rule_id])
            )
            await db.execute(stmt)

    async def delete(self, tenant_id: str, rule_id: str) -> None:
        from sqlalchemy import delete

        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            await db.execute(
                delete(GuardrailRuleRow).where(
                    GuardrailRuleRow.rule_id == rule_id, GuardrailRuleRow.tenant_id == tenant_id
                )
            )

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        """Load ONE tenant's rules under that tenant's RLS context.

        ``tenant_id`` is required. The previous ``load(None)`` read every tenant's
        rules through ``system_session`` at startup; under the API's NOBYPASSRLS
        role that failed ("failed to load persisted guardrail rules"), and a
        maintenance-role read has no place on the request-serving path. The engine
        now loads each tenant lazily on that tenant's first evaluation. The explicit
        predicate is defense in depth on top of the RLS policy.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required to load guardrail rules")
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            result = await db.execute(
                select(GuardrailRuleRow).where(GuardrailRuleRow.tenant_id == tenant_id)
            )
            return [_from_row(r) for r in result.scalars().all()]
