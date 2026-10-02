"""Browser session manager — keeps Playwright sessions alive across multiple RPA calls.

Uses open-source Playwright for real browser automation.
Sessions are scoped to (session_id, tenant_id) for isolation.
Idle sessions auto-close after max_idle_seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BrowserSession:
    session_id: str
    tenant_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    current_url: str = ""
    # True once the context's SSRF request/WebSocket guard is installed
    # (app.net.browser_guard). The executor refuses a page that is not guarded.
    ssrf_guarded: bool = False
    allowed_domains: list[str] | None = None
    _playwright: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _context: Any = field(default=None, repr=False)
    _page: Any = field(default=None, repr=False)

    @property
    def page(self) -> Any:
        return self._page

    @property
    def is_alive(self) -> bool:
        return self._browser is not None

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    async def close(self) -> None:
        """Close the browser and clean up resources."""
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
        if self._playwright:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._context = None
        self._page = None
        logger.info("browser_session_closed", session_id=self.session_id)


class SessionOnAnotherReplicaError(RuntimeError):
    """The session's live browser belongs to another API replica.

    Browser pages cannot move between processes; opening a fresh blank browser
    under the same id would silently lose the page state the caller expects.
    """

    def __init__(self, session_id: str, owner_replica: str) -> None:
        self.session_id = session_id
        self.owner_replica = owner_replica
        super().__init__(
            f"RPA session {session_id} is live on another API replica ({owner_replica}); "
            "browser sessions are per-replica. Route the request to that replica "
            "(sticky sessions) or open a new session."
        )


class BrowserSessionCapError(RuntimeError):
    """The tenant's (global) or this host's browser-session cap is full (API: 429).

    Raised instead of closing another goal's live browser to make room.
    """

    def __init__(self, scope: str, limit: int, active_sessions: list[str]) -> None:
        self.scope = scope
        self.limit = limit
        self.active_sessions = active_sessions
        super().__init__(
            f"browser session limit reached ({scope} limit {limit}); close one of the "
            f"active sessions first: {', '.join(active_sessions) or 'none listed'}"
        )


# Lease on a tenant's browser slot (Redis lease set); refreshed on every use and by
# cleanup_expired's heartbeat, so a crashed holder's slot frees itself.
_SLOT_LEASE_S = 120.0
_HOST_SLOT_KEY = "__host__"


class BrowserSessionManager:
    """Manages live Playwright browser sessions across RPA tool calls.

    Each session keeps its browser context alive so that multi-step
    workflows (open → click → extract → screenshot) share one page state.

    Open source: uses Playwright (not Selenium or cloud browsers).
    """

    def __init__(
        self,
        headless: bool = True,
        max_idle_seconds: int = 300,
        max_sessions_per_tenant: int | None = None,
        redis: Any = None,
        allowed_domains: list[str] | None = None,
        max_browsers_per_host: int | None = None,
    ) -> None:
        from app.core.config import get_settings
        from app.reliability.bulkhead import LocalSlotCounter

        settings = get_settings()
        # Default SSRF egress allowlist for new sessions (None → public only).
        self._allowed_domains = allowed_domains
        self._sessions: dict[tuple[str, str], BrowserSession] = {}
        self._headless = headless
        self._max_idle = max_idle_seconds
        # Per tenant across EVERY replica and worker (Redis lease set).
        self._max_per_tenant = int(
            max_sessions_per_tenant or settings.rpa_max_sessions_per_tenant
        )
        # Chromium processes this process may run, all tenants together.
        self._max_per_host = int(max_browsers_per_host or settings.rpa_max_browsers_per_host)
        # Process-local counts: the host cap, and the per-tenant fallback while
        # Redis is unreachable (still bounded per replica).
        self._local_slots = LocalSlotCounter()
        # Which slot backs each session: "lease" (Redis) or "local".
        self._slot_kind: dict[tuple[str, str], str] = {}
        # At the cap, only a session idle at least this long may be evicted.
        self._evict_idle_s = float(settings.rpa_session_evict_idle_s)
        self._lock = asyncio.Lock()
        self._redis = redis
        self._SESSION_TTL = 3600  # 1 hour
        # Identifies this process's live browsers in the shared Redis registry.
        self.replica_id = uuid.uuid4().hex[:12]

    async def get_or_create(
        self,
        session_id: str,
        tenant_id: str,
        *,
        allowed_domains: list[str] | None = None,
    ) -> BrowserSession:
        """Get existing session or create a new one, enforcing per-tenant cap.

        ``allowed_domains`` is the SSRF egress allowlist the new session's
        browser guard enforces (defaults to the manager's).
        """
        key = (session_id, tenant_id)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing and existing.is_alive:
                existing.touch()
                await self._refresh_slot(session_id, tenant_id)
                return existing
            owner = await self.live_elsewhere(session_id, tenant_id)
            if owner is not None:
                raise SessionOnAnotherReplicaError(session_id, owner)

            try:
                await self._acquire_slots(session_id, tenant_id)
            except BrowserSessionCapError as cap:
                # Only a session idle past the grace period may make room; a
                # recently used one may be mid-workflow for another goal.
                if not await self._evict_one_idle_locked(
                    tenant_id if cap.scope == "tenant" else None
                ):
                    raise
                await self._acquire_slots(session_id, tenant_id)
            try:
                session = await self._create_session(
                    session_id, tenant_id, allowed_domains=allowed_domains
                )
            except BaseException:
                await self._release_slots(session_id, tenant_id)
                raise
            self._sessions[key] = session
            logger.info(
                "browser_session_created",
                session_id=session_id,
                tenant_id=tenant_id,
            )
            await self._register_in_redis(session)
            return session

    # ── Caps: per tenant (global, Redis lease set) and per host ─────────────────

    @staticmethod
    def _lease_key(tenant_id: str) -> str:
        return f"rpa:leases:{tenant_id}"

    async def _acquire_slots(self, session_id: str, tenant_id: str) -> None:
        """Take a host slot and one of the tenant's slots, or raise the cap error.

        Never closes another session to make room.
        """
        from app.reliability.bulkhead import RedisLeaseLimiter

        if not self._local_slots.try_acquire(_HOST_SLOT_KEY, self._max_per_host):
            mine = [sid for (sid, tid) in self._sessions if tid == tenant_id]
            raise BrowserSessionCapError("host", self._max_per_host, mine)
        key = (session_id, tenant_id)
        try:
            if self._redis is not None:
                limiter = RedisLeaseLimiter(self._redis)
                try:
                    ok = await limiter.try_acquire(
                        self._lease_key(tenant_id),
                        session_id,
                        limit=self._max_per_tenant,
                        lease_s=_SLOT_LEASE_S,
                    )
                except Exception as exc:
                    logger.warning("rpa_slot_lease_unavailable", error=str(exc)[:200])
                else:
                    if not ok:
                        active = await self._active_lease_ids(tenant_id)
                        raise BrowserSessionCapError("tenant", self._max_per_tenant, active)
                    self._slot_kind[key] = "lease"
                    return
            if not self._local_slots.try_acquire(f"tenant:{tenant_id}", self._max_per_tenant):
                raise BrowserSessionCapError(
                    "tenant",
                    self._max_per_tenant,
                    [sid for (sid, tid) in self._sessions if tid == tenant_id],
                )
            self._slot_kind[key] = "local"
        except BaseException:
            self._local_slots.release(_HOST_SLOT_KEY)
            raise

    async def _evict_one_idle_locked(self, tenant_id: str | None) -> bool:
        """Close the least-recently-used session idle past the grace period.

        ``tenant_id`` limits candidates to that tenant (tenant cap); ``None`` means
        any tenant (host cap). Caller holds ``self._lock``. The eviction is
        awaited and fully undone: browser closed, registry record deleted, slot
        released — it used to be a fire-and-forget close that left the Redis
        record claiming the session was live.
        """
        cutoff = time.monotonic() - self._evict_idle_s
        candidates = [
            (k, s)
            for k, s in self._sessions.items()
            if (tenant_id is None or k[1] == tenant_id) and s.last_used_at < cutoff
        ]
        if not candidates:
            return False
        key, victim = min(candidates, key=lambda kv: kv[1].last_used_at)
        self._sessions.pop(key, None)
        with contextlib.suppress(Exception):
            await victim.close()
        await self._deregister_from_redis(key[0], key[1])
        await self._release_slots(key[0], key[1])
        logger.info(
            "browser_session_evicted",
            session_id=key[0],
            tenant_id=key[1],
            reason="idle_past_grace_at_cap",
        )
        return True

    async def _release_slots(self, session_id: str, tenant_id: str) -> None:
        from app.reliability.bulkhead import RedisLeaseLimiter

        kind = self._slot_kind.pop((session_id, tenant_id), None)
        if kind is None:
            return
        self._local_slots.release(_HOST_SLOT_KEY)
        if kind == "local":
            self._local_slots.release(f"tenant:{tenant_id}")
        elif self._redis is not None:
            with contextlib.suppress(Exception):
                await RedisLeaseLimiter(self._redis).release(
                    self._lease_key(tenant_id), session_id
                )

    async def _refresh_slot(self, session_id: str, tenant_id: str) -> None:
        """Keep a live session's tenant lease from expiring while it is used."""
        from app.reliability.bulkhead import RedisLeaseLimiter

        if self._slot_kind.get((session_id, tenant_id)) != "lease" or self._redis is None:
            return
        with contextlib.suppress(Exception):
            await RedisLeaseLimiter(self._redis).refresh(
                self._lease_key(tenant_id), session_id, lease_s=_SLOT_LEASE_S
            )

    async def _active_lease_ids(self, tenant_id: str) -> list[str]:
        from app.reliability.bulkhead import RedisLeaseLimiter

        try:
            return sorted(await RedisLeaseLimiter(self._redis).members(self._lease_key(tenant_id)))
        except Exception:
            return sorted(sid for (sid, tid) in self._sessions if tid == tenant_id)

    async def _create_session(
        self,
        session_id: str,
        tenant_id: str,
        *,
        allowed_domains: list[str] | None = None,
    ) -> BrowserSession:
        """Launch a browser whose context carries the SSRF request guard.

        Only the first goto URL used to be validated; Chromium then followed
        redirects, click navigations and subresource requests to internal hosts
        (169.254.169.254, localhost, RFC-1918) unchecked. Every request is now
        validated by app.net.browser_guard. If the guard cannot be installed the
        browser is closed and the error raised (fail closed).
        """
        domains = allowed_domains if allowed_domains is not None else self._allowed_domains
        session = BrowserSession(
            session_id=session_id, tenant_id=tenant_id, allowed_domains=domains
        )
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return session  # Playwright not installed — no page; the executor fails closed

        from app.net.browser_guard import new_guarded_context

        pw = await async_playwright().start()
        browser: Any = None
        try:
            browser = await pw.chromium.launch(headless=self._headless)
            context = await new_guarded_context(
                browser,
                allowed_domains=domains,
                context="rpa_browser",
                viewport={"width": 1280, "height": 720},
                user_agent="AgentVerse-RPA/1.0",
            )
            page = await context.new_page()
        except BaseException:
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            with contextlib.suppress(Exception):
                await pw.stop()
            raise
        session._playwright = pw
        session._browser = browser
        session._context = context
        session._page = page
        session.ssrf_guarded = True
        return session

    async def close(self, session_id: str, tenant_id: str) -> bool:
        """Close a specific session."""
        key = (session_id, tenant_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            await session.close()
            await self._deregister_from_redis(session_id, tenant_id)
            await self._release_slots(session_id, tenant_id)
            return True
        return False

    async def close_all(self) -> int:
        """Close every session this manager holds (owner shutdown)."""
        async with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()
        for (sid, tid), session in sessions:
            with contextlib.suppress(Exception):
                await session.close()
            await self._deregister_from_redis(sid, tid)
            await self._release_slots(sid, tid)
        return len(sessions)

    async def cleanup_expired(self) -> int:
        """Close sessions idle longer than max_idle_seconds."""
        cutoff = time.monotonic() - self._max_idle
        to_close: list[tuple[str, str]] = []
        async with self._lock:
            for key, session in list(self._sessions.items()):
                if session.last_used_at < cutoff:
                    to_close.append(key)

        for key in to_close:
            async with self._lock:
                closed_session = self._sessions.pop(key, None)
            if closed_session:
                await closed_session.close()
                await self._deregister_from_redis(key[0], key[1])
                await self._release_slots(key[0], key[1])

        return len(to_close)

    def list_active(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List all active sessions, optionally filtered by tenant."""
        result = []
        for (sid, tid), session in self._sessions.items():
            if tenant_id and tid != tenant_id:
                continue
            result.append(
                {
                    "session_id": sid,
                    "tenant_id": tid,
                    "current_url": session.current_url,
                    "is_alive": session.is_alive,
                    "idle_seconds": round(time.monotonic() - session.last_used_at),
                }
            )
        return result

    # ── Redis session registry ─────────────────────────────────────────────────

    async def _register_in_redis(self, session: BrowserSession) -> None:
        """Persist session metadata to Redis for visibility across restarts."""
        if self._redis is None:
            return
        import json as _json

        key = f"rpa_session:{session.tenant_id}:{session.session_id}"
        with contextlib.suppress(Exception):
            await self._redis.setex(
                key,
                self._SESSION_TTL,
                _json.dumps(
                    {
                        "session_id": session.session_id,
                        "tenant_id": session.tenant_id,
                        "created_at": session.created_at,
                        "current_url": session.current_url,
                        # The page lives only in this process (see live_elsewhere).
                        "replica_id": self.replica_id,
                        "live": session.is_alive,
                    }
                ),
            )

    async def live_elsewhere(self, session_id: str, tenant_id: str) -> str | None:
        """Replica id holding this session's live browser, if it is not this one.

        ``None`` when the session is live here, unknown, closed, or the registry
        is unavailable/unreadable (then this replica may open it as before).
        """
        local = self._sessions.get((session_id, tenant_id))
        if local is not None and local.is_alive:
            return None
        if self._redis is None:
            return None
        import json as _json

        try:
            raw = await self._redis.get(f"rpa_session:{tenant_id}:{session_id}")
            record = _json.loads(raw) if raw else None
        except Exception:
            return None
        if not isinstance(record, dict) or not record.get("live"):
            return None
        if record.get("tenant_id") != tenant_id:
            return None
        owner = str(record.get("replica_id") or "")
        return owner if owner and owner != self.replica_id else None

    async def _deregister_from_redis(self, session_id: str, tenant_id: str) -> None:
        """Remove session metadata from Redis on close."""
        if self._redis is None:
            return
        with contextlib.suppress(Exception):
            await self._redis.delete(f"rpa_session:{tenant_id}:{session_id}")

    async def list_active_from_redis(self, tenant_id: str) -> list[dict[str, Any]]:
        """List active sessions persisted in Redis (survives restarts)."""
        if self._redis is None:
            return self.list_active(tenant_id=tenant_id)
        import json as _json

        pattern = f"rpa_session:{tenant_id}:*"
        try:
            keys = await self._redis.keys(pattern)
            result = []
            for key in keys:
                raw = await self._redis.get(key)
                if raw:
                    with contextlib.suppress(Exception):
                        result.append(_json.loads(raw))
            return result
        except Exception:
            return self.list_active(tenant_id=tenant_id)

    def get_page(self, session_id: str, *, tenant_id: str) -> Any:
        """Return the tenant's live Playwright page for a session, or None.

        Scoped to the tenant: it used to search every tenant's sessions by id, so
        any rpa:read key could screenshot another tenant's live browser.
        """
        session = self._sessions.get((session_id, tenant_id))
        if session is not None and session.is_alive:
            return getattr(session, "_page", None) or getattr(session, "page", None)
        return None
