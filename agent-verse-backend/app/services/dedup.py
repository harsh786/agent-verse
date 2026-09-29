"""
Goal-level request deduplication.

When a tenant submits an identical goal within a short window (default 60s),
the second submission does NOT launch a new agent execution — it attaches to
the first goal and polls for the same result.

Key design:
- Dedup key = sha256(tenant_id + "\\x00" + goal.strip().lower() + "\\x00" + scope)[:32]
  where *scope* fingerprints everything else that changes what the run does
  (agent, dry-run, workflow mode, priority, caller permissions, execution
  context incl. autonomy mode / runtime profile — see :func:`goal_dedup_scope`).
  Text alone was too coarse: a real submission was "deduplicated" onto an
  in-flight dry run, or onto a run by a different agent.
- Storage: Redis SET with 60s TTL, value = first goal_id
- Race-free: SET NX (set if not exists) is atomic
- If the original goal fails, the dedup key is deleted so the next submit retries

This prevents duplicate Celery tasks for the same tenant submitting the same
goal twice (e.g. from double-click, retry button, or CI pipelines).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_TTL = 60  # seconds


def _dedup_key(tenant_id: str, goal: str, scope: str = "") -> str:
    payload = tenant_id + "\x00" + goal.strip().lower()
    if scope:
        payload += "\x00" + scope
    return "goal_dedup:" + hashlib.sha256(payload.encode()).hexdigest()[:32]


def goal_dedup_scope(
    *,
    agent_id: str | None,
    dry_run: bool,
    workflow_mode: str,
    priority: str,
    execution_context: dict[str, Any] | None,
    roles: tuple[str, ...] | list[str] = (),
    scopes: tuple[str, ...] | list[str] = (),
) -> str:
    """Fingerprint of everything besides the text that decides how a goal runs.

    Two submissions only share one in-flight run when ALL of these match: the
    requested agent (``None`` = auto-routed), dry-run vs live, workflow mode,
    priority, the caller's permissions (roles/scopes) and the full execution
    context (autonomy mode, strategy / runtime profile, persistence settings,
    model override, attachments, routing decision, ...). When in doubt the key
    differs: a missed dedup costs one extra run, a wrong one silently drops a
    real submission.
    """
    material = {
        "agent_id": agent_id or "",
        "dry_run": bool(dry_run),
        "workflow_mode": workflow_mode or "single_agent",
        "priority": priority or "normal",
        "roles": sorted(str(r) for r in roles),
        "scopes": sorted(str(s) for s in scopes),
        "execution_context": execution_context or {},
    }
    encoded = json.dumps(material, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:32]


class GoalDeduplicator:
    """
    Redis-backed goal-level deduplication.

    Usage:
        dedup = GoalDeduplicator(redis=redis_client)

        # Before creating a new goal:
        existing_id = await dedup.get_existing(tenant_id, goal, scope=scope)
        if existing_id:
            return {"goal_id": existing_id, "deduplicated": True}

        # After creating the goal:
        await dedup.register(tenant_id, goal, new_goal_id, scope=scope)

        # When the goal completes or fails:
        await dedup.release(tenant_id, goal, scope=scope)
    """

    def __init__(self, redis: Any = None, ttl: int = _TTL) -> None:
        self._redis = redis
        self._ttl = ttl
        # In-memory fallback: key → goal_id
        self._mem: dict[str, str] = {}

    async def get_existing(self, tenant_id: str, goal: str, *, scope: str = "") -> str | None:
        """Return the in-flight goal_id for this (tenant, goal, scope), or None."""
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            try:
                val = await self._redis.get(key)
                if val:
                    goal_id = val.decode() if isinstance(val, bytes) else val
                    logger.info("goal_deduplicated", tenant=tenant_id, goal_id=goal_id)
                    return goal_id
            except Exception as exc:
                logger.debug("dedup_get_error", error=str(exc)[:60])
        return self._mem.get(key)

    async def register(
        self, tenant_id: str, goal: str, goal_id: str, *, scope: str = ""
    ) -> bool:
        """
        Register a new goal. Returns True if this is the first registration
        (i.e. no duplicate exists). Returns False if a duplicate was already
        registered concurrently (caller should use the existing goal_id).
        """
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            try:
                # SET NX: only sets if key doesn't exist (atomic)
                set_result = await self._redis.set(key, goal_id, ex=self._ttl, nx=True)
                # falsy set_result means the key already existed — someone else
                # registered first
                return bool(set_result)
            except Exception as exc:
                logger.debug("dedup_register_error", error=str(exc)[:60])
        self._mem[key] = goal_id
        return True

    async def release(self, tenant_id: str, goal: str, *, scope: str = "") -> None:
        """Delete the dedup key so future identical goals can be submitted."""
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(key)
        self._mem.pop(key, None)


# Module-level in-memory singleton (upgraded with Redis in lifespan)
_default_deduplicator = GoalDeduplicator()
