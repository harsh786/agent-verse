"""Postgres-backed persistence for guardrail rules (P1-4).

Rules were previously held only in ``GuardrailsEngine._rules`` (in-memory) and
were lost on restart. This repository durably stores them, tenant-scoped and
RLS-protected. It is bound to the engine in the app lifespan (two-phase wiring:
in-memory in ``create_app``, DB-backed here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.guardrail_rule import GuardrailRuleRow
from app.db.rls import sqlalchemy_rls_context, system_session
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
                index_elements=[GuardrailRuleRow.rule_id], set_=update_cols
            )
            await db.execute(stmt)

    async def load(self, tenant_id: str | None = None) -> list[GuardrailRule]:
        """Load rules. With ``tenant_id`` set, scope to that tenant under its RLS
        context; with ``None`` (lifespan rehydrate) read across tenants via a
        system session (requires a BYPASSRLS/superuser DB role)."""
        if tenant_id is not None:
            async with (
                self._sessions() as db,
                db.begin(),
                sqlalchemy_rls_context(db, tenant_id),
            ):
                rows = (
                    await db.execute(
                        select(GuardrailRuleRow).where(
                            GuardrailRuleRow.tenant_id == tenant_id
                        )
                    )
                ).scalars().all()
                return [_from_row(r) for r in rows]

        async with self._sessions() as db, db.begin(), system_session(db):
            rows = (await db.execute(select(GuardrailRuleRow))).scalars().all()
            return [_from_row(r) for r in rows]
