"""Cost controls — per-goal and per-tenant daily budget enforcement.

Before every tool call the pipeline calls check_and_record():
  - If the estimated cost would exceed per_goal_usd, returns False (block).
  - If it would exceed per_tenant_daily_usd, returns False (block).
  - Otherwise adds the cost to running totals and returns True.

In production this is backed by Redis counters with daily TTL (midnight reset).
This in-memory implementation is used in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
from collections import defaultdict
from collections.abc import Awaitable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger
from app.observability.metrics import record_cost_usd
from app.tenancy.context import TenantContext

# Metric scope of the spend being checked/recorded: "tool" by default; LLM
# charges run under llm_spend() so dashboards can separate LLM from tool cost.
_COST_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentverse_cost_metric_scope", default="tool"
)


async def llm_spend(call: Awaitable[bool]) -> bool:
    """Await a ``check_and_record(...)`` charge, recording it under scope ``llm``."""
    token = _COST_SCOPE.set("llm")
    try:
        return await call
    finally:
        _COST_SCOPE.reset(token)


# The agent a charge belongs to, for per-agent daily caps (COST-02): set by the
# goal runner (worker run_goal / GoalService) around a goal's execution, so every
# LLM/tool charge made inside it is attributed without threading an argument.
_COST_AGENT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentverse_cost_agent_id", default=""
)


@contextlib.contextmanager
def cost_agent_scope(agent_id: str | None) -> Iterator[None]:
    """Attribute every charge made inside the block to *agent_id*."""
    token = _COST_AGENT.set(str(agent_id or ""))
    try:
        yield
    finally:
        _COST_AGENT.reset(token)


def bind_cost_agent(agent_id: str | None) -> None:
    """Attribute the rest of the CURRENT task's charges to *agent_id*.

    For a goal-runner task's entry point: the task owns its context copy, so
    nothing needs resetting (child tasks inherit it).
    """
    _COST_AGENT.set(str(agent_id or ""))


def _charge_agent(agent_id: str | None, tenant_ctx: Any) -> str:
    """Explicit agent > running goal's agent > agent-scoped API key's agent."""
    if agent_id:
        return str(agent_id)
    scoped = _COST_AGENT.get()
    if scoped:
        return scoped
    key = getattr(tenant_ctx, "agent_key", None)
    key_agent = str(getattr(key, "agent_id", "") or "")
    return "" if key_agent in ("", "unknown") else key_agent


@dataclass(frozen=True)
class BudgetConfig:
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0
    # agent_id → daily USD cap (absent = no per-agent cap).
    per_agent_daily_usd: Mapping[str, float] = field(default_factory=dict)
    alert_pct_thresholds: tuple[int, ...] = (50, 75, 90)

    def agent_daily_limit(self, agent_id: str) -> float:
        if not agent_id:
            return 0.0
        try:
            return max(0.0, float(self.per_agent_daily_usd.get(agent_id, 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0


class BudgetUnavailableError(RuntimeError):
    """The tenant's configured budget could not be loaded (and none is cached)."""


# ── Budget threshold alerts (COST-03) ─────────────────────────────────────────
# Each configured ``alert_pct_thresholds`` value fires once per UTC day per scope
# when a charge crosses it. They used to be persisted and never evaluated (only a
# hard-coded 79-81% log line, and none at all on the production Lua path).

_ALERT_DELIVERY_TIMEOUT_S = 5.0


def crossed_thresholds(
    before: float, after: float, limit: float, thresholds: tuple[int, ...] | list[int]
) -> list[int]:
    """The thresholds (percent of *limit*) that a charge from *before* to *after* crossed."""
    if limit <= 0 or after <= before:
        return []
    out: list[int] = []
    for pct in sorted({int(t) for t in thresholds if 0 < int(t) <= 100}):
        mark = limit * pct / 100.0
        if before < mark <= after:
            out.append(pct)
    return out


async def _deliver_budget_alert(db_factory: Any, alert: dict[str, Any]) -> None:
    """Send *alert* to the tenant's notification channels (Slack/Teams/webhook)."""
    if db_factory is None:
        return
    from app.services.notification_service import NotificationService

    svc = NotificationService()
    svc.set_db(db_factory)
    await svc.notify_budget_alert(alert)


