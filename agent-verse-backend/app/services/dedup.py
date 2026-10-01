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
- Race-free: one SET NX (set if not exists) claim per submission
- Released (compare-and-delete by goal_id) when the goal reaches a terminal
  state — in the API's event dispatch and in the worker — or when the
  submission itself fails, so the next submit runs

This prevents duplicate Celery tasks for the same tenant submitting the same
goal twice (e.g. from double-click, retry button, or CI pipelines).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
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


def _owner_key(goal_id: str) -> str:
    """Reverse index goal_id -> dedup key, so a terminal path that only knows the
    goal id (the worker, the API bridge) can release the claim."""
    return f"goal_dedup_owner:{goal_id}"


# Delete the dedup key only while it still names *this* goal (a newer claim by
# another goal must survive), then drop the reverse index.
_RELEASE_LUA = (
    "local k = redis.call('get', KEYS[1]) "
    "if k then "
    "  if redis.call('get', k) == ARGV[1] then redis.call('del', k) end "
    "  redis.call('del', KEYS[1]) "
    "end "
    "return 1"
)

_MEM_MAX = 10_000


def _text(val: Any) -> str:
    return val.decode() if isinstance(val, bytes) else str(val)


async def release_goal_claim(redis: Any, goal_id: str) -> None:
    """Release the dedup claim held by *goal_id* (no-op when it holds none)."""
    owner = _owner_key(goal_id)
    evaluate = getattr(redis, "eval", None)
    if callable(evaluate):
        await evaluate(_RELEASE_LUA, 1, owner, goal_id)
        return
    key = await redis.get(owner)
    if key:
        key = _text(key)
        current = await redis.get(key)
        if current is not None and _text(current) == goal_id:
            await redis.delete(key)
        await redis.delete(owner)


class GoalDeduplicator:
    """
    Redis-backed goal-level deduplication.

    Usage (goal submission):
        goal_id = new_id()
        winner = await dedup.claim(tenant_id, goal, goal_id, scope=scope)
        if winner:  # an identical goal is in flight
            return {"goal_id": winner, "deduplicated": True}
        ...
        await dedup.release_goal(goal_id)  # terminal / submission failed

    ``claim`` is one atomic ``SET NX EX``: two concurrent identical submissions
    cannot both win (get_existing + register used to be separate calls, and a
    lost register was ignored). A Redis error means *no dedup* — logged at
    warning — never a silent per-process fallback. ``get_existing`` /
    ``register`` / ``release`` remain for other callers (e-mail listener).
    """

    def __init__(self, redis: Any = None, ttl: int = _TTL) -> None:
        self._redis = redis
        self._ttl = ttl
        # In-memory store for the no-Redis (dev / test) build only: key ->
        # (goal_id, expires_at monotonic). Bounded and TTL'd — it used to grow
        # forever.
        self._mem: dict[str, tuple[str, float]] = {}
        self._mem_owner: dict[str, str] = {}

    # ── in-memory helpers (no Redis configured) ──────────────────────────────

    def _mem_get(self, key: str) -> str | None:
        entry = self._mem.get(key)
        if entry is None:
            return None
        if entry[1] <= time.monotonic():
            self._mem.pop(key, None)
            self._mem_owner.pop(entry[0], None)
            return None
        return entry[0]

    def _mem_set(self, key: str, goal_id: str) -> None:
        now = time.monotonic()
        if len(self._mem) >= _MEM_MAX:
            for k, (gid, exp) in list(self._mem.items()):
                if exp <= now:
                    self._mem.pop(k, None)
                    self._mem_owner.pop(gid, None)
            while len(self._mem) >= _MEM_MAX:
                k, (gid, _exp) = next(iter(self._mem.items()))
                self._mem.pop(k, None)
                self._mem_owner.pop(gid, None)
        self._mem[key] = (goal_id, now + self._ttl)
        self._mem_owner[goal_id] = key

    # ── atomic claim API (goal submission) ───────────────────────────────────

    async def claim(
        self, tenant_id: str, goal: str, goal_id: str, *, scope: str = ""
    ) -> str | None:
        """Atomically claim (tenant, goal, scope) for *goal_id*.

        Returns ``None`` when this goal won (it should run), else the goal_id of
        the in-flight identical goal it should be deduplicated onto.
        """
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is None:
            winner = self._mem_get(key)
            if winner is not None:
                return winner
            self._mem_set(key, goal_id)
            return None
        try:
            for _attempt in range(3):
                if await self._redis.set(key, goal_id, ex=self._ttl, nx=True):
                    try:
                        await self._redis.set(_owner_key(goal_id), key, ex=self._ttl)
                    except Exception as exc:  # the claim still expires with its TTL
                        logger.warning("goal_dedup_owner_write_failed", error=str(exc)[:120])
                    return None
                winner = await self._redis.get(key)
                if winner:
                    logger.info("goal_deduplicated", tenant=tenant_id, goal_id=_text(winner))
                    return _text(winner)
                # The winner's key expired between SET and GET: claim again.
        except Exception as exc:
            logger.warning(
                "goal_dedup_unavailable_running_without_dedup",
                tenant=tenant_id,
                error=str(exc)[:120],
            )
        return None

    async def release_goal(self, goal_id: str) -> None:
        """Release the claim held by *goal_id*; never touches another goal's claim."""
        if self._redis is None:
            key = self._mem_owner.pop(goal_id, None)
            if key is not None and self._mem.get(key, ("", 0.0))[0] == goal_id:
                self._mem.pop(key, None)
            return
        try:
            await release_goal_claim(self._redis, goal_id)
        except Exception as exc:  # the claim expires with its TTL
            logger.warning("goal_dedup_release_failed", goal_id=goal_id, error=str(exc)[:120])

    # ── legacy two-step API (kept for the e-mail listener) ───────────────────

    async def get_existing(self, tenant_id: str, goal: str, *, scope: str = "") -> str | None:
        """Return the in-flight goal_id for this (tenant, goal, scope), or None."""
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            try:
                val = await self._redis.get(key)
                if val:
                    goal_id = _text(val)
                    logger.info("goal_deduplicated", tenant=tenant_id, goal_id=goal_id)
                    return goal_id
            except Exception as exc:
                logger.warning("dedup_get_error", error=str(exc)[:60])
            return None
        return self._mem_get(key)

    async def register(self, tenant_id: str, goal: str, goal_id: str, *, scope: str = "") -> bool:
        """
        Register a new goal. Returns True if this is the first registration
        (i.e. no duplicate exists). Returns False if a duplicate was already
        registered concurrently (caller should use the existing goal_id).
        """
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            try:
                return bool(await self._redis.set(key, goal_id, ex=self._ttl, nx=True))
            except Exception as exc:
                logger.warning("dedup_register_error", error=str(exc)[:60])
                return True
        if self._mem_get(key) is not None:
            return False
        self._mem_set(key, goal_id)
        return True

    async def release(self, tenant_id: str, goal: str, *, scope: str = "") -> None:
        """Delete the dedup key so future identical goals can be submitted."""
        key = _dedup_key(tenant_id, goal, scope)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(key)
            return
        entry = self._mem.pop(key, None)
        if entry is not None:
            self._mem_owner.pop(entry[0], None)


# Module-level in-memory singleton (upgraded with Redis in lifespan)
_default_deduplicator = GoalDeduplicator()
