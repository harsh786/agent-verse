"""Per-tool reliability tracking — success rates and latency across all agent executions."""

from __future__ import annotations

from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)


class ToolReliabilityStore:
    """Track per-tool success/failure rates and latency in PostgreSQL.

    Table: tool_reliability_memory
    Key: (tenant_id, tool_name)

    The table is FORCE ROW LEVEL SECURITY. Every statement here runs for one
    known tenant (a goal's tool call, or a tenant's own API request), so each
    opens a transaction with the tenant GUC set and keeps its explicit
    ``tenant_id`` predicate as defense in depth. Without the GUC, a NOBYPASSRLS
    application role rejects every upsert and reads back zero rows — both of
    which the broad ``except`` blocks below would otherwise hide.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        self._db = db_session_factory
        self._cache: dict[str, dict] = {}  # In-process cache

    async def record(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        success: bool,
        latency_ms: float = 0.0,
        error: str = "",
    ) -> None:
        """Record a tool call outcome."""
        key = f"{tenant_id}:{tool_name}"
        entry = self._cache.setdefault(
            key,
            {
                "tool_name": tool_name,
                "success_count": 0,
                "failure_count": 0,
                "total_latency_ms": 0.0,
            },
        )
        if success:
            entry["success_count"] += 1
        else:
            entry["failure_count"] += 1
        entry["total_latency_ms"] += latency_ms

        if self._db is None:
            return
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                    INSERT INTO tool_reliability_memory
                        (tenant_id, tool_name, success_count, failure_count,
                         total_latency_ms, last_used_at)
                    VALUES (:tid, :tool, :sc, :fc, :lat, NOW())
                    ON CONFLICT (tenant_id, tool_name) DO UPDATE SET
                        success_count = tool_reliability_memory.success_count + :sc,
                        failure_count = tool_reliability_memory.failure_count + :fc,
                        total_latency_ms = tool_reliability_memory.total_latency_ms + :lat,
                        last_used_at = NOW()
                """),
                    {
                        "tid": tenant_id,
                        "tool": tool_name,
                        "sc": 1 if success else 0,
                        "fc": 0 if success else 1,
                        "lat": latency_ms,
                    },
                )
        except Exception as exc:
            logger.debug("tool_reliability_record_failed", error=str(exc))

    async def get_reliability(self, *, tenant_id: str, tool_name: str) -> dict:
        """Get reliability stats for a specific tool."""
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    row = (
                        await session.execute(
                            text("""
                        SELECT success_count, failure_count, total_latency_ms, last_used_at
                        FROM tool_reliability_memory
                        WHERE tenant_id = :tid AND tool_name = :tool
                    """),
                            {"tid": tenant_id, "tool": tool_name},
                        )
                    ).fetchone()
                if row:
                    total = (row[0] or 0) + (row[1] or 0)
                    return {
                        "tool_name": tool_name,
                        "success_count": row[0] or 0,
                        "failure_count": row[1] or 0,
                        "success_rate": row[0] / total if total > 0 else 1.0,
                        "avg_latency_ms": (row[2] or 0) / total if total > 0 else 0.0,
                        "last_used_at": row[3].isoformat() if row[3] else None,
                    }
            except Exception as exc:
                logger.debug("tool_reliability_get_failed", error=str(exc))

        key = f"{tenant_id}:{tool_name}"
        cached = self._cache.get(key, {})
        total = cached.get("success_count", 0) + cached.get("failure_count", 0)
        return {
            "tool_name": tool_name,
            "success_count": cached.get("success_count", 0),
            "failure_count": cached.get("failure_count", 0),
            "success_rate": cached["success_count"] / total if total > 0 else 1.0,
            "avg_latency_ms": cached.get("total_latency_ms", 0) / total if total > 0 else 0.0,
            "last_used_at": None,
        }

    async def get_unreliable_tools(
        self, *, tenant_id: str, min_calls: int = 5, max_success_rate: float = 0.7
    ) -> list[dict]:
        """Get tools with poor reliability for agent planning awareness."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                    SELECT tool_name, success_count, failure_count,
                           success_count * 1.0 / NULLIF(success_count + failure_count, 0) as rate
                    FROM tool_reliability_memory
                    WHERE tenant_id = :tid
                      AND success_count + failure_count >= :min_calls
                      AND success_count * 1.0 / NULLIF(success_count + failure_count, 0) < :max_rate
                    ORDER BY rate ASC
                    LIMIT 10
                """),
                        {"tid": tenant_id, "min_calls": min_calls, "max_rate": max_success_rate},
                    )
                ).fetchall()
            return [
                {
                    "tool_name": r[0],
                    "success_count": r[1],
                    "failure_count": r[2],
                    "total_calls": (r[1] or 0) + (r[2] or 0),
                    "success_rate": float(r[3] or 0),
                }
                for r in rows
            ]
        except Exception:
            return []
