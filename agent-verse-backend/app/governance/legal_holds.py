"""Legal hold lifecycle management for AgentVerse audit events.

A legal hold prevents deletion of audit events related to specified resources
or users for the duration of the hold.  Required for:
  - Legal proceedings (e-discovery)
  - Regulatory investigations (SEC, GDPR erasure exceptions)
  - Internal compliance audits

Enforcement is two-tier:
  1. Application layer — LegalHoldManager.is_under_hold() checked before deletions
  2. Database layer  — PostgreSQL BEFORE DELETE trigger on audit_events (migration 0057)

Redis provides O(1) hold checks for high-throughput paths.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Redis key: "legal_hold:{tenant_id}" → Redis Set of resource_ids under hold
_CACHE_KEY = "legal_hold:{tenant_id}"
_CACHE_TTL = 3600  # 1 hour


class LegalHoldManager:
    """Full lifecycle for legal holds with Redis-cached membership checks."""

    def __init__(self, redis: Any, db_factory: Any) -> None:
        self._redis = redis
        self._db = db_factory

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_hold(
        self,
        tenant_id: str,
        name: str,
        resource_type: str,
        resource_ids: list[str] | None = None,
        user_ids: list[str] | None = None,
        date_range_start: datetime | None = None,
        date_range_end: datetime | None = None,
        legal_matter_id: str | None = None,
        description: str | None = None,
        created_by: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a new legal hold and warm the Redis cache."""
        hold_id = uuid.uuid4().hex
        resource_ids = resource_ids or []
        user_ids = user_ids or []

        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session, session.begin():
                    await session.execute(
                        text(
                            """
                            INSERT INTO legal_holds
                                (id, tenant_id, name, description, resource_type,
                                 resource_ids, user_ids, date_range_start, date_range_end,
                                 status, legal_matter_id, created_by, created_at, expires_at)
                            VALUES
                                (:id, :tid, :name, :desc, :rtype,
                                 :rids::jsonb, :uids::jsonb, :dstart, :dend,
                                 'active', :matter_id, :by, now(), :expires)
                            """
                        ),
                        {
                            "id": hold_id,
                            "tid": tenant_id,
                            "name": name,
                            "desc": description,
                            "rtype": resource_type,
                            "rids": json.dumps(resource_ids),
                            "uids": json.dumps(user_ids),
                            "dstart": date_range_start,
                            "dend": date_range_end,
                            "matter_id": legal_matter_id,
                            "by": created_by,
                            "expires": expires_at,
                        },
                    )
            except Exception as exc:
                logger.error("legal_hold_create_db_error", error=str(exc))

        # Warm Redis cache
        if self._redis is not None and resource_ids:
            try:
                cache_key = _CACHE_KEY.format(tenant_id=tenant_id)
                await self._redis.sadd(cache_key, *resource_ids)
                await self._redis.expire(cache_key, _CACHE_TTL)
            except Exception as exc:
                logger.warning("legal_hold_cache_warm_error", error=str(exc))

        logger.info("legal_hold_created", hold_id=hold_id, tenant_id=tenant_id)
        return {
            "id": hold_id,
            "tenant_id": tenant_id,
            "name": name,
            "resource_type": resource_type,
            "resource_ids": resource_ids,
            "user_ids": user_ids,
            "status": "active",
            "legal_matter_id": legal_matter_id,
            "created_at": datetime.now(UTC).isoformat(),
        }

    async def release_hold(
        self,
        tenant_id: str,
        hold_id: str,
        released_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Release a hold and rebuild the cache from the remaining active holds."""
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session, session.begin():
                    result = await session.execute(
                        text(
                            """
                            UPDATE legal_holds
                            SET status = 'released',
                                released_at = now(),
                                released_by = :by,
                                release_reason = :reason
                            WHERE id = :id AND tenant_id = :tid AND status = 'active'
                            RETURNING id
                            """
                        ),
                        {
                            "id": hold_id,
                            "tid": tenant_id,
                            "by": released_by,
                            "reason": reason,
                        },
                    )
                    if result.rowcount == 0:
                        return False
            except Exception as exc:
                logger.error("legal_hold_release_db_error", error=str(exc))
                return False

        # Rebuild cache so released resource_ids are no longer present
        await self.sync_cache(tenant_id)
        logger.info("legal_hold_released", hold_id=hold_id, tenant_id=tenant_id)
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def is_under_hold(self, tenant_id: str, resource_id: str) -> bool:
        """Return True if *resource_id* is under any active hold (O(1) via Redis)."""
        # Redis cache check (fast path)
        if self._redis is not None:
            try:
                cache_key = _CACHE_KEY.format(tenant_id=tenant_id)
                members = await self._redis.smembers(cache_key)
                if members:
                    str_members = {
                        m.decode() if isinstance(m, bytes) else m for m in members
                    }
                    return resource_id in str_members
            except Exception as exc:
                logger.warning("legal_hold_cache_check_error", error=str(exc))
                # Fall through to DB

        # DB fallback
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session:
                    result = await session.execute(
                        text(
                            """
                            SELECT 1 FROM legal_holds
                            WHERE tenant_id = :tid
                              AND status = 'active'
                              AND resource_ids @> :rid::jsonb
                            LIMIT 1
                            """
                        ),
                        {"tid": tenant_id, "rid": json.dumps([resource_id])},
                    )
                    return result.fetchone() is not None
            except Exception as exc:
                logger.warning("legal_hold_db_check_error", error=str(exc))

        return False

    async def list_holds(
        self, tenant_id: str, status: str = "active"
    ) -> list[dict[str, Any]]:
        """Return all holds of the given status for a tenant."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT id, name, description, resource_type, resource_ids,
                               user_ids, date_range_start, date_range_end,
                               status, legal_matter_id, created_by, created_at,
                               released_at, expires_at
                        FROM legal_holds
                        WHERE tenant_id = :tid AND status = :status
                        ORDER BY created_at DESC
                        """
                    ),
                    {"tid": tenant_id, "status": status},
                )
                rows = result.fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[1],
                    "description": r[2],
                    "resource_type": r[3],
                    "resource_ids": r[4] or [],
                    "user_ids": r[5] or [],
                    "date_range_start": r[6].isoformat() if r[6] else None,
                    "date_range_end": r[7].isoformat() if r[7] else None,
                    "status": r[8],
                    "legal_matter_id": r[9],
                    "created_by": r[10],
                    "created_at": r[11].isoformat() if r[11] else None,
                    "released_at": r[12].isoformat() if r[12] else None,
                    "expires_at": r[13].isoformat() if r[13] else None,
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("legal_hold_list_error", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Cache maintenance
    # ------------------------------------------------------------------

    async def sync_cache(self, tenant_id: str) -> None:
        """Rebuild the Redis set from the DB for *tenant_id*."""
        if self._redis is None or self._db is None:
            return
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT resource_ids FROM legal_holds
                        WHERE tenant_id = :tid AND status = 'active'
                        """
                    ),
                    {"tid": tenant_id},
                )
                rows = result.fetchall()

            cache_key = _CACHE_KEY.format(tenant_id=tenant_id)
            await self._redis.delete(cache_key)

            all_ids: list[str] = []
            for (rids,) in rows:
                if rids:
                    ids = rids if isinstance(rids, list) else json.loads(rids)
                    all_ids.extend(ids)

            if all_ids:
                await self._redis.sadd(cache_key, *all_ids)
                await self._redis.expire(cache_key, _CACHE_TTL)
        except Exception as exc:
            logger.warning("legal_hold_cache_sync_error", error=str(exc))
