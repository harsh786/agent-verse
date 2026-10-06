"""Per-client-IP sliding-window limits for unauthenticated endpoints.

Used by signup and the SSO token endpoints. Fixes, relative to the ad-hoc
limiters they replace:

* the client IP comes from :func:`app.auth.scope_enforcement._get_client_ip`
  (trusted-proxy aware) — ``request.client.host`` is the load balancer behind a
  proxy, so every caller shared ONE bucket (or, unproxied, the limit was fine
  but spoof-prone helpers were inconsistent);
* no Redis, or a Redis error, falls back to an in-process window instead of
  allowing everything (fail-open);
* the sorted-set member is unique per request — ``str(now_ms)`` collapsed all
  requests landing in the same millisecond into one entry;
* the 429 is raised OUTSIDE the Redis ``try`` (the auth limiter's own
  ``except Exception`` swallowed its 429, so it never limited anything).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from fastapi import HTTPException, Request

from app.observability.logging import get_logger

logger = get_logger(__name__)

# key -> timestamps (monotonic seconds) inside the window; bounded in size.
_local_windows: dict[str, deque[float]] = {}
_MAX_LOCAL_KEYS = 50_000


def _client_ip(request: Request) -> str:
    from app.auth.scope_enforcement import _get_client_ip

    try:
        return _get_client_ip(request) or "unknown"
    except Exception:
        return request.client.host if request.client else "unknown"


def _local_count(key: str, window_s: float) -> int:
    now = time.monotonic()
    if key not in _local_windows and len(_local_windows) >= _MAX_LOCAL_KEYS:
        _local_windows.pop(next(iter(_local_windows)))
    window = _local_windows.setdefault(key, deque())
    while window and now - window[0] > window_s:
        window.popleft()
    window.append(now)
    return len(window)


async def _redis_count(redis: Any, key: str, window_s: float) -> int:
    now_ms = int(time.time() * 1000)
    window_ms = int(window_s * 1000)
    member = f"{now_ms}:{uuid.uuid4().hex}"
    ttl = int(window_s) * 2
    pipe = redis.pipeline() if hasattr(redis, "pipeline") else None
    if pipe is not None:
        pipe.zremrangebyscore(key, 0, now_ms - window_ms)
        pipe.zadd(key, {member: now_ms})
        pipe.zcard(key)
        pipe.expire(key, ttl)
        results = await pipe.execute()
        return int(results[2])
    await redis.zremrangebyscore(key, 0, now_ms - window_ms)
    await redis.zadd(key, {member: now_ms})
    count = int(await redis.zcard(key))
    await redis.expire(key, ttl)
    return count


async def enforce_ip_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_s: float,
    redis: Any = None,
    detail: str = "Too many requests. Please wait before trying again.",
) -> None:
    """Raise HTTP 429 when this client IP exceeded *limit* hits per *window_s*."""
    await enforce_key_rate_limit(
        f"{bucket}:{_client_ip(request)}",
        bucket=bucket,
        limit=limit,
        window_s=window_s,
        redis=redis,
        detail=detail,
    )


async def enforce_key_rate_limit(
    key: str,
    *,
    bucket: str,
    limit: int,
    window_s: float,
    redis: Any = None,
    detail: str = "Too many requests. Please wait before trying again.",
) -> None:
    """Raise HTTP 429 when *key* (e.g. ``"<bucket>:<tenant_id>"``) exceeded *limit*
    hits per *window_s* — the same Redis window (in-process without Redis)."""
    count: int
    if redis is not None:
        try:
            count = await _redis_count(redis, key, window_s)
        except Exception as exc:
            logger.warning("ip_rate_limit_redis_error", bucket=bucket, error=str(exc))
            count = _local_count(key, window_s)
    else:
        count = _local_count(key, window_s)
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(int(window_s))},
        )
