"""Public status page API — no authentication required.

Reads the app's real :class:`~app.observability.health.HealthRegistry`
(``app.state.health``). It used to read ``app.state.health_registry`` — which
nothing sets — and call a ``run_all()`` the registry does not have, so it always
reported "operational" (and the router was never mounted, so /status was a 404).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog
from fastapi import APIRouter, Request

router = APIRouter(prefix="/status", tags=["status"])
_log = structlog.get_logger(__name__)

_DEFAULT_CACHE_SECONDS = 10.0


def _now() -> float:
    return time.monotonic()


def _cache_seconds() -> float:
    """``PUBLIC_STATUS_CACHE_SECONDS`` (default 10; 0 disables the cache)."""
    raw = os.getenv("PUBLIC_STATUS_CACHE_SECONDS", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_CACHE_SECONDS
    except ValueError:
        return _DEFAULT_CACHE_SECONDS
    return max(0.0, value)


def _lock_for(state: Any) -> asyncio.Lock:
    """The app's single-flight lock for the current event loop."""
    loop = asyncio.get_running_loop()
    held = getattr(state, "_public_status_lock", None)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        state._public_status_lock = held
    lock: asyncio.Lock = held[1]
    return lock


@router.get("")
async def get_public_status(request: Request) -> dict[str, Any]:
    """Public system health: ``operational`` | ``degraded`` | ``unknown``.

    ``unknown`` when no dependency checks are registered or they could not be
    run — the API answering this request is not evidence its dependencies work.
    Component details carry only up/down, never error text.

    a10-F244-01: this route is anonymous, and every request used to run every
    dependency check. The report is cached per app for
    ``PUBLIC_STATUS_CACHE_SECONDS`` and concurrent misses share one run, so a
    flood of anonymous GETs costs at most one check round per window.
    ``timestamp`` is when the checks ran.
    """
    ttl = _cache_seconds()
    if ttl <= 0:
        return await _compute_status(request)
    state = request.app.state
    cached = getattr(state, "_public_status_cache", None)
    if cached is not None and cached[0] > _now():
        return dict(cached[1])
    async with _lock_for(state):
        cached = getattr(state, "_public_status_cache", None)
        if cached is not None and cached[0] > _now():
            return dict(cached[1])
        payload = await _compute_status(request)
        state._public_status_cache = (_now() + ttl, payload)
        return dict(payload)


async def _compute_status(request: Request) -> dict[str, Any]:
    registry = getattr(request.app.state, "health", None)
    components: dict[str, Any] = {"api": {"status": "operational"}}
    overall = "unknown"
    checks = list(getattr(registry, "checks", []) or []) if registry is not None else []
    if checks:
        try:
            healthy, report = await registry.run()  # type: ignore[union-attr]
            for name, item in report.items():
                up = isinstance(item, dict) and item.get("status") == "up"
                components[name] = {"status": "operational" if up else "degraded"}
            overall = "operational" if healthy else "degraded"
        except Exception as exc:
            _log.warning("public_status_check_failed", error=str(exc)[:200])
            overall = "unknown"
    failed_routers = list(getattr(request.app.state, "failed_routers", []) or [])
    if failed_routers:
        components["api"] = {"status": "degraded"}
        if overall == "operational":
            overall = "degraded"

    return {
        "status": overall,
        "components": components,
        "timestamp": time.time(),
        "page_title": "AgentVerse System Status",
    }
