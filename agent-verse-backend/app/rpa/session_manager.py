"""Browser session manager — keeps Playwright sessions alive across multiple RPA calls.

Uses open-source Playwright for real browser automation.
Sessions are scoped to (session_id, tenant_id) for isolation.
Idle sessions auto-close after max_idle_seconds.
"""
from __future__ import annotations

import asyncio
import time
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
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None
        self._context = None
        self._page = None
        logger.info("browser_session_closed", session_id=self.session_id)


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
        max_sessions_per_tenant: int = 5,
        redis: Any = None,
    ) -> None:
        self._sessions: dict[tuple[str, str], BrowserSession] = {}
        self._headless = headless
        self._max_idle = max_idle_seconds
        self._max_per_tenant = max_sessions_per_tenant
        self._lock = asyncio.Lock()
        self._redis = redis
        self._SESSION_TTL = 3600  # 1 hour

    async def get_or_create(
        self, session_id: str, tenant_id: str
    ) -> BrowserSession:
        """Get existing session or create a new one, enforcing per-tenant cap."""
        key = (session_id, tenant_id)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing and existing.is_alive:
                existing.touch()
                return existing

            # Enforce per-tenant session cap
            tenant_active = sum(
                1 for (sid, tid) in self._sessions
                if tid == tenant_id and self._sessions[(sid, tid)].is_alive
            )
            if tenant_active >= self._max_per_tenant:
                # Close the oldest idle session to make room
                oldest_key = min(
                    ((sid, tid) for (sid, tid) in self._sessions if tid == tenant_id),
                    key=lambda k: self._sessions[k].last_used_at,
                    default=None,
                )
                if oldest_key:
                    old_session = self._sessions.pop(oldest_key)
                    asyncio.create_task(old_session.close())
                    logger.info(
                        "browser_session_evicted",
                        session_id=oldest_key[0],
                        tenant_id=tenant_id,
                        reason="cap_exceeded",
                    )
                else:
                    # All slots taken — return a simulation-only session
                    logger.warning(
                        "browser_session_cap_reached",
                        tenant_id=tenant_id,
                        limit=self._max_per_tenant,
                    )
                    return BrowserSession(session_id=session_id, tenant_id=tenant_id)

            session = await self._create_session(session_id, tenant_id)
            self._sessions[key] = session
            logger.info(
                "browser_session_created",
                session_id=session_id,
                tenant_id=tenant_id,
            )
            await self._register_in_redis(session)
            return session

    async def _create_session(
        self, session_id: str, tenant_id: str
    ) -> BrowserSession:
        session = BrowserSession(session_id=session_id, tenant_id=tenant_id)
        try:
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=self._headless)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="AgentVerse-RPA/1.0",
            )
            page = await context.new_page()
            session._playwright = pw
            session._browser = browser
            session._context = context
            session._page = page
        except ImportError:
            pass  # Playwright not installed — session has no page, uses simulation
        return session

    async def close(self, session_id: str, tenant_id: str) -> bool:
        """Close a specific session."""
        key = (session_id, tenant_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session:
            await session.close()
            await self._deregister_from_redis(session_id, tenant_id)
            return True
        return False

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
                session = self._sessions.pop(key, None)
            if session:
                await session.close()
                await self._deregister_from_redis(key[0], key[1])

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
        try:
            await self._redis.setex(
                key,
                self._SESSION_TTL,
                _json.dumps({
                    "session_id": session.session_id,
                    "tenant_id": session.tenant_id,
                    "created_at": session.created_at,
                    "current_url": session.current_url,
                }),
            )
        except Exception:
            pass

    async def _deregister_from_redis(self, session_id: str, tenant_id: str) -> None:
        """Remove session metadata from Redis on close."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(f"rpa_session:{tenant_id}:{session_id}")
        except Exception:
            pass

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
                    try:
                        result.append(_json.loads(raw))
                    except Exception:
                        pass
            return result
        except Exception:
            return self.list_active(tenant_id=tenant_id)

    def get_page(self, session_id: str) -> Any:
        """Return the live Playwright page for a session, or None if not found."""
        # Search across all tenants since we only have session_id here
        for (sid, tid), session in self._sessions.items():
            if sid == session_id and session.is_alive:
                return getattr(session, "_page", None) or getattr(session, "page", None)
        return None
