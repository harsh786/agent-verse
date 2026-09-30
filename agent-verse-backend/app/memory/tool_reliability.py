"""Per-tool reliability tracking — success rates and latency across all agent executions."""

from __future__ import annotations

from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Tools with at least this many calls and a success rate below the threshold
#: are "unreliable" (the executor deprioritises them, the API flags them).
DEFAULT_MIN_CALLS = 3
DEFAULT_MAX_SUCCESS_RATE = 0.7


class ToolReliabilityUnavailableError(RuntimeError):
    """The durable reliability store could not be read.

    Raised instead of returning an empty list: "no unreliable tools" and "the
    store is down" must not look the same to callers.
    """


def _stats(
    tool_name: str,
    success: int,
    failure: int,
    total_latency_ms: float,
    *,
    last_used_at: Any = None,
    blacklisted_at: Any = None,
    blacklist_reason: str | None = None,
    min_calls: int = DEFAULT_MIN_CALLS,
    max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
) -> dict[str, Any]:
    total = success + failure
    rate = success / total if total > 0 else 1.0
    return {
        "tool_name": tool_name,
        "success_count": success,
        "failure_count": failure,
        "total_calls": total,
        "success_rate": rate,
        "avg_latency_ms": total_latency_ms / total if total > 0 else 0.0,
        "last_used_at": last_used_at.isoformat() if hasattr(last_used_at, "isoformat") else None,
        "blacklisted": blacklisted_at is not None,
        "blacklist_reason": blacklist_reason,
        "unreliable": blacklisted_at is not None
        or (total >= min_calls and rate < max_success_rate),
    }


