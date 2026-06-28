"""Cache warmer: pre-populates the Redis permission cache at startup.

Runs during ``lifespan`` to eliminate cold-start cache misses for tenants
that were active in the last hour.  Runs non-blocking (errors are logged,
not raised) so startup failures do not prevent the app from serving traffic.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


async def warm_permission_cache(
    redis: Any,
    db_factory: Any,
    min_daily_calls: int = 10_000,
) -> None:
    """Pre-warm the permission cache for recently-active tenants.

    Queries the ``goals`` table for tenants that created goals in the last
    hour, then pre-populates a ``PermissionCache`` entry for each of their
    API keys.  Capped at 50,000 entries to bound startup time.

    Args:
        redis:           Redis client (already connected).
        db_factory:      Async session factory (``async_sessionmaker``).
        min_daily_calls: Minimum daily API calls threshold (default 10,000).
                         Only keys meeting this threshold are warmed.
                         In practice we use a 1-hour proxy: goals in last hour.
    """
    if redis is None or db_factory is None:
        return

    try:
        from sqlalchemy import text as _t

        from app.auth.permission_cache import PermissionCache
        from app.auth.scope_enforcement import ScopeEnforcementMiddleware

        # Step 1: find recently-active tenant/key pairs
        async with db_factory() as session:
            rows = (
                await session.execute(
                    _t(
                        """
                        SELECT DISTINCT ak.tenant_id, ak.id AS key_id
                        FROM api_keys ak
                        JOIN goals g ON g.tenant_id = ak.tenant_id
                        WHERE g.created_at > NOW() - INTERVAL '1 hour'
                          AND ak.is_active = TRUE
                        LIMIT 50000
                        """
                    )
                )
            ).fetchall()

        if not rows:
            logger.info("cache_warmer_no_active_tenants")
            return

        cache = PermissionCache(redis)

        # Step 2: load and cache scopes for each active key
        warmed = 0
        async with db_factory() as session:
            for tenant_id, key_id in rows:
                try:
                    scopes = await ScopeEnforcementMiddleware._load_scopes(
                        db_factory=db_factory,
                        tenant_id=str(tenant_id),
                        key_id=str(key_id),
                        roles=(),
                    )
                    await cache.set(str(tenant_id), str(key_id), scopes)
                    warmed += 1
                except Exception as exc:
                    logger.warning(
                        "cache_warmer_key_failed",
                        tenant_id=str(tenant_id),
                        key_id=str(key_id),
                        error=str(exc),
                    )

        logger.info("cache_warmer_complete", warmed=warmed, total=len(rows))

    except Exception as exc:
        # Non-fatal: startup should not fail if cache warming fails
        logger.warning("cache_warmer_failed", error=str(exc))
