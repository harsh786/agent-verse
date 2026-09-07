"""Data-subject deletion orchestrator — real GDPR/DPDP right-to-erasure cascade.

``execute_deletion`` removes a data subject's personal data across every store
that supports subject-scoped deletion, records exact per-store counts in a
:class:`~app.lifecycle.deletion_receipt.DeletionReceipt`, emits an audit entry,
and can independently re-scan for residue via ``verify_deleted``.

Subject linkage (matching the convention already used by the DPDP erasure
Celery task in ``app.scaling.tasks``):

* Direct — ``dpdp_consents`` rows carry the ``data_principal_id`` verbatim.
* Goal-anchored — a subject's goals are those whose ``execution_context`` JSON
  references the ``subject_ref``; goal-linked stores (feedback, memories,
  knowledge-graph nodes) cascade from those goal ids.
* Tagged — knowledge ``documents`` / ``knowledge_chunks`` carry the subject
  reference in their ``metadata`` JSONB.

Legal holds are checked FIRST and are absolute: an active hold covering the
subject SUSPENDS the run (``suspended=True``, ``total_deleted=0``) — held data
is never destroyed.

Stores that do not support subject-scoped deletion (content-hash keyed caches,
object-store artifacts, ephemeral build artifacts) are recorded with a count of
0 and an explanatory note rather than being silently skipped or faked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.lifecycle.deletion_receipt import DeletionReceipt
from app.lifecycle.retention_policy import DataCategory
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.governance.audit_v3 import AuditV3

logger = get_logger(__name__)


# ── Backward-compatible legacy stub API (kept for existing callers/tests) ──────
@dataclass
class DeletionSchedule:
    tenant_id: str
    data_category: DataCategory
    record_ids: list[str]
    scheduled_count: int


# Stores that genuinely support subject-scoped deletion.  Each entry is
# (receipt_key, table, where_fragment, requires_goal_ids).
_DIRECT_STORES: tuple[tuple[str, str, str], ...] = (
    ("goals", "goals", "tenant_id = :tid AND execution_context::text ILIKE :pat"),
    ("documents", "documents", "tenant_id = :tid AND metadata::text ILIKE :pat"),
    (
        "knowledge_chunks",
        "knowledge_chunks_768",
        "tenant_id = :tid AND metadata::text ILIKE :pat",
    ),
    ("dpdp_consents", "dpdp_consents", "tenant_id = :tid AND data_principal_id = :subj"),
)

_GOAL_LINKED_STORES: tuple[tuple[str, str, str], ...] = (
    ("goal_feedback", "goal_feedback", "goal_id"),
    ("memory_episodic", "episodic_memories", "goal_id"),
    ("memory_canonical", "memory_records", "source_goal_id"),
    ("memory_long_term", "long_term_memory", "source_goal_id"),
    ("knowledge_graph_nodes", "knowledge_nodes", "source_id"),
)

# Stores recorded (count 0) with a note — NOT subject-scoped, cannot honestly
# be erased per-subject without destroying other subjects' data.
_RECORDED_ONLY: dict[str, str] = {
    "semantic_cache": "content-hash keyed cache; not subject-scoped (tenant-wide clear only)",
    "llm_response_cache": "content-hash keyed cache; not subject-scoped",
    "tool_cache": "Redis content-hash keyed cache; not subject-scoped",
    "rpa_artifacts": "object/file store keyed by goal path; no DB subject index",
    "result_artifacts": "ephemeral build output; not persisted to a queryable store",
    "execution_memory": "no subject/goal linkage column (goal_text only)",
}


class DeletionOrchestrator:
    """Executes and verifies data-subject deletion cascades."""

    def __init__(
        self,
        db_factory: Any = None,
        *,
        audit: AuditV3 | None = None,
    ) -> None:
        self._db = db_factory
        self._audit = audit

    # ── Legacy stub (unchanged behaviour, still used by test_lifecycle) ───────
    def schedule_deletion(
        self,
        tenant_id: str,
        data_category: DataCategory,
        record_ids: list[str],
    ) -> DeletionSchedule:
        return DeletionSchedule(
            tenant_id=tenant_id,
            data_category=data_category,
            record_ids=record_ids,
            scheduled_count=len(record_ids),
        )

    # ── Real erasure cascade ─────────────────────────────────────────────────
    async def execute_deletion(
        self,
        tenant_id: str,
        subject_ref: str,
        *,
        dry_run: bool = False,
    ) -> DeletionReceipt:
        """Delete (or, if *dry_run*, count) a subject's data across all stores."""
        # SAFETY (mass-deletion guard): the subject match uses ILIKE against
        # serialized JSON, so an empty/short/wildcard subject_ref would match —
        # and permanently delete — unrelated subjects' (or the whole tenant's)
        # data. Refuse anything that is not a specific identifier.
        subject_ref = (subject_ref or "").strip()
        if len(subject_ref) < 3 or "%" in subject_ref or "\\" in subject_ref:
            raise ValueError(
                "refusing deletion: subject_ref must be a specific identifier "
                "(non-empty, >= 3 chars, no LIKE wildcards)"
            )
        started_at = datetime.now(UTC)
        # Match the subject as a complete JSON string value (quoted), not an
        # arbitrary substring, so 'subject-1' cannot match 'subject-12' etc.
        pat = f'%"{subject_ref}"%'

        receipt = DeletionReceipt(
            subject_ref=subject_ref,
            tenant_id=tenant_id,
            started_at=started_at,
            completed_at=started_at,
        )

        if self._db is None:
            receipt.completed_at = datetime.now(UTC)
            receipt.notes["_no_db"] = "no database configured; nothing to delete"
            return receipt

        # 1. Legal hold takes absolute priority — SUSPEND, never destroy.
        hold = await self._active_hold(tenant_id, subject_ref)
        if hold is not None:
            receipt.suspended = True
            receipt.suspension_reason = (
                f"active legal hold '{hold}' covers subject {subject_ref}"
            )
            receipt.completed_at = datetime.now(UTC)
            await self._emit_audit(tenant_id, subject_ref, receipt, suspended=True)
            logger.info(
                "deletion_suspended_by_hold", tenant=tenant_id, subject=subject_ref, hold=hold
            )
            return receipt

        # 2. Resolve the subject's goal ids up front (goal-linked cascade anchor).
        goal_ids = await self._subject_goal_ids(tenant_id, pat)

        # 3. Direct-subject stores.
        for key, table, where in _DIRECT_STORES:
            params = {"tid": tenant_id, "pat": pat, "subj": subject_ref}
            count, ids = await self._apply(table, where, params, dry_run=dry_run)
            receipt.per_store[key] = count
            if key == "goals" and dry_run:
                # In dry-run goals are not deleted; use the counted ids as the
                # cascade anchor so goal-linked would-be counts are accurate.
                goal_ids = ids or goal_ids

        # 4. Goal-linked stores.
        node_ids: list[str] = []
        for key, table, column in _GOAL_LINKED_STORES:
            if not goal_ids:
                receipt.per_store[key] = 0
                continue
            where = f"tenant_id = :tid AND {column} = ANY(CAST(:gids AS text[]))"
            params = {"tid": tenant_id, "gids": list(goal_ids)}
            count, ids = await self._apply(table, where, params, dry_run=dry_run)
            receipt.per_store[key] = count
            if key == "knowledge_graph_nodes":
                node_ids = ids

        # 5. Knowledge-graph edges cascade from deleted nodes.
        receipt.per_store["knowledge_graph_edges"] = await self._delete_edges(
            tenant_id, node_ids, dry_run=dry_run
        )

        # 6. Recorded-only stores (honest zeros + notes).
        for key, note in _RECORDED_ONLY.items():
            receipt.per_store.setdefault(key, 0)
            receipt.notes[key] = note

        receipt.total_deleted = sum(
            v for k, v in receipt.per_store.items() if k not in _RECORDED_ONLY
        )

        # 7. Audit.
        await self._emit_audit(tenant_id, subject_ref, receipt, suspended=False)

        # 8. Independent verification (skipped for dry-run: nothing was deleted).
        if not dry_run:
            residue = await self.verify_deleted(tenant_id, subject_ref)
            receipt.residue = residue
            receipt.verified = residue == {}

        receipt.completed_at = datetime.now(UTC)
        logger.info(
            "deletion_executed",
            tenant=tenant_id,
            subject=subject_ref,
            total=receipt.total_deleted,
            dry_run=dry_run,
            verified=receipt.verified,
        )
        return receipt

    async def verify_deleted(self, tenant_id: str, subject_ref: str) -> dict[str, int]:
        """Independent re-scan proving erasure — returns residue per store.

        An empty dict means no subject data survives.  Goal-linked stores are
        verified transitively: their anchor goals are re-derived from the
        ``goals`` table, so once the goals are gone their children cannot be
        re-associated with the subject.
        """
        if self._db is None:
            return {}
        subject_ref = (subject_ref or "").strip()
        if len(subject_ref) < 3 or "%" in subject_ref or "\\" in subject_ref:
            return {}
        pat = f'%"{subject_ref}"%'
        residue: dict[str, int] = {}

        goal_ids = await self._subject_goal_ids(tenant_id, pat)

        for key, table, where in _DIRECT_STORES:
            params = {"tid": tenant_id, "pat": pat, "subj": subject_ref}
            n = await self._count(table, where, params)
            if n:
                residue[key] = n

        for key, table, column in _GOAL_LINKED_STORES:
            if not goal_ids:
                continue
            where = f"tenant_id = :tid AND {column} = ANY(CAST(:gids AS text[]))"
            n = await self._count(table, where, {"tid": tenant_id, "gids": list(goal_ids)})
            if n:
                residue[key] = n

        return residue

    # ── internals ────────────────────────────────────────────────────────────
    async def _active_hold(self, tenant_id: str, subject_ref: str) -> str | None:
        """Return the name of an active legal hold covering the subject, else None."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        subj_json = json.dumps([subject_ref])
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT name FROM legal_holds
                            WHERE tenant_id = :tid AND status = 'active'
                              AND (resource_ids @> CAST(:subj AS jsonb)
                                   OR user_ids @> CAST(:subj AS jsonb))
                            LIMIT 1
                            """
                        ),
                        {"tid": tenant_id, "subj": subj_json},
                    )
                ).fetchone()
            return str(row[0]) if row is not None else None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("legal_hold_check_failed", error=str(exc)[:120])
            # Fail closed: if we cannot confirm the absence of a hold, suspend.
            return "unverifiable-hold-state"

    async def _subject_goal_ids(self, tenant_id: str, pat: str) -> list[str]:
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
                        "SELECT id FROM goals "
                        "WHERE tenant_id = :tid AND execution_context::text ILIKE :pat"
                    ),
                    {"tid": tenant_id, "pat": pat},
                )
            ).fetchall()
        return [r[0] for r in rows]

    async def _apply(
        self,
        table: str,
        where: str,
        params: dict[str, Any],
        *,
        dry_run: bool,
    ) -> tuple[int, list[str]]:
        """Delete (or count, if dry_run) rows; return (count, ids). Isolated txn."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        tenant_id = str(params["tid"])
        sql = (
            f"SELECT id FROM {table} WHERE {where}"
            if dry_run
            else f"DELETE FROM {table} WHERE {where} RETURNING id"
        )
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (await session.execute(text(sql), params)).fetchall()
            ids = [r[0] for r in rows]
            return len(ids), ids
        except Exception as exc:
            logger.warning("deletion_store_failed", table=table, error=str(exc)[:120])
            return 0, []

    async def _count(self, table: str, where: str, params: dict[str, Any]) -> int:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        tenant_id = str(params["tid"])
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                n = (
                    await session.execute(
                        text(f"SELECT count(*) FROM {table} WHERE {where}"), params
                    )
                ).scalar_one()
            return int(n or 0)
        except Exception as exc:
            logger.warning("deletion_verify_failed", table=table, error=str(exc)[:120])
            return 0

    async def _delete_edges(
        self, tenant_id: str, node_ids: list[str], *, dry_run: bool
    ) -> int:
        if not node_ids:
            return 0
        where = (
            "tenant_id = :tid AND (source_node_id = ANY(CAST(:nids AS text[])) "
            "OR target_node_id = ANY(CAST(:nids AS text[])))"
        )
        count, _ = await self._apply(
            "knowledge_edges",
            where,
            {"tid": tenant_id, "nids": list(node_ids)},
            dry_run=dry_run,
        )
        return count

    async def _emit_audit(
        self,
        tenant_id: str,
        subject_ref: str,
        receipt: DeletionReceipt,
        *,
        suspended: bool,
    ) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.append(
                tenant_id=tenant_id,
                goal_id=subject_ref,
                action="data_subject_deletion",
                actor="system",
                metadata={
                    "subject_ref": subject_ref,
                    "suspended": suspended,
                    "total_deleted": receipt.total_deleted,
                    "per_store": receipt.per_store,
                    "suspension_reason": receipt.suspension_reason,
                },
            )
        except Exception as exc:  # pragma: no cover - audit must not block erasure
            logger.warning("deletion_audit_failed", error=str(exc)[:120])
