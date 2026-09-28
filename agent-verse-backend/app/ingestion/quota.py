"""Per-tenant ingestion quotas (pipeline Stage 1 + ``POST /sources``).

Before this module the pipeline's ``quota_enforcer`` was never constructed, so
Stage 1 was skipped for every document, and ``GET /ingestion/quota`` counted the
API module's in-memory ``_SOURCES`` dict — which is always empty once the
DB-backed ``SourceConfigStore`` is wired (i.e. in production). Nothing enforced
the Source limit on ``POST /sources`` either.

All usage numbers here come from the database (the source of truth), read under
the tenant's RLS context with an explicit ``tenant_id`` predicate, using
aggregate SQL over indexed columns — never by loading rows:

* sources   — ``COUNT(*) FROM source_configs WHERE tenant_id`` (idx_source_configs_tenant)
* documents — ``SUM(document_count) FROM knowledge_collections WHERE tenant_id``: the
  per-collection counters ``KnowledgeStore._persist_chunks`` maintains exactly under
  the collection row lock, so this never scans the (millions-row) chunk tables.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from app.tenancy.context import PlanTier

# ``None`` = unlimited. The Source limits are the ones ``GET /ingestion/quota``
# already advertised (free 2 / starter 10 / professional 50 / enterprise ∞).
SOURCE_LIMITS: dict[str, int | None] = {
    PlanTier.FREE.value: 2,
    PlanTier.STARTER.value: 10,
    PlanTier.PROFESSIONAL.value: 50,
    PlanTier.ENTERPRISE.value: None,
}
DOCUMENT_LIMITS: dict[str, int | None] = {
    PlanTier.FREE.value: 1_000,
    PlanTier.STARTER.value: 50_000,
    PlanTier.PROFESSIONAL.value: 2_000_000,
    PlanTier.ENTERPRISE.value: None,
}


class IngestionQuotaExceededError(Exception):
    """A tenant is at its plan's Source or document limit."""

    def __init__(self, kind: str, used: int, limit: int, plan: str) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        self.plan = plan
        super().__init__(f"{kind} quota exceeded for plan '{plan}': {used}/{limit}")


@dataclass(frozen=True)
class IngestionUsage:
    plan: str
    sources_used: int
    documents_indexed: int
    chunks_indexed: int
    bytes_indexed: int

    @property
    def sources_limit(self) -> int | None:
        # Unknown plan strings get the free tier, never "unlimited".
        return SOURCE_LIMITS.get(self.plan, SOURCE_LIMITS[PlanTier.FREE.value])

    @property
    def documents_limit(self) -> int | None:
        return DOCUMENT_LIMITS.get(self.plan, DOCUMENT_LIMITS[PlanTier.FREE.value])


def _plan_value(plan: Any) -> str:
    value = getattr(plan, "value", plan)
    return str(value or PlanTier.FREE.value).lower()


class IngestionQuotaEnforcer:
    """DB-backed quota checks. ``db`` is the application (NOBYPASSRLS) factory."""

    def __init__(self, db: Any) -> None:
        if db is None:
            raise ValueError("IngestionQuotaEnforcer requires a database session factory")
        self._db = db

    async def usage(self, tenant_id: str, *, plan: Any = None) -> IngestionUsage:
        """Current usage for one tenant, straight from the database.

        ``plan`` may be supplied by a caller that already holds the tenant's
        authenticated context (API path); the pipeline passes none and the plan
        is read from ``tenants.plan_tier``.
        """
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            if plan is None:
                plan = (
                    await session.execute(
                        text("SELECT plan_tier FROM tenants WHERE id = :tid"),
                        {"tid": tenant_id},
                    )
                ).scalar_one_or_none()
            sources = (
                await session.execute(
                    text("SELECT COUNT(*) FROM source_configs WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            docs_row = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(document_count), 0), "
                        "COALESCE(SUM(chunk_count), 0), "
                        "COALESCE(SUM(total_size_bytes), 0) "
                        "FROM knowledge_collections "
                        "WHERE tenant_id = :tid AND is_active IS TRUE"
                    ),
                    {"tid": tenant_id},
                )
            ).one()
        return IngestionUsage(
            plan=_plan_value(plan),
            sources_used=int(sources or 0),
            documents_indexed=int(docs_row[0] or 0),
            chunks_indexed=int(docs_row[1] or 0),
            bytes_indexed=int(docs_row[2] or 0),
        )

    async def check_doc_quota(self, tenant_id: str) -> None:
        """Pipeline Stage 1: raise ``IngestionQuotaExceededError`` at the doc limit.

        Any other exception (DB down) propagates — the pipeline reports it as a
        failure (→ DLQ, retried) instead of pretending the tenant is over quota.
        """
        usage = await self.usage(tenant_id)
        limit = usage.documents_limit
        if limit is not None and usage.documents_indexed >= limit:
            raise IngestionQuotaExceededError(
                "document", usage.documents_indexed, limit, usage.plan
            )

    async def check_source_quota(self, tenant_id: str, *, plan: Any = None) -> IngestionUsage:
        """``POST /sources``: raise ``IngestionQuotaExceededError`` at the Source limit."""
        usage = await self.usage(tenant_id, plan=plan)
        limit = usage.sources_limit
        if limit is not None and usage.sources_used >= limit:
            raise IngestionQuotaExceededError("source", usage.sources_used, limit, usage.plan)
        return usage


async def call_quota_check(enforcer: Any, tenant_id: str) -> None:
    """Invoke ``enforcer.check_doc_quota`` whether it is sync or async."""
    result = enforcer.check_doc_quota(tenant_id)
    if inspect.isawaitable(result):
        await result
