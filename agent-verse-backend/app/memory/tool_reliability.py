"""Per-tool reliability tracking — success rates and latency across all agent executions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger

logger = get_logger(__name__)

#: Tools with at least this many (decayed) calls and a success rate below the
#: threshold are "unreliable" (the executor deprioritises them, the API flags them).
DEFAULT_MIN_CALLS = 3
DEFAULT_MAX_SUCCESS_RATE = 0.7
#: Half-life of the decayed outcome counters the verdict uses (MEM-45): an
#: outcome a week old weighs half as much as one just recorded, so a tool that
#: failed long ago and succeeds now becomes reliable again.
DECAY_HALF_LIFE = timedelta(days=7)
#: A self-improvement blacklist lapses after this long (it used to be permanent).
BLACKLIST_TTL = timedelta(days=7)


class ToolReliabilityUnavailableError(RuntimeError):
    """The durable reliability store could not be read.

    Raised instead of returning an empty list: "no unreliable tools" and "the
    store is down" must not look the same to callers.
    """


def _decay_factor(since: Any, now: datetime) -> float:
    if not isinstance(since, datetime):
        return 1.0
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    age = max(0.0, (now - since).total_seconds())
    return float(0.5 ** (age / DECAY_HALF_LIFE.total_seconds()))


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _stats(
    tool_name: str,
    success: int,
    failure: int,
    total_latency_ms: float,
    *,
    last_used_at: Any = None,
    blacklisted_at: Any = None,
    blacklist_reason: str | None = None,
    blacklist_expires_at: Any = None,
    recent_success: float | None = None,
    recent_failure: float | None = None,
    decayed_at: Any = None,
    now: datetime | None = None,
    min_calls: int = DEFAULT_MIN_CALLS,
    max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    total = success + failure
    rate = success / total if total > 0 else 1.0
    # The verdict uses the decayed counters (lifetime counts when a row has
    # none yet), aged to now.
    factor = _decay_factor(decayed_at, current)
    eff_s = (success if recent_success is None else recent_success) * factor
    eff_f = (failure if recent_failure is None else recent_failure) * factor
    eff_total = eff_s + eff_f
    recent_rate = eff_s / eff_total if eff_total > 0 else 1.0
    expires = blacklist_expires_at
    if blacklisted_at is not None and expires is None and isinstance(blacklisted_at, datetime):
        expires = blacklisted_at + BLACKLIST_TTL
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    blacklisted = blacklisted_at is not None and (
        not isinstance(expires, datetime) or expires > current
    )
    return {
        "tool_name": tool_name,
        "success_count": success,
        "failure_count": failure,
        "total_calls": total,
        "success_rate": rate,
        "recent_success_rate": recent_rate,
        "recent_calls": round(eff_total, 3),
        "avg_latency_ms": total_latency_ms / total if total > 0 else 0.0,
        "last_used_at": _iso(last_used_at),
        "blacklisted": blacklisted,
        "blacklist_reason": blacklist_reason if blacklisted else None,
        "blacklist_expires_at": _iso(expires) if blacklisted else None,
        "unreliable": blacklisted
        # (epsilon: calls recorded a moment ago have decayed by ~1e-12)
        or (eff_total + 1e-6 >= min_calls and recent_rate < max_success_rate),
    }


_SELECT_COLS = (
    "tool_name, success_count, failure_count, total_latency_ms, last_used_at, "
    "blacklisted_at, blacklist_reason, blacklist_expires_at, recent_success, "
    "recent_failure, decayed_at"
)


# MEM-46: the verdict, expressed in SQL so ranking/filtering happens BEFORE the
# LIMIT. The decayed success rate needs no decay factor (it cancels out); the
# decayed call volume does.
_SQL_BLACKLISTED = (
    "(blacklisted_at IS NOT NULL AND "
    "COALESCE(blacklist_expires_at, blacklisted_at + CAST(:bl_ttl AS interval)) "
    "> CAST(:now AS timestamptz))"
)
_SQL_RECENT_RATE = (
    "COALESCE(recent_success / NULLIF(recent_success + recent_failure, 0), 1.0)"
)
_SQL_RECENT_CALLS = (
    "(recent_success + recent_failure) * power(0.5, GREATEST(0, EXTRACT(EPOCH FROM "
    "(CAST(:now AS timestamptz) - COALESCE(decayed_at, CAST(:now AS timestamptz))))) "
    "/ :half_life)"
)
_SQL_UNRELIABLE = (
    f"({_SQL_BLACKLISTED} OR ({_SQL_RECENT_CALLS} + 1e-6 >= :min_calls "
    f"AND {_SQL_RECENT_RATE} < :max_rate))"
)


def _verdict_params(now: datetime, min_calls: int, max_success_rate: float) -> dict[str, Any]:
    return {
        "now": now,
        "bl_ttl": BLACKLIST_TTL,
        "half_life": DECAY_HALF_LIFE.total_seconds(),
        "min_calls": float(min_calls),
        "max_rate": float(max_success_rate),
    }


def _row_stats(r: Any, *, now: datetime, **thresholds: Any) -> dict[str, Any]:
    return _stats(
        r[0],
        int(r[1] or 0),
        int(r[2] or 0),
        float(r[3] or 0.0),
        last_used_at=r[4],
        blacklisted_at=r[5],
        blacklist_reason=r[6],
        blacklist_expires_at=r[7],
        recent_success=float(r[8] or 0.0),
        recent_failure=float(r[9] or 0.0),
        decayed_at=r[10],
        now=now,
        **thresholds,
    )


class ToolReliabilityStore:
    """Track per-tool success/failure rates and latency in PostgreSQL.

    Table: tool_reliability_memory
    Key: (tenant_id, tool_name)

    Counts come only from real tool executions (the agent executor records every
    MCP dispatch). A self-improvement "blacklist" is its own flag
    (``blacklisted_at``), never a synthetic failure count, and it expires
    (``blacklist_expires_at``) or is cleared by an admin. The verdict uses
    exponentially decayed counters (``recent_*``), so recovery is possible.

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
                "recent_success": 0.0,
                "recent_failure": 0.0,
                "decayed_at": None,
                "last_used_at": None,
                "blacklisted_at": None,
                "blacklist_reason": None,
                "blacklist_expires_at": None,
            },
        )

    @staticmethod
    def _cache_stats(c: dict[str, Any], now: datetime, **thresholds: Any) -> dict[str, Any]:
        return _stats(
            c["tool_name"],
            c["success_count"],
            c["failure_count"],
            c["total_latency_ms"],
            last_used_at=c["last_used_at"],
            blacklisted_at=c["blacklisted_at"],
            blacklist_reason=c["blacklist_reason"],
            blacklist_expires_at=c["blacklist_expires_at"],
            recent_success=c["recent_success"],
            recent_failure=c["recent_failure"],
            decayed_at=c["decayed_at"],
            now=now,
            **thresholds,
        )

    async def record(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        success: bool,
        latency_ms: float = 0.0,
        error: str = "",
        now: datetime | None = None,
    ) -> None:
        """Record one real tool call outcome.

        A DB failure is logged at warning (a tool call must not fail because its
        telemetry could not be written) — it is never silently dropped.
        """
        if not tenant_id or not tool_name:
            return
        when = now or datetime.now(UTC)
        entry = self._cache_entry(tenant_id, tool_name)
        factor = _decay_factor(entry["decayed_at"], when)
        entry["recent_success"] = entry["recent_success"] * factor + (1 if success else 0)
        entry["recent_failure"] = entry["recent_failure"] * factor + (0 if success else 1)
        entry["decayed_at"] = when
        entry["last_used_at"] = when
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
                # Decay the stored counters to :now, then add this outcome.
                decay = (
                    "power(0.5, GREATEST(0, EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) "
                    "- COALESCE(tool_reliability_memory.decayed_at, CAST(:now AS timestamptz)))"
                    ")) / :half_life)"
                )
                await session.execute(
                    text(f"""
                    INSERT INTO tool_reliability_memory
                        (tenant_id, tool_name, success_count, failure_count,
                         total_latency_ms, last_used_at, recent_success, recent_failure,
                         decayed_at)
                    VALUES (:tid, :tool, :sc, :fc, :lat, :now, :rs, :rf, :now)
                    ON CONFLICT (tenant_id, tool_name) DO UPDATE SET
                        success_count = tool_reliability_memory.success_count + :sc,
                        failure_count = tool_reliability_memory.failure_count + :fc,
                        total_latency_ms = tool_reliability_memory.total_latency_ms + :lat,
                        recent_success = tool_reliability_memory.recent_success * {decay} + :rs,
                        recent_failure = tool_reliability_memory.recent_failure * {decay} + :rf,
                        decayed_at = :now,
                        last_used_at = :now
                """),
                    {
                        "tid": tenant_id,
                        "tool": tool_name,
                        "sc": 1 if success else 0,
                        "fc": 0 if success else 1,
                        "rs": 1.0 if success else 0.0,
                        "rf": 0.0 if success else 1.0,
                        "lat": latency_ms,
                        "now": when,
                        "half_life": DECAY_HALF_LIFE.total_seconds(),
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

    async def blacklist(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        reason: str,
        ttl: timedelta = BLACKLIST_TTL,
        now: datetime | None = None,
    ) -> None:
        """Flag a tool as blacklisted for *ttl* without touching its counts."""
        if not tenant_id or not tool_name:
            return
        when = now or datetime.now(UTC)
        entry = self._cache_entry(tenant_id, tool_name)
        entry["blacklisted_at"] = when
        entry["blacklist_reason"] = reason
        entry["blacklist_expires_at"] = when + ttl
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
                    (tenant_id, tool_name, blacklisted_at, blacklist_reason,
                     blacklist_expires_at)
                VALUES (:tid, :tool, :now, :reason, :exp)
                ON CONFLICT (tenant_id, tool_name) DO UPDATE SET
                    blacklisted_at = :now,
                    blacklist_reason = :reason,
                    blacklist_expires_at = :exp
            """),
                {
                    "tid": tenant_id,
                    "tool": tool_name,
                    "reason": reason[:200],
                    "now": when,
                    "exp": when + ttl,
                },
            )

    async def clear_blacklist(self, *, tenant_id: str, tool_name: str) -> bool:
        """Lift a tool's blacklist now. Returns whether one was set.

        Raises :class:`ToolReliabilityUnavailableError` on a DB failure.
        """
        if self._db is None:
            entry = self._cache.get(f"{tenant_id}:{tool_name}")
            if entry is None or entry["blacklisted_at"] is None:
                return False
            entry["blacklisted_at"] = entry["blacklist_reason"] = None
            entry["blacklist_expires_at"] = None
            return True
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                res = await session.execute(
                    text("""
                    UPDATE tool_reliability_memory
                       SET blacklisted_at = NULL, blacklist_reason = NULL,
                           blacklist_expires_at = NULL
                     WHERE tenant_id = :tid AND tool_name = :tool
                       AND blacklisted_at IS NOT NULL
                """),
                    {"tid": tenant_id, "tool": tool_name},
                )
        except Exception as exc:
            logger.warning(
                "tool_reliability_clear_failed", tenant_id=tenant_id, error=str(exc)[:200]
            )
            raise ToolReliabilityUnavailableError(str(exc)) from exc
        return int(res.rowcount or 0) > 0

    async def get_reliability(
        self, *, tenant_id: str, tool_name: str, now: datetime | None = None
    ) -> dict[str, Any]:
        """Get reliability stats for a specific tool.

        Raises :class:`ToolReliabilityUnavailableError` when the DB is wired but
        cannot be read.
        """
        when = now or datetime.now(UTC)
        if self._db is None:
            return self._cache_stats(self._cache_entry(tenant_id, tool_name), when)
        try:
            from sqlalchemy import text

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(f"""
                    SELECT {_SELECT_COLS}
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
            return _stats(tool_name, 0, 0, 0.0, now=when)
        return _row_stats(row, now=when)

    async def list_tools(
        self,
        *,
        tenant_id: str,
        min_calls: int = DEFAULT_MIN_CALLS,
        max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
        limit: int = 200,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Every tool with recorded calls or a blacklist flag, least reliable first.

        Ranked in SQL (unreliable first, then by decayed success rate) BEFORE the
        LIMIT (MEM-46) — it used to LIMIT an unordered set and rank afterwards,
        so a large tenant's unreliable tools could be cut off.

        Raises :class:`ToolReliabilityUnavailableError` on a DB failure.
        """
        return await self._query_tools(
            tenant_id=tenant_id,
            min_calls=min_calls,
            max_success_rate=max_success_rate,
            limit=limit,
            now=now,
            unreliable_only=False,
        )

    async def _query_tools(
        self,
        *,
        tenant_id: str,
        min_calls: int,
        max_success_rate: float,
        limit: int,
        now: datetime | None,
        unreliable_only: bool,
    ) -> list[dict[str, Any]]:
        when = now or datetime.now(UTC)
        thresholds = {"min_calls": min_calls, "max_success_rate": max_success_rate}
        rows: list[dict[str, Any]]
        if self._db is None:
            rows = [
                self._cache_stats(c, when, **thresholds)
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
                            text(f"""
                        SELECT {_SELECT_COLS}
                        FROM tool_reliability_memory
                        WHERE tenant_id = :tid
                          AND (success_count + failure_count > 0
                               OR blacklisted_at IS NOT NULL)
                          {"AND " + _SQL_UNRELIABLE if unreliable_only else ""}
                        ORDER BY {_SQL_UNRELIABLE} DESC, {_SQL_RECENT_RATE} ASC, tool_name
                        LIMIT :lim
                    """),
                            {
                                "tid": tenant_id,
                                "lim": limit,
                                **_verdict_params(when, min_calls, max_success_rate),
                            },
                        )
                    ).fetchall()
            except Exception as exc:
                logger.warning(
                    "tool_reliability_list_failed", tenant_id=tenant_id, error=str(exc)[:200]
                )
                raise ToolReliabilityUnavailableError(str(exc)) from exc
            rows = [_row_stats(r, now=when, **thresholds) for r in db_rows]
        if unreliable_only:
            rows = [r for r in rows if r["unreliable"]]
        rows.sort(key=lambda s: (not s["unreliable"], s["recent_success_rate"], s["tool_name"]))
        return rows[:limit]

    async def get_unreliable_tools(
        self,
        *,
        tenant_id: str,
        min_calls: int = DEFAULT_MIN_CALLS,
        max_success_rate: float = DEFAULT_MAX_SUCCESS_RATE,
    ) -> list[dict[str, Any]]:
        """Tools with poor reliability (or blacklisted) for agent planning awareness.

        The threshold is applied in SQL (MEM-46), so the answer never depends on
        which rows a LIMIT window happened to contain.

        Raises :class:`ToolReliabilityUnavailableError` on a DB failure.
        """
        return await self._query_tools(
            tenant_id=tenant_id,
            min_calls=min_calls,
            max_success_rate=max_success_rate,
            limit=200,
            now=None,
            unreliable_only=True,
        )