async def emit_budget_alerts(
    *,
    tenant_id: str,
    scope: str,
    before: float,
    after: float,
    limit: float,
    thresholds: tuple[int, ...] | list[int],
    agent_id: str = "",
    redis: Any = None,
    db_factory: Any = None,
    seen: set[str] | None = None,
) -> list[int]:
    """Fire every threshold the charge crossed, once per day (fleet-wide with Redis).

    Dedupe: ``SET NX`` on a per-(tenant, scope, agent, day, pct) key in the shared
    Redis, else the caller's process-local *seen* set. Delivery is bounded and
    never raises — an alert must not block or fail the charge it reports.
    """
    crossed = crossed_thresholds(before, after, limit, thresholds)
    fired: list[int] = []
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    for pct in crossed:
        key = f"cost_alert:{tenant_id}:{scope}:{agent_id}:{day}:{pct}"
        try:
            if redis is not None:
                won = bool(await redis.set(key, "1", nx=True, ex=2 * 86400))
            elif seen is not None:
                if len(seen) > 10_000:  # bounded: old days' keys are dropped
                    seen.clear()
                won = key not in seen
                seen.add(key)
            else:
                won = True
        except Exception as exc:
            get_logger(__name__).warning("budget_alert_dedupe_failed", error=str(exc)[:100])
            won = True  # better a duplicate alert than a missed one
        if not won:
            continue
        fired.append(pct)
        alert = {
            "type": "budget_threshold_alert",
            "tenant_id": tenant_id,
            "scope": scope,
            "agent_id": agent_id,
            "threshold_pct": pct,
            "spent_usd": round(after, 6),
            "limit_usd": limit,
        }
        get_logger(__name__).warning("budget_threshold_alert", **alert)
        try:
            await asyncio.wait_for(
                _deliver_budget_alert(db_factory, alert), _ALERT_DELIVERY_TIMEOUT_S
            )
        except Exception as exc:
            get_logger(__name__).warning(
                "budget_alert_delivery_failed", tenant_id=tenant_id, error=str(exc)[:100]
            )
    return fired


# Budgets live in ``budget_configs`` (DB-authoritative, shared by every replica
# and Celery worker). Each process caches a tenant's row briefly so the per-LLM-
# call check is not a DB round-trip; a PUT invalidates this process's cache and
# every other process picks the change up within the TTL.
BUDGET_CACHE_TTL_S = 15.0


class TenantBudgetSource:
    """Loads per-tenant BudgetConfig from ``budget_configs`` with a short TTL cache.

    ``get`` returns ``None`` when the tenant has no row (caller falls back to its
    own default). On a DB error it serves the last cached value if there is one;
    with nothing cached it raises :class:`BudgetUnavailableError` so enforcement
    can fail closed rather than silently apply a looser default budget.
    """

    def __init__(self, db_factory: Any = None, ttl_s: float = BUDGET_CACHE_TTL_S) -> None:
        self._db = db_factory
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, BudgetConfig | None]] = {}

    @property
    def configured(self) -> bool:
        return self._db is not None

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)

    def put(self, tenant_id: str, cfg: BudgetConfig) -> None:
        self._cache[tenant_id] = (time.monotonic(), cfg)

    async def get(self, tenant_id: str) -> BudgetConfig | None:
        if self._db is None:
            return None
        hit = self._cache.get(tenant_id)
        now = time.monotonic()
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        try:
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
                            "SELECT per_goal_usd, per_tenant_daily_usd, "
                            "per_agent_daily_usd, alert_pct_thresholds "
                            "FROM budget_configs WHERE tenant_id = :tid"
                        ),
                        {"tid": tenant_id},
                    )
                ).fetchone()
        except Exception as exc:
            get_logger(__name__).warning(
                "tenant_budget_load_failed", tenant_id=tenant_id, error=str(exc)[:200]
            )
            if hit is not None:
                return hit[1]  # stale but real — better than an unknown budget
            raise BudgetUnavailableError(f"budget for tenant {tenant_id} unavailable") from exc
        cfg = _budget_from_row(row) if row else None
        self._cache[tenant_id] = (now, cfg)
        return cfg