class ToolReliabilityStore:
    """Track per-tool success/failure rates and latency in PostgreSQL.

    Table: tool_reliability_memory
    Key: (tenant_id, tool_name)

    Counts come only from real tool executions (the agent executor records every
    MCP dispatch). A self-improvement "blacklist" is its own flag
    (``blacklisted_at``), never a synthetic failure count.

    The table is FORCE ROW LEVEL SECURITY. Every statement here runs for one
    known tenant (a goal's tool call, or a tenant's own API request), so each
    opens a transaction with the tenant GUC set and keeps its explicit
    ``tenant_id`` predicate as defense in depth.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        self._db = db_session_factory
        self._cache: dict[str, dict[str, Any]] = {}  # In-process cache (no-DB mode)

    def _cache_entry(self, tenant_id: str, tool_name: str) -> dict[str, Any]:
        return self._cache.setdefault(
            f"{tenant_id}:{tool_name}",
            {
                "tenant_id": tenant_id,
                "tool_name": tool_name,
                "success_count": 0,
                "failure_count": 0,
                "total_latency_ms": 0.0,
                "blacklisted_at": None,
                "blacklist_reason": None,
            },
        )

    async def record(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        success: bool,
        latency_ms: float = 0.0,
        error: str = "",
    ) -> None:
        """Record one real tool call outcome.

        A DB failure is logged at warning (a tool call must not fail because its
        telemetry could not be written) — it is never silently dropped.
        """
        if not tenant_id or not tool_name:
            return
        entry = self._cache_entry(tenant_id, tool_name)
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
            logger.warning(
                "tool_reliability_record_failed",
                tenant_id=tenant_id,
                tool_name=tool_name,
                success=success,
                error_class=error[:80],
                error=str(exc)[:200],
            )

    async def blacklist(self, *, tenant_id: str, tool_name: str, reason: str) -> None:
        """Flag a tool as blacklisted for this tenant without touching its counts."""
        if not tenant_id or not tool_name:
            return
        entry = self._cache_entry(tenant_id, tool_name)
        entry["blacklisted_at"] = "now"
        entry["blacklist_reason"] = reason
        if self._db is None:
            return
        from sqlalchemy import text

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                text("""
                INSERT INTO tool_reliability_memory
                    (tenant_id, tool_name, blacklisted_at, blacklist_reason)
                VALUES (:tid, :tool, NOW(), :reason)
                ON CONFLICT (tenant_id, tool_name) DO UPDATE SET
                    blacklisted_at = NOW(),
                    blacklist_reason = :reason
            """),
                {"tid": tenant_id, "tool": tool_name, "reason": reason[:200]},
            )

    async def get_reliability(self, *, tenant_id: str, tool_name: str) -> dict[str, Any]:
        """Get reliability stats for a specific tool.

        Raises :class:`ToolReliabilityUnavailableError` when the DB is wired but
        cannot be read.
        """
        if self._db is None:
            c = self._cache_entry(tenant_id, tool_name)
            return _stats(
                tool_name,
                c["success_count"],
                c["failure_count"],
                c["total_latency_ms"],
                blacklisted_at=c["blacklisted_at"],
                blacklist_reason=c["blacklist_reason"],
            )
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
                    SELECT success_count, failure_count, total_latency_ms, last_used_at,
                           blacklisted_at, blacklist_reason
                    FROM tool_reliability_memory
                    WHERE tenant_id = :tid AND tool_name = :tool
                """),
                        {"tid": tenant_id, "tool": tool_name},
                    )
                ).fetchone()
        except Exception as exc:
            logger.warning(
                "tool_reliability_get_failed", tenant_id=tenant_id, error=str(exc)[:200]
            )
            raise ToolReliabilityUnavailableError(str(exc)) from exc
        if not row:
            return _stats(tool_name, 0, 0, 0.0)
        return _stats(
            tool_name,
            int(row[0] or 0),
            int(row[1] or 0),
            float(row[2] or 0.0),
            last_used_at=row[3],
            blacklisted_at=row[4],
            blacklist_reason=row[5],
        )

    async def list_tools(
        self,
        *,
        tenant_id: str,
        min_calls: int = DEFAULT_MIN_CALLS,
        max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Every tool with recorded calls or a blacklist flag, least reliable first.

        Raises :class:`ToolReliabilityUnavailableError` on a DB failure.
        """
        rows: list[dict[str, Any]]
        if self._db is None:
            rows = [
                _stats(
                    c["tool_name"],
                    c["success_count"],
                    c["failure_count"],
                    c["total_latency_ms"],
                    blacklisted_at=c["blacklisted_at"],
                    blacklist_reason=c["blacklist_reason"],
                    min_calls=min_calls,
                    max_success_rate=max_success_rate,
                )
                for c in self._cache.values()
                if c["tenant_id"] == tenant_id
                and (c["success_count"] + c["failure_count"] > 0 or c["blacklisted_at"])
            ]
        else:
            try:
                from sqlalchemy import text

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    db_rows = (
                        await session.execute(
                            text("""
                        SELECT tool_name, success_count, failure_count, total_latency_ms,
                               last_used_at, blacklisted_at, blacklist_reason
                        FROM tool_reliability_memory
                        WHERE tenant_id = :tid
                          AND (success_count + failure_count > 0
                               OR blacklisted_at IS NOT NULL)
                        LIMIT :lim
                    """),
                            {"tid": tenant_id, "lim": limit},
                        )
                    ).fetchall()
            except Exception as exc:
                logger.warning(
                    "tool_reliability_list_failed", tenant_id=tenant_id, error=str(exc)[:200]
                )
                raise ToolReliabilityUnavailableError(str(exc)) from exc
            rows = [
                _stats(
                    r[0],
                    int(r[1] or 0),
                    int(r[2] or 0),
                    float(r[3] or 0.0),
                    last_used_at=r[4],
                    blacklisted_at=r[5],
                    blacklist_reason=r[6],
                    min_calls=min_calls,
                    max_success_rate=max_success_rate,
                )
                for r in db_rows
            ]
        rows.sort(key=lambda s: (not s["unreliable"], s["success_rate"], s["tool_name"]))
        return rows[:limit]

    async def get_unreliable_tools(
        self,
        *,
        tenant_id: str,
        min_calls: int = DEFAULT_MIN_CALLS,
        max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
    ) -> list[dict[str, Any]]:
        """Tools with poor reliability (or blacklisted) for agent planning awareness.

        Raises :class:`ToolReliabilityUnavailableError` on a DB failure.
        """
        tools = await self.list_tools(
            tenant_id=tenant_id, min_calls=min_calls, max_success_rate=max_success_rate
        )
        return [t for t in tools if t["unreliable"]]
