"""Per-tenant daily recipient quota for the platform email relay.

POST /tools/email/send relays through the platform's SMTP sender, so without a
bound any tools:write key could send unlimited mail to unlimited recipients.
Each recipient counts against a per-tenant, per-UTC-day quota (by plan, or
``email_daily_recipient_quota`` when set) held in Redis so every replica and
worker shares it. Without a usable Redis the relay refuses outside development.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger
from app.tenancy.context import PlanTier

_log = get_logger(__name__)

DAILY_RECIPIENT_QUOTA: dict[PlanTier, int] = {
    PlanTier.FREE: 100,
    PlanTier.STARTER: 1_000,
    PlanTier.PROFESSIONAL: 10_000,
    PlanTier.ENTERPRISE: 100_000,
}

# Atomic check-and-consume: refuse (and consume nothing) when n would exceed the limit.
_LUA_CONSUME = """
local key = KEYS[1]
local n = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local used = tonumber(redis.call('GET', key) or 0)
if used + n > limit then
    return {0, used}
end
used = redis.call('INCRBY', key, n)
redis.call('EXPIRE', key, ttl)
return {1, used}
"""

# Process-local counts, used only in development without Redis.
_local_used: dict[str, int] = {}


class EmailQuotaExceededError(Exception):
    def __init__(self, limit: int, used: int) -> None:
        super().__init__(f"daily email recipient quota exhausted ({used}/{limit})")
        self.limit = limit
        self.used = used


class EmailQuotaUnavailableError(Exception):
    """The shared quota store is unreachable (the relay refuses: 503)."""


@dataclass(frozen=True)
class QuotaResult:
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def daily_limit(plan: PlanTier | str) -> int:
    from app.core.config import get_settings

    override = int(getattr(get_settings(), "email_daily_recipient_quota", 0) or 0)
    if override > 0:
        return override
    try:
        return DAILY_RECIPIENT_QUOTA[PlanTier(plan)]
    except ValueError:
        return DAILY_RECIPIENT_QUOTA[PlanTier.FREE]


def _day() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


async def consume(redis: Any, tenant_id: str, plan: PlanTier | str, n: int) -> QuotaResult:
    """Consume *n* recipients from today's quota or raise."""
    from app.governance.audit import _durable_audit_required

    limit = daily_limit(plan)
    key = f"email_quota:{tenant_id}:{_day()}"
    if redis is not None:
        try:
            ok, used = await redis.eval(_LUA_CONSUME, 1, key, str(n), str(limit), str(2 * 86400))
        except Exception as exc:
            _log.error("email_quota_store_unavailable", error=str(exc)[:200])
            if _durable_audit_required():
                raise EmailQuotaUnavailableError(str(exc)) from exc
        else:
            if not int(ok):
                raise EmailQuotaExceededError(limit, int(used))
            return QuotaResult(limit=limit, used=int(used))
    elif _durable_audit_required():
        raise EmailQuotaUnavailableError("no Redis configured for the shared email quota")
    # Development only: a per-process count.
    used = _local_used.get(key, 0)
    if used + n > limit:
        raise EmailQuotaExceededError(limit, used)
    _local_used[key] = used + n
    return QuotaResult(limit=limit, used=used + n)
