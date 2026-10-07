"""GDPR/SOC2/PCI-DSS compliance controls.

Provides:
- GDPR right-to-erasure: delete all tenant data
- GDPR right-of-access: export all tenant data
- Data residency: declare data location
- Retention sweep: delete records older than configured retention window
- SOC2: audit access logs
"""

from __future__ import annotations

import json
import logging
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.tenancy.context import TenantContext

warnings.warn(
    "app.enterprise.compliance is deprecated. Use app.enterprise.compliance_v2 instead. "
    "This module will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


# Grace period between an erasure request and its execution (GDPR art. 17 SLA
# is one month); the beat task only executes jobs whose scheduled_for has passed.
ERASURE_GRACE_DAYS = 30

# The synchronous export collects each section in one bounded query: a
# section with more rows than this fails the export (with a reason) instead
# of loading the whole table or truncating silently.
SYNC_EXPORT_MAX_ROWS = 10_000


class ExportNotRecordedError(RuntimeError):
    """A finished export could not be written to ``compliance_requests``.

    A ``ready`` export must be durable before its download link is handed out:
    other replicas resolve the link from Postgres only.
    """

# Postgres SQLSTATEs meaning "this table/column does not exist in this schema"
# (the ordered table list is a superset across deployments) — not a failure.
_NOT_APPLICABLE_SQLSTATES = frozenset({"42P01", "42703"})


def _classify_delete_error(exc: BaseException) -> str:
    """Return 'not_applicable', 'retained' (immutable by law, e.g. audit_log) or 'failed'."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    msg = str(exc).lower()
    if sqlstate in _NOT_APPLICABLE_SQLSTATES or "does not exist" in msg:
        return "not_applicable"
    if "immutable" in msg:
        return "retained"
    return "failed"


@dataclass
class DataExportRequest:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    status: str = "pending"  # pending | processing | ready | failed
    download_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)


def deployment_data_regions() -> tuple[str | None, str | None]:
    """``(primary, backup)`` data regions the operator declared for this
    deployment (``DATA_REGION`` / ``DATA_BACKUP_REGION``); None when unset."""
    from app.core.config import get_settings

    settings = get_settings()
    primary = str(getattr(settings, "data_region", "") or "").strip() or None
    backup = str(getattr(settings, "data_backup_region", "") or "").strip() or None
    return primary, (backup if backup != primary else None)


class ComplianceController:
    """GDPR/SOC2/PCI-DSS compliance controller.

    Export requests and deletion records are persisted to PostgreSQL
    (compliance_requests / deleted_tenants tables from migration 0026).
    Falls back to in-process dicts when DB is not configured.
    """

    def __init__(self) -> None:
        # In-memory fallbacks (dev / test mode without DB)
        self._export_requests: dict[str, DataExportRequest] = {}
        self._deleted_tenants: set[str] = set()
        self._deletion_jobs: dict[str, dict[str, Any]] = {}
        # Optional DB session factory injected via configure_services()
        self._db: Any = None
        # Optional service references
        self._goal_service: Any = None
        self._audit_log: Any = None
        self._tenant_service: Any = None
        self._agent_store: Any = None
        self._schedule_store: Any = None
        self._knowledge_store: Any = None

    def configure_services(
        self,
        *,
        goal_service: Any = None,
        audit_log: Any = None,
        tenant_service: Any = None,
        agent_store: Any = None,
        schedule_store: Any = None,
        knowledge_store: Any = None,
        db: Any = None,
    ) -> None:
        """Inject service references for comprehensive data export."""
        self._goal_service = goal_service
        self._audit_log = audit_log
        self._tenant_service = tenant_service
        self._agent_store = agent_store
        self._schedule_store = schedule_store
        self._knowledge_store = knowledge_store
        if db is not None:
            self._db = db

    # ── internal DB helpers ────────────────────────────────────────────────────

    async def _db_save_request(self, req: DataExportRequest, *, strict: bool = False) -> None:
        """Upsert *req* into ``compliance_requests`` (tenant RLS).

        Best-effort by default (logged). With *strict* a failed write raises
        :class:`ExportNotRecordedError`.
        """
        if self._db is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, req.tenant_id),
            ):
                await session.execute(
                    text(
                        """INSERT INTO compliance_requests
                           (request_id, tenant_id, status, download_url, payload, created_at)
                           VALUES (:rid, :tid, :status, :url, CAST(:payload AS jsonb), NOW())
                           ON CONFLICT (request_id) DO UPDATE
                             SET status = EXCLUDED.status,
                                 download_url = EXCLUDED.download_url,
                                 payload = EXCLUDED.payload"""
                    ),
                    {
                        "rid": req.request_id,
                        "tid": req.tenant_id,
                        "status": req.status,
                        "url": req.download_url,
                        "payload": json.dumps(req.payload),
                    },
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("compliance_request_save_failed: %s", exc)
            if strict:
                raise ExportNotRecordedError(
                    f"export {req.request_id} could not be recorded: {type(exc).__name__}"
                ) from exc

    async def _db_load_request(self, request_id: str, tenant_id: str) -> DataExportRequest | None:
        if self._db is None:
            return None
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # NOTE: compliance_requests has RLS FORCE'd (migration 0026/0767fe9d87bfe).
            # Every read/write here previously ran with no `app.tenant_id` GUC set at
            # all, so under any non-BYPASSRLS DB role (the least-privilege role this
            # app actually provisions in production and in integration tests) the
            # policy's `tenant_id = current_setting('app.tenant_id', TRUE)` check was
            # never satisfiable -- every call silently failed (caught below) and fell
            # back to the in-process dict, so GDPR export request state never
            # actually reached Postgres despite the class docstring's promise that it
            # does. Only invisible under a superuser/BYPASSRLS connection (RLS never
            # applies to those), which is why this went unnoticed.
            async with self._db() as session, sqlalchemy_rls_context(session, tenant_id):
                row = (
                    await session.execute(
                        text(
                            "SELECT request_id, tenant_id, status, download_url, payload, created_at "  # noqa: E501
                            "FROM compliance_requests "
                            "WHERE request_id = :rid AND tenant_id = :tid"
                        ),
                        {"rid": request_id, "tid": tenant_id},
                    )
                ).fetchone()
            if row is None:
                return None
            rid, tid, status, url, payload, created_at = row
            req = DataExportRequest(
                request_id=rid,
                tenant_id=tid,
                status=status,
                download_url=url or "",
                created_at=created_at.isoformat() if created_at else "",
                payload=payload if isinstance(payload, dict) else json.loads(payload or "{}"),
            )
            return req
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("compliance_request_load_failed: %s", exc)
            return None

    async def _db_save_deletion(self, tenant_id: str) -> None:
        """Record the tenant's own erasure request as a durable job row.

        ``deleted_tenants`` is a tenant-scoped table (one row per tenant, keyed by
        ``tenant_id``): this runs on the tenant's own request path, so it is
        written under that tenant's RLS context — never the maintenance role.
        The row is the job the ``process_tenant_erasures`` beat task executes once
        ``scheduled_for`` (request time + the 30-day grace period) has passed.

        A failure RAISES: this used to log a warning and let the endpoint answer
        ``deletion_scheduled: true`` for a request that was never recorded.
        """
        if self._db is None:
            return
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text(
                    "INSERT INTO deleted_tenants (tenant_id, requested_at, status, scheduled_for) "
                    "VALUES (:tid, NOW(), 'pending', NOW() + make_interval(days => :grace)) "
                    "ON CONFLICT (tenant_id) DO NOTHING"
                ),
                {"tid": tenant_id, "grace": ERASURE_GRACE_DAYS},
            )

    async def _db_load_deletion(self, tenant_id: str) -> dict[str, Any] | None:
        """Read the tenant's erasure job row (tenant RLS). Raises on DB failure."""
        if self._db is None:
            return None
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            row = (
                await session.execute(
                    text(
                        "SELECT status, requested_at, scheduled_for, attempts, last_error, "
                        "completed_at, result FROM deleted_tenants WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchone()
        if row is None:
            return None
        status, requested_at, scheduled_for, attempts, last_error, completed_at, result = row
        return {
            "tenant_id": tenant_id,
            "status": status,
            "requested_at": requested_at.isoformat() if requested_at else None,
            "scheduled_at": scheduled_for.isoformat() if scheduled_for else None,
            "attempts": int(attempts or 0),
            "last_error": last_error,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "legal_hold": status == "on_hold",
            "result": result if isinstance(result, dict) else None,
        }

    # ── public API ─────────────────────────────────────────────────────────────

    async def request_data_export(self, *, tenant_ctx: TenantContext) -> DataExportRequest:
        """GDPR right-of-access — collect every section, then report honestly.

        a09-F212-01: the request used to be ``ready`` before anything was
        collected; a goals DB error was only logged (export still ``ready`` with
        ``goals=[]``), audit entries were capped at 100, agents/schedules came
        from this replica's in-memory cache and ``knowledge_collections`` was
        always ``[]``. Now each section is read from Postgres when configured
        (tenant GUC + explicit ``tenant_id`` predicate, ``LIMIT`` one past
        :data:`SYNC_EXPORT_MAX_ROWS`), and the export is ``ready`` only when
        every section was collected in full. A failed or over-limit section makes
        it ``failed`` with the reason and no partial data.
        """
        tid = tenant_ctx.tenant_id
        req = DataExportRequest(tenant_id=tid, status="processing")
        sections: dict[str, list[dict[str, Any]]] = {}
        failures: dict[str, str] = {}
        collectors = (
            ("goals", self._export_goals),
            ("audit_entries", self._export_audit),
            ("agents", self._export_agents),
            ("schedules", self._export_schedules),
            ("knowledge_collections", self._export_knowledge_collections),
        )
        for name, collect in collectors:
            try:
                rows = await collect(tenant_ctx, SYNC_EXPORT_MAX_ROWS + 1)
            except Exception as exc:
                failures[name] = f"unavailable: {type(exc).__name__}: {str(exc)[:200]}"
                logging.getLogger(__name__).warning(
                    "compliance_export_section_failed: %s %s", name, failures[name]
                )
                continue
            if len(rows) > SYNC_EXPORT_MAX_ROWS:
                failures[name] = (
                    f"too_large: more than {SYNC_EXPORT_MAX_ROWS} rows; "
                    "use the asynchronous GDPR export"
                )
                continue
            sections[name] = rows

        exported_at = datetime.now(UTC).isoformat()
        if failures:
            req.status = "failed"
            req.payload = {
                "tenant_id": tid,
                "export_timestamp": exported_at,
                "error": "export incomplete: "
                + "; ".join(f"{k}: {v}" for k, v in failures.items()),
                "failed_sections": failures,
            }
        else:
            req.status = "ready"
            req.payload = {
                "tenant_id": tid,
                "plan": tenant_ctx.plan.value,
                "export_timestamp": exported_at,
                "export_format_version": "1.0",
                "data": {
                    "tenant_profile": {"tenant_id": tid, "plan": tenant_ctx.plan.value},
                    "api_keys": [],  # Never export raw keys
                    **sections,
                },
            }
            req.download_url = f"/compliance/export/{req.request_id}/download"

        # A ready export is recorded before its download link is handed out:
        # other replicas resolve the link from Postgres only, so an unrecorded
        # "ready" answer would 404 there (salvage RV-08). A failed export has no
        # link to break and is still reported when it cannot be recorded.
        await self._db_save_request(req, strict=req.status == "ready")
        self._export_requests[req.request_id] = req
        return req

    async def _export_select(
        self, db: Any, tenant_id: str, sql: str, limit: int
    ) -> list[Any]:
        """Run one tenant-scoped, bounded export SELECT. Raises on any DB error."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            result = await session.execute(text(sql), {"tid": tenant_id, "lim": limit})
            return list(result.fetchall())

    @staticmethod
    def _iso(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value or "")

    async def _export_goals(self, tenant_ctx: TenantContext, limit: int) -> list[dict[str, Any]]:
        svc = self._goal_service
        db = getattr(svc, "_db_session_factory", None) if svc is not None else None
        db = db or self._db
        if db is not None:
            rows = await self._export_select(
                db,
                tenant_ctx.tenant_id,
                "SELECT id, goal_text, status, created_at FROM goals "
                "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT :lim",
                limit,
            )
            return [
                {
                    "goal_id": r[0],
                    "goal_text": r[1],
                    "status": r[2],
                    "created_at": self._iso(r[3]),
                }
                for r in rows
            ]
        if svc is None:
            return []
        out: list[dict[str, Any]] = []
        records: dict[str, Any] = getattr(svc, "_goals", {})
        for gid, record in records.items():
            if getattr(record, "tenant_id", "") != tenant_ctx.tenant_id:
                continue
            out.append(
                {
                    "goal_id": gid,
                    "goal_text": getattr(record, "goal_text", ""),
                    "status": str(getattr(record, "status", "")),
                    "created_at": getattr(record, "created_at", ""),
                }
            )
            if len(out) >= limit:
                break
        return out

    async def _export_audit(self, tenant_ctx: TenantContext, limit: int) -> list[dict[str, Any]]:
        if self._db is not None:
            rows = await self._export_select(
                self._db,
                tenant_ctx.tenant_id,
                "SELECT id, goal_id, tool_name, outcome, created_at FROM audit_log "
                "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT :lim",
                limit,
            )
            return [
                {
                    "event_id": r[0],
                    "goal_id": r[1],
                    "tool_name": r[2],
                    "outcome": r[3],
                    "created_at": self._iso(r[4]),
                }
                for r in rows
            ]
        if self._audit_log is None:
            return []
        entries = self._audit_log.query(tenant_ctx=tenant_ctx, limit=limit)
        return [
            {
                "event_id": e.event_id,
                "goal_id": e.goal_id,
                "tool_name": e.tool_name,
                "outcome": e.outcome,
            }
            for e in entries
        ]

    async def _export_agents(self, tenant_ctx: TenantContext, limit: int) -> list[dict[str, Any]]:
        if self._db is not None:
            # Soft-deleted (inactive) agents still hold the tenant's data.
            rows = await self._export_select(
                self._db,
                tenant_ctx.tenant_id,
                "SELECT id, name, is_active, created_at FROM agents "
                "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT :lim",
                limit,
            )
            return [
                {
                    "agent_id": r[0],
                    "name": r[1],
                    "is_active": bool(r[2]),
                    "created_at": self._iso(r[3]),
                }
                for r in rows
            ]
        if self._agent_store is None:
            return []
        agents = self._agent_store.list_all(tenant_ctx=tenant_ctx)
        return [{"agent_id": a.get("agent_id"), "name": a.get("name")} for a in agents[:limit]]

    async def _export_schedules(
        self, tenant_ctx: TenantContext, limit: int
    ) -> list[dict[str, Any]]:
        if self._db is not None:
            rows = await self._export_select(
                self._db,
                tenant_ctx.tenant_id,
                "SELECT id, agent_id, trigger_type, cron_expression, description, created_at "
                "FROM schedules WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT :lim",
                limit,
            )
            return [
                {
                    "schedule_id": r[0],
                    "agent_id": r[1],
                    "trigger_type": r[2],
                    "cron_expression": r[3],
                    "description": r[4],
                    "created_at": self._iso(r[5]),
                }
                for r in rows
            ]
        if self._schedule_store is None:
            return []
        schedules = self._schedule_store.list_all(tenant_ctx=tenant_ctx)
        return [
            {"schedule_id": s.get("schedule_id"), "goal_id": s.get("goal_id")}
            for s in schedules[:limit]
        ]

    async def _export_knowledge_collections(
        self, tenant_ctx: TenantContext, limit: int
    ) -> list[dict[str, Any]]:
        if self._db is not None:
            rows = await self._export_select(
                self._db,
                tenant_ctx.tenant_id,
                "SELECT id, name, description, document_count, created_at "
                "FROM knowledge_collections WHERE tenant_id = :tid "
                "ORDER BY created_at DESC LIMIT :lim",
                limit,
            )
            return [
                {
                    "collection_id": r[0],
                    "name": r[1],
                    "description": r[2] or "",
                    "document_count": int(r[3] or 0),
                    "created_at": self._iso(r[4]),
                }
                for r in rows
            ]
        if self._knowledge_store is None:
            return []
        collections = self._knowledge_store.list_collections(tenant_ctx=tenant_ctx)
        return [
            {
                "collection_id": c.collection_id,
                "name": c.name,
                "description": getattr(c, "description", "") or "",
                "document_count": int(getattr(c, "document_count", 0) or 0),
            }
            for c in list(collections)[:limit]
        ]

    async def get_export_status(
        self, *, request_id: str, tenant_ctx: TenantContext
    ) -> DataExportRequest | None:
        # DB first
        if self._db is not None:
            req = await self._db_load_request(request_id, tenant_ctx.tenant_id)
            if req is not None:
                self._export_requests[request_id] = req  # refresh cache
                return req
        # In-memory fallback
        req = self._export_requests.get(request_id)
        if req is None or req.tenant_id != tenant_ctx.tenant_id:
            return None
        return req

    async def request_data_deletion(self, *, tenant_ctx: TenantContext) -> dict[str, Any]:
        """GDPR right-to-erasure: record a durable erasure job due in 30 days.

        With a database the job row is what ``process_tenant_erasures`` (Celery
        beat) executes — previously nothing ever executed it. The response
        reflects the stored job (an existing request is not reset). Without a
        database (dev/tests) the request is only tracked in-process.
        """
        tid = tenant_ctx.tenant_id
        if self._db is not None:
            await self._db_save_deletion(tid)
            job = await self._db_load_deletion(tid)
            if job is None:  # pragma: no cover - insert committed but row not visible
                raise RuntimeError("erasure request was not recorded")
        else:
            self._deleted_tenants.add(tid)
            now = datetime.now(UTC)
            job = self._deletion_jobs.setdefault(
                tid,
                {
                    "tenant_id": tid,
                    "status": "pending",
                    "requested_at": now.isoformat(),
                    "scheduled_at": (now + timedelta(days=ERASURE_GRACE_DAYS)).isoformat(),
                    "attempts": 0,
                    "last_error": None,
                    "completed_at": None,
                    "legal_hold": False,
                    "result": None,
                },
            )
        return {
            **job,
            "deletion_scheduled": job["status"] != "completed",
            "note": (
                f"Data will be permanently deleted {ERASURE_GRACE_DAYS} days after the "
                "request per GDPR article 17, unless a legal hold applies. Track progress "
                "at GET /enterprise/compliance/delete."
            ),
        }

    async def get_deletion_status(self, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        """Return the tenant's erasure job as stored (DB authoritative)."""
        if self._db is not None:
            return await self._db_load_deletion(tenant_ctx.tenant_id)
        job = self._deletion_jobs.get(tenant_ctx.tenant_id)
        return dict(job) if job is not None else None

    def retention_sweep(self, *, retention_days: int = 90) -> dict[str, Any]:
        """Sweep and mark records older than retention_days for deletion."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        swept = [
            req.request_id
            for req in self._export_requests.values()
            if datetime.fromisoformat(req.created_at) < cutoff
        ]
        return {
            "sweep_cutoff": cutoff.isoformat(),
            "records_swept": len(swept),
            "retention_days": retention_days,
        }

    async def get_export_payload(
        self, *, request_id: str, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Return the raw export payload dict for a ready export request."""
        req = await self.get_export_status(request_id=request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != "ready":
            return None
        return req.payload

    def get_data_residency(self, *, tenant_ctx: TenantContext) -> dict[str, Any]:
        # FIX: No longer returns hardcoded gdpr_compliant=True / soc2_type2=True.
        # These are compliance assertions that must be earned, not assumed.
        # Use ComplianceChecker (compliance_v2.py) for authoritative compliance status.
        #
        # a10-F253-01: the regions are the operator-declared DATA_REGION /
        # DATA_BACKUP_REGION of this deployment — they were hardcoded us-east-1 /
        # eu-west-1 for every tenant. Nothing selects a region per tenant.
        primary, backup = deployment_data_regions()
        if primary:
            description = (
                f"All tenants of this deployment store data in {primary}"
                + (f" (backups in {backup})" if backup else "")
                + "; per-tenant region selection is not available."
            )
        else:
            description = (
                "This deployment has not declared its data region (DATA_REGION); "
                "per-tenant region selection is not available."
            )
        return {
            "tenant_id": tenant_ctx.tenant_id,
            # ``region`` is what the frontend's DataResidencyInfo reads.
            "region": primary or "unconfigured",
            "primary_region": primary,
            "backup_region": backup,
            "residency_configured": primary is not None,
            "per_tenant_residency": False,
            "description": description,
            "gdpr_compliant": False,  # FIX: was hardcoded True — dynamically checked via /compliance/gdpr  # noqa: E501
            "pci_dss_scope": False,
            "soc2_type2": False,  # FIX: was hardcoded True — dynamically checked via /compliance/soc2  # noqa: E501
            "note": "Use GET /enterprise/compliance/{framework} for authoritative compliance status.",  # noqa: E501
        }

    async def execute_data_deletion_async(
        self, *, tenant_ctx: TenantContext, db: Any
    ) -> dict[str, Any]:
        """Execute GDPR erasure — actual DB deletion. Called 30 days after request.

        Run by the ``process_tenant_erasures`` beat task. An active legal hold on
        the tenant BLOCKS the erasure entirely (nothing is deleted; the result
        carries ``blocked="legal_hold"``) — the hold check fails closed. The
        result's ``complete`` flag is False when any table failed for a reason
        other than "not present in this schema" / "retained by law", so the job
        is retried instead of being reported done.
        """
        if db is None:
            return {"error": "No database configured", "deleted_rows": 0, "complete": False}

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.governance.legal_holds import LegalHoldManager

        if await LegalHoldManager(redis=None, db_factory=db).has_active_hold(
            tenant_ctx.tenant_id
        ):
            return {
                "tenant_id": tenant_ctx.tenant_id,
                "blocked": "legal_hold",
                "complete": False,
                "total_rows_deleted": 0,
                "tables": {},
            }

        deleted_counts: dict[str, Any] = {}
        failed_tables: list[str] = []
        retained_tables: list[str] = []
        tables_ordered = [
            # Child tables first (FK constraints)
            "goal_events",
            "goal_checkpoints",
            "goal_steps",
            "decision_traces",
            "evaluations",
            "cost_ledger",
            "audit_log",
            "approval_requests",
            "governance_policies",
            "collab_operations",
            "collab_sessions",
            "documents",
            "knowledge_collections",
            "goal_connector_usage",
            "workspace_files",
            "workspace_usage",
            "mcp_builtin_provisioning",
            "mcp_credentials",
            "oauth_tokens",
            "mcp_servers",
            "execution_memory",
            "long_term_memory",
            "agent_snapshots",
            "agent_permissions",
            "agents",
            "schedules",
            "compliance_requests",
            "goals",
            "api_keys",
            # Parent last
            "tenants",
        ]

        # NOTE: several of these tables (decision_traces, tool_capabilities,
        # compliance_requests, agent_snapshots, ...) have RLS FORCE'd. Without
        # the `app.tenant_id` GUC set on this session, every DELETE below would
        # silently match zero rows under a non-BYPASSRLS role (the
        # least-privilege role this app actually provisions in production) --
        # GDPR erasure would report "success" while deleting nothing. Only
        # invisible under a superuser/BYPASSRLS connection, which is why this
        # went unnoticed.
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            for table in tables_ordered:
                # Each table's DELETE runs in its own SAVEPOINT. Postgres aborts
                # the *entire* enclosing transaction on any error (permission
                # denied, constraint violation, etc.) until a ROLLBACK — without
                # a savepoint here, one table's failure silently poisons every
                # subsequent table's DELETE in this loop too (each one then
                # raises "current transaction is aborted" and gets swallowed by
                # the except below), so a single mid-list failure would make
                # this function report a string of independent-looking
                # "skipped: ..." entries while actually having deleted nothing
                # for the rest of the tenant's data.
                try:
                    async with session.begin_nested():
                        # Use tenant_id column — all tables have it
                        # tenants uses id column
                        col = "id" if table == "tenants" else "tenant_id"
                        result = await session.execute(
                            text(f"DELETE FROM {table} WHERE {col} = :tid"),
                            {"tid": tenant_ctx.tenant_id},
                        )
                        deleted_counts[table] = result.rowcount
                except Exception as exc:
                    deleted_counts[table] = f"skipped: {exc}"
                    kind = _classify_delete_error(exc)
                    if kind == "failed":
                        failed_tables.append(table)
                    elif kind == "retained":
                        retained_tables.append(table)

        # The deleted_tenants row is NOT removed any more: it is the durable job
        # record (status/result) and the proof that the erasure ran. It holds no
        # personal data — only the opaque tenant id.
        total = sum(v for v in deleted_counts.values() if isinstance(v, int))
        return {
            "tenant_id": tenant_ctx.tenant_id,
            "deleted_at": datetime.now(UTC).isoformat(),
            "total_rows_deleted": total,
            "tables": deleted_counts,
            "failed_tables": failed_tables,
            "retained_tables": retained_tables,
            "complete": not failed_tables,
        }