def _budget_from_row(row: Any) -> BudgetConfig:
    import json as _json

    per_agent: Any = row[2] if len(row) > 2 else None
    if isinstance(per_agent, str):
        try:
            per_agent = _json.loads(per_agent)
        except ValueError:
            per_agent = None
    agents = {
        str(k): float(v)
        for k, v in (per_agent or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    thresholds_raw: Any = row[3] if len(row) > 3 else None
    thresholds = tuple(
        sorted({int(p) for p in (thresholds_raw or ()) if isinstance(p, int) and 0 < p <= 100})
    )
    return BudgetConfig(
        per_goal_usd=float(row[0]),
        per_tenant_daily_usd=float(row[1]),
        per_agent_daily_usd=agents,
        alert_pct_thresholds=thresholds if thresholds_raw is not None else (50, 75, 90),
    )


async def persist_tenant_budget(
    db_factory: Any,
    tenant_id: str,
    *,
    per_goal_usd: float,
    per_tenant_daily_usd: float,
    per_agent_daily_usd: dict[str, float] | None = None,
    alert_pct_thresholds: list[int] | None = None,
) -> None:
    """Upsert the tenant's budget_configs row under its RLS context (raises on error).

    Columns not supplied keep their stored value (or the table default on insert).
    """
    import json as _json

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    sets = [
        "per_goal_usd = EXCLUDED.per_goal_usd",
        "per_tenant_daily_usd = EXCLUDED.per_tenant_daily_usd",
    ]
    cols = ["tenant_id", "per_goal_usd", "per_tenant_daily_usd"]
    vals = [":tid", ":pg", ":ptd"]
    params: dict[str, Any] = {
        "tid": tenant_id,
        "pg": per_goal_usd,
        "ptd": per_tenant_daily_usd,
    }
    if per_agent_daily_usd is not None:
        cols.append("per_agent_daily_usd")
        vals.append("CAST(:pad AS jsonb)")
        sets.append("per_agent_daily_usd = EXCLUDED.per_agent_daily_usd")
        params["pad"] = _json.dumps(per_agent_daily_usd)
    if alert_pct_thresholds is not None:
        cols.append("alert_pct_thresholds")
        vals.append(":apt")
        sets.append("alert_pct_thresholds = EXCLUDED.alert_pct_thresholds")
        params["apt"] = alert_pct_thresholds
    sets.append("updated_at = NOW()")
    sql = (
        f"INSERT INTO budget_configs ({', '.join(cols)}) VALUES ({', '.join(vals)}) "
        f"ON CONFLICT (tenant_id) DO UPDATE SET {', '.join(sets)}"
    )
    # budget_configs is FORCE ROW LEVEL SECURITY: the write must carry the GUC.
    async with db_factory() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        await session.execute(text(sql), params)


def _parse_float(val: Any) -> float:
    """Decode a Redis GET value (bytes, str, int, or None) to float."""
    if val is None:
        return 0.0
    if isinstance(val, (bytes, bytearray)):
        val = val.decode()
    return float(val) if val else 0.0


class CostController:
    """Enforces per-goal and per-tenant-daily cost budgets."""

    def __init__(
        self,
        config: BudgetConfig | None = None,
        *,
        per_goal_usd: float | None = None,
        per_tenant_daily_usd: float | None = None,
    ) -> None:
        if config is None:
            config = BudgetConfig(
                per_goal_usd=per_goal_usd if per_goal_usd is not None else 10.0,
                per_tenant_daily_usd=(
                    per_tenant_daily_usd if per_tenant_daily_usd is not None else 500.0
                ),
            )
        self._cfg = config
        # Key: (tenant_id, goal_id) → total USD spent
        self._goal_totals: dict[tuple[str, str], float] = defaultdict(float)
        # Key: tenant_id → total daily USD spent
        self._daily_totals: dict[str, float] = defaultdict(float)
        # Key: (tenant_id, agent_id) → daily USD spent by that agent
        self._agent_daily_totals: dict[tuple[str, str], float] = {}
        # Track when daily totals were last reset (per tenant: tenant_id → date string)
        self._last_reset_date: dict[str, str] = {}
        # Optional Redis client for cross-replica cost tracking (set by main.py)
        self._redis: Any = None
        # Per-goal+tenant locks to prevent TOCTOU races
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Per-tenant overrides (no-DB mode) and the DB-authoritative source.
        self._tenant_configs: dict[str, BudgetConfig] = {}
        self._budget_source = TenantBudgetSource()
        # Threshold alerts already fired by this process (no-Redis dedupe).
        self._alerts_seen: set[str] = set()

    def set_budget_db(self, db_factory: Any) -> None:
        """Enforce each tenant's budget_configs row (wired in lifespan / worker)."""
        self._budget_source = TenantBudgetSource(db_factory)

    def configure_tenant_budget(self, tenant_id: str, budget: BudgetConfig) -> None:
        self._tenant_configs[tenant_id] = budget
        if self._budget_source.configured:
            self._budget_source.put(tenant_id, budget)

    def invalidate_tenant_budget(self, tenant_id: str) -> None:
        self._budget_source.invalidate(tenant_id)

    async def resolve_config(self, tenant_id: str) -> BudgetConfig:
        """DB row (authoritative) > per-tenant override > controller default."""
        cfg = await self._budget_source.get(tenant_id)
        if cfg is not None:
            return cfg
        return self._tenant_configs.get(tenant_id, self._cfg)

    async def ahas_remaining_budget(self, *, tenant_ctx: TenantContext) -> bool:
        cfg = await self.resolve_config(tenant_ctx.tenant_id)
        self._reset_if_new_day(tenant_ctx.tenant_id)
        return self._daily_totals.get(tenant_ctx.tenant_id, 0.0) < cfg.per_tenant_daily_usd

    def _reset_if_new_day(self, tenant_id: str) -> None:
        """Reset daily totals if we've crossed midnight UTC (sync, no lock)."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        last = self._last_reset_date.get(tenant_id)
        if last != today:
            if last is not None:
                # It's a new day — reset daily total
                self._daily_totals[tenant_id] = 0.0
            self._last_reset_date[tenant_id] = today

    async def _reset_if_new_day_atomic(self, tenant_id: str) -> None:
        """Atomic daily reset using a per-tenant lock."""
        async with self._locks[f"reset:{tenant_id}"]:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if self._last_reset_date.get(tenant_id) != today:
                if self._last_reset_date.get(tenant_id) is not None:
                    self._daily_totals[tenant_id] = 0.0
                    for key in [k for k in self._agent_daily_totals if k[0] == tenant_id]:
                        del self._agent_daily_totals[key]
                self._last_reset_date[tenant_id] = today

    async def check_and_record(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: TenantContext,
        tool_name: str = "",
        attempt_id: str = "",
        agent_id: str | None = None,
    ) -> bool:
        """Atomically check budget and record cost. Returns True if within budget."""
        del attempt_id
        try:
            cfg = await self.resolve_config(tenant_ctx.tenant_id)
        except BudgetUnavailableError:
            # Unknown budget → fail closed (a looser default could overspend).
            return False
        agent = _charge_agent(agent_id, tenant_ctx)
        agent_limit = cfg.agent_daily_limit(agent)
        # One lock per tenant: the daily (and per-agent) totals are tenant-wide.
        lock_key = f"tenant:{tenant_ctx.tenant_id}"
        async with self._locks[lock_key]:
            await self._reset_if_new_day_atomic(tenant_ctx.tenant_id)

            goal_key = (tenant_ctx.tenant_id, goal_id)
            agent_key = (tenant_ctx.tenant_id, agent)
            new_goal_total = self._goal_totals[goal_key] + cost_usd
            new_daily_total = self._daily_totals[tenant_ctx.tenant_id] + cost_usd
            new_agent_total = self._agent_daily_totals.get(agent_key, 0.0) + cost_usd

            if new_goal_total > cfg.per_goal_usd:
                return False
            if new_daily_total > cfg.per_tenant_daily_usd:
                return False
            if agent_limit > 0 and new_agent_total > agent_limit:
                return False

            self._goal_totals[goal_key] = new_goal_total
            self._daily_totals[tenant_ctx.tenant_id] = new_daily_total
            if agent:
                self._agent_daily_totals[agent_key] = new_agent_total
            record_cost_usd(scope=_COST_SCOPE.get(), amount=cost_usd)
        # Configured threshold alerts (COST-03), outside the tenant lock.
        await self._alert(
            tenant_ctx.tenant_id,
            cfg,
            cost_usd,
            new_daily_total,
            agent,
            agent_limit,
            new_agent_total,
        )
        return True

    async def _alert(
        self,
        tenant_id: str,
        cfg: BudgetConfig,
        cost_usd: float,
        new_daily: float,
        agent: str,
        agent_limit: float,
        new_agent: float,
    ) -> None:
        db = self._budget_source._db
        await emit_budget_alerts(
            tenant_id=tenant_id,
            scope="tenant_daily",
            before=new_daily - cost_usd,
            after=new_daily,
            limit=cfg.per_tenant_daily_usd,
            thresholds=cfg.alert_pct_thresholds,
            redis=self._redis,
            db_factory=db,
            seen=self._alerts_seen,
        )
        if agent and agent_limit > 0:
            await emit_budget_alerts(
                tenant_id=tenant_id,
                scope="agent_daily",
                before=new_agent - cost_usd,
                after=new_agent,
                limit=agent_limit,
                thresholds=cfg.alert_pct_thresholds,
                agent_id=agent,
                redis=self._redis,
                db_factory=db,
                seen=self._alerts_seen,
            )

    def goal_total(self, goal_id: str, *, tenant_ctx: TenantContext) -> float:
        return self._goal_totals.get((tenant_ctx.tenant_id, goal_id), 0.0)

    def daily_total(self, *, tenant_ctx: TenantContext) -> float:
        self._reset_if_new_day(tenant_ctx.tenant_id)
        return self._daily_totals.get(tenant_ctx.tenant_id, 0.0)

    def has_remaining_budget(self, *, tenant_ctx: TenantContext) -> bool:
        """True if the tenant has any daily budget left for a new goal.

        Used as a goal-submission pre-flight so an over-budget tenant is blocked
        up-front with a budget reason, rather than accepted and then silently
        having every step skipped. A per-tenant daily budget of 0 means "no
        budget" — nothing can run.
        """
        self._reset_if_new_day(tenant_ctx.tenant_id)
        spent = self._daily_totals.get(tenant_ctx.tenant_id, 0.0)
        return spent < self._cfg.per_tenant_daily_usd

    def get_tenant_cost_today(self, tenant_ctx: TenantContext) -> float:
        """Return the current-day spend for the tenant (resets at UTC midnight)."""
        self._reset_if_new_day(tenant_ctx.tenant_id)
        return self._daily_totals.get(tenant_ctx.tenant_id, 0.0)


# Atomic Lua script: check BOTH goal AND daily limits, then increment both.
# Returns "<new_goal>:<new_daily>" on success, or raises a Redis error reply
# on budget violation — so no increment ever happens when a limit is exceeded.
_LUA_CHECK_AND_INCREMENT = """\
local goal_current  = tonumber(redis.call('GET', KEYS[1])) or 0
local daily_current = tonumber(redis.call('GET', KEYS[2])) or 0
local cost        = tonumber(ARGV[1])
local goal_limit  = tonumber(ARGV[2])
local daily_limit = tonumber(ARGV[3])
local goal_expiry  = tonumber(ARGV[4])
local daily_expiry = tonumber(ARGV[5])

local agent_limit = tonumber(ARGV[6]) or 0
local track_agent = ARGV[7] == '1'
local agent_current = 0
if track_agent then
    agent_current = tonumber(redis.call('GET', KEYS[3])) or 0
end

if goal_limit > 0 and goal_current + cost > goal_limit then
    return redis.error_reply('GOAL_BUDGET_EXCEEDED')
end
if daily_limit > 0 and daily_current + cost > daily_limit then
    return redis.error_reply('DAILY_BUDGET_EXCEEDED')
end
if track_agent and agent_limit > 0 and agent_current + cost > agent_limit then
    return redis.error_reply('AGENT_BUDGET_EXCEEDED')
end

local new_goal  = redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIREAT', KEYS[1], goal_expiry)
local new_daily = redis.call('INCRBYFLOAT', KEYS[2], cost)
redis.call('EXPIREAT', KEYS[2], daily_expiry)
local new_agent = 0
if track_agent then
    new_agent = redis.call('INCRBYFLOAT', KEYS[3], cost)
    redis.call('EXPIREAT', KEYS[3], daily_expiry)
end
return tostring(new_goal) .. ':' .. tostring(new_daily) .. ':' .. tostring(new_agent)
"""

_BUDGET_EXCEEDED_REPLIES = (
    "GOAL_BUDGET_EXCEEDED",
    "DAILY_BUDGET_EXCEEDED",
    "AGENT_BUDGET_EXCEEDED",
)


class RedisCostController:
    """Production CostController backed by Redis for cross-replica accuracy.

    Uses an atomic Lua script (check-then-increment) so a denied request never
    permanently charges the tenant.  EXPIREAT resets counters at next UTC midnight
    for daily counters and after 24 h for per-goal counters.

    All replicas share the same counters, preventing per-replica bypass.
    """

    def __init__(
        self,
        redis: Any,
        per_tenant_config: dict[str, BudgetConfig] | None = None,
        *,
        budget_db: Any = None,
    ) -> None:
        self._redis = redis
        self._tenant_configs: dict[str, BudgetConfig] = per_tenant_config or {}
        # budget_configs was written by PUT /costs/budgets but never read here, so
        # every tenant was enforced at the hard-coded BudgetConfig() defaults.
        self._budget_source = TenantBudgetSource(budget_db)

    def set_budget_db(self, db_factory: Any) -> None:
        self._budget_source = TenantBudgetSource(db_factory)

    def invalidate_tenant_budget(self, tenant_id: str) -> None:
        self._budget_source.invalidate(tenant_id)

    async def resolve_config(self, tenant_id: str) -> BudgetConfig:
        """DB row (authoritative) > per-tenant override > default BudgetConfig."""
        cfg = await self._budget_source.get(tenant_id)
        if cfg is not None:
            return cfg
        return self._tenant_configs.get(tenant_id, BudgetConfig())

    async def ahas_remaining_budget(self, *, tenant_ctx: TenantContext) -> bool:
        """Whether the tenant's shared daily spend is still under its budget.

        Same contract as ``CostController.ahas_remaining_budget`` — the
        StrategyRunner budget reservation and the guarded-completion preflight
        call it. Missing here, every DISTRIBUTED goal was refused with
        ``budget_reservation_failed`` once Redis was wired. A Redis/DB error
        propagates (callers fail closed); it is never read as "budget left".
        """
        cfg = await self.resolve_config(tenant_ctx.tenant_id)
        spent = _parse_float(await self._redis.get(self._daily_key(tenant_ctx.tenant_id)))
        return spent < cfg.per_tenant_daily_usd

    def _daily_key(self, tenant_id: str) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"cost:daily:{tenant_id}:{today}"

    def _goal_key(self, goal_id: str, tenant_id: str = "") -> str:
        return f"cost:goal:{tenant_id}:{goal_id}"

    async def _get_ttl_to_midnight(self) -> int:
        """Seconds until next UTC midnight."""
        now = datetime.now(UTC)
        from datetime import timedelta

        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((midnight - now).total_seconds()))

    def _agent_key(self, tenant_id: str, agent_id: str) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"cost:agent_daily:{tenant_id}:{agent_id}:{today}"

    async def check_and_record_async(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: Any,
        attempt_id: str = "",
        agent_id: str | None = None,
    ) -> bool:
        """Check budget atomically (check-then-increment). Returns True if within budget.

        Uses a Lua script so the counters are only incremented when the
        per-goal, per-tenant-daily and per-agent-daily limits are all satisfied.
        The agent is *agent_id*, else the running goal's ``cost_agent_scope``,
        else the agent-scoped API key's agent (COST-02). On Redis clients that
        don't support Lua (test doubles), falls back to a non-atomic
        GET-check-INCRBYFLOAT sequence.

        ``attempt_id`` enables idempotency: the same (goal_id, attempt_id) pair
        is only charged once, making it safe to retry on Celery redeliveries.
        """
        try:
            cfg = await self.resolve_config(tenant_ctx.tenant_id)
        except BudgetUnavailableError:
            # Unknown budget → fail closed (a looser default could overspend).
            return False

        # Idempotency guard — skip re-charging the same attempt
        idem_key = f"cost_idem:{tenant_ctx.tenant_id}:{goal_id}:{attempt_id}" if attempt_id else ""
        if idem_key:
            try:
                if await self._redis.exists(idem_key):
                    return True  # already charged this attempt
            except Exception:
                pass

        agent = _charge_agent(agent_id, tenant_ctx)
        try:
            goal_key = self._goal_key(goal_id, tenant_ctx.tenant_id)
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            agent_key = self._agent_key(tenant_ctx.tenant_id, agent) if agent else daily_key
            goal_limit = cfg.per_goal_usd if cfg.per_goal_usd > 0 else 0.0
            daily_limit = cfg.per_tenant_daily_usd if cfg.per_tenant_daily_usd > 0 else 0.0
            agent_limit = cfg.agent_daily_limit(agent)
            now_ts = int(time.time())
            ttl = await self._get_ttl_to_midnight()
            goal_expiry = now_ts + 86400
            daily_expiry = now_ts + ttl

            if getattr(self._redis, "register_script", None) is not None:
                # Atomic Lua path — check THEN increment (all counters in one script)
                script = self._redis.register_script(_LUA_CHECK_AND_INCREMENT)
                _totals = await script(
                    keys=[goal_key, daily_key, agent_key],
                    args=[
                        str(cost_usd),
                        str(goal_limit),
                        str(daily_limit),
                        str(goal_expiry),
                        str(daily_expiry),
                        str(agent_limit),
                        "1" if agent else "0",
                    ],
                )
                _parts = (
                    _totals.decode() if isinstance(_totals, bytes) else str(_totals or "")
                ).split(":")
                _new_daily = _parse_float(_parts[1]) if len(_parts) > 1 else 0.0
                _new_agent = _parse_float(_parts[2]) if len(_parts) > 2 else 0.0
            else:
                # Fallback for test doubles without Lua support.
                # Non-atomic GET-check-INCRBYFLOAT — safe for single-replica tests.
                goal_current = _parse_float(await self._redis.get(goal_key))
                daily_current = _parse_float(await self._redis.get(daily_key))
                if goal_limit > 0 and goal_current + cost_usd > goal_limit:
                    return False
                if daily_limit > 0 and daily_current + cost_usd > daily_limit:
                    return False
                if agent and agent_limit > 0:
                    agent_current = _parse_float(await self._redis.get(agent_key))
                    if agent_current + cost_usd > agent_limit:
                        return False
                await self._redis.incrbyfloat(goal_key, cost_usd)
                await self._redis.expire(goal_key, 86400)
                _new_daily = _parse_float(await self._redis.incrbyfloat(daily_key, cost_usd))
                await self._redis.expireat(daily_key, daily_expiry)
                _new_agent = 0.0
                if agent:
                    _new_agent = _parse_float(await self._redis.incrbyfloat(agent_key, cost_usd))
                    await self._redis.expireat(agent_key, daily_expiry)

            # Stamp idempotency key so retries are skipped
            if idem_key:
                with contextlib.suppress(Exception):
                    await self._redis.set(idem_key, "1", ex=86400)

            record_cost_usd(scope=_COST_SCOPE.get(), amount=cost_usd)
            # Configured threshold alerts (COST-03): fleet-wide once per day.
            await emit_budget_alerts(
                tenant_id=tenant_ctx.tenant_id,
                scope="tenant_daily",
                before=_new_daily - cost_usd,
                after=_new_daily,
                limit=daily_limit,
                thresholds=cfg.alert_pct_thresholds,
                redis=self._redis,
                db_factory=self._budget_source._db,
            )
            if agent and agent_limit > 0:
                await emit_budget_alerts(
                    tenant_id=tenant_ctx.tenant_id,
                    scope="agent_daily",
                    before=_new_agent - cost_usd,
                    after=_new_agent,
                    limit=agent_limit,
                    thresholds=cfg.alert_pct_thresholds,
                    agent_id=agent,
                    redis=self._redis,
                    db_factory=self._budget_source._db,
                )
            return True

        except Exception as exc:
            err_str = str(exc)
            if any(reply in err_str for reply in _BUDGET_EXCEEDED_REPLIES):
                return False
            # Fail closed in every environment: an unset/misspelled ENVIRONMENT
            # used to make a Redis outage wave all spend through unmetered.
            get_logger(__name__).warning("cost_check_error_fail_closed", error=err_str[:100])
            return False

    async def refund_async(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: Any,
        reason: str = "",
        agent_id: str | None = None,
    ) -> None:
        """Refund a previously charged cost when the downstream operation failed.

        Uses INCRBYFLOAT with a negative amount — this is the standard Redis
        compensating pattern and is always safe (no underflow guard; a counter
        going slightly negative is acceptable and self-corrects on the next charge).
        """
        try:
            goal_key = self._goal_key(goal_id, tenant_ctx.tenant_id)
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            await self._redis.incrbyfloat(goal_key, -cost_usd)
            await self._redis.incrbyfloat(daily_key, -cost_usd)
            agent = _charge_agent(agent_id, tenant_ctx)
            if agent:
                await self._redis.incrbyfloat(
                    self._agent_key(tenant_ctx.tenant_id, agent), -cost_usd
                )
            get_logger(__name__).info(
                "cost_refunded", goal_id=goal_id, amount=cost_usd, reason=reason
            )
        except Exception as exc:
            get_logger(__name__).warning("cost_refund_failed", error=str(exc)[:80])

    async def get_tenant_cost_today(self, tenant_ctx: Any) -> float:
        """Get today's accumulated cost for this tenant from Redis."""
        try:
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            val = await self._redis.get(daily_key)
            return float(val) if val else 0.0
        except Exception:
            return 0.0

    async def get_budget_status(
        self,
        tenant_id: str | None = None,
        goal_id: str | None = None,
        *,
        tenant_ctx: Any = None,
    ) -> dict[str, Any]:
        """Pure READ operation — never modifies Redis counters (Amendment 6.4).

        Accepts either ``tenant_id`` (positional/keyword) or ``tenant_ctx``
        (keyword-only) to resolve the tenant.  Safe to call from prediction
        endpoints and dashboards.  Uses GET (not INCRBYFLOAT) so it cannot
        corrupt TTLs or counter values.
        """
        resolved_id: str
        if tenant_id is not None:
            resolved_id = tenant_id
        elif tenant_ctx is not None:
            resolved_id = tenant_ctx.tenant_id
        else:
            msg = "Either tenant_id or tenant_ctx must be provided"
            raise ValueError(msg)

        daily_key = self._daily_key(resolved_id)
        goal_key = f"cost:goal:{resolved_id}:{goal_id}" if goal_id else None

        try:
            daily_spent = _parse_float(await self._redis.get(daily_key))
            goal_spent = _parse_float(await self._redis.get(goal_key)) if goal_key else 0.0
        except Exception:
            daily_spent = 0.0
            goal_spent = 0.0

        cfg = await self.resolve_config(resolved_id)
        remaining = max(0.0, cfg.per_tenant_daily_usd - daily_spent)
        daily_pct_remaining = remaining / max(cfg.per_tenant_daily_usd, 0.01)

        result: dict[str, Any] = {
            "daily_spent": daily_spent,
            "daily_limit": cfg.per_tenant_daily_usd,
            "daily_remaining": remaining,
            "budget_pct_remaining": daily_pct_remaining,
            "goal_spent": goal_spent,
        }

        # A goal that is close to exhausting ITS OWN per_goal_usd cap must be
        # reflected here too — otherwise get_cost_tier() (which drives model
        # auto-downgrade for a single goal) only ever sees the tenant-wide daily
        # ratio and stays "premium" right up until check_and_record hard-blocks
        # the goal's next call, skipping the graceful cost/quality tradeoff.
        # budget_pct_remaining is the MORE constrained of the two — whichever
        # limit would bind first is the one that should drive the tier.
        if goal_key is not None and cfg.per_goal_usd > 0:
            goal_remaining = max(0.0, cfg.per_goal_usd - goal_spent)
            goal_pct_remaining = goal_remaining / max(cfg.per_goal_usd, 0.01)
            result["goal_pct_remaining"] = goal_pct_remaining
            result["budget_pct_remaining"] = min(daily_pct_remaining, goal_pct_remaining)

        return result

    async def get_cost_tier(self, *, goal_id: str, tenant_ctx: Any) -> str:
        """
        Return cost tier based on budget consumption.
        Used by ModelRouter to auto-downgrade models when budget is tight.

        Returns:
            'premium'  — < 60% budget used → full model tier
            'standard' — 60-85% used      → execution model for planning
            'economy'  — > 85% used        → verification model for all
        """
        try:
            status = await self.get_budget_status(tenant_ctx.tenant_id, goal_id=goal_id)
            pct_used = 1.0 - status.get("budget_pct_remaining", 1.0)
            if pct_used >= 0.85:
                return "economy"
            if pct_used >= 0.60:
                return "standard"
        except Exception:
            pass
        return "premium"

    async def check_and_record(
        self,
        *,
        tenant_ctx: Any,
        goal_id: str,
        cost_usd: float,
        tool_name: str = "",
        attempt_id: str = "",
        agent_id: str | None = None,
    ) -> bool:
        """Drop-in alias matching CostController.check_and_record signature."""
        return await self.check_and_record_async(
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
            cost_usd=cost_usd,
            attempt_id=attempt_id,
            agent_id=agent_id,
        )

    def configure_tenant_budget(self, tenant_id: str, budget: BudgetConfig) -> None:
        self._tenant_configs[tenant_id] = budget
        if self._budget_source.configured:
            self._budget_source.put(tenant_id, budget)
