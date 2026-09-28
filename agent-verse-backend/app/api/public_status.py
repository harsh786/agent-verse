"""Public status page API — no authentication required.

Reads the app's real :class:`~app.observability.health.HealthRegistry`
(``app.state.health``). It used to read ``app.state.health_registry`` — which
nothing sets — and call a ``run_all()`` the registry does not have, so it always
reported "operational" (and the router was never mounted, so /status was a 404).
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Request

router = APIRouter(prefix="/status", tags=["status"])
_log = structlog.get_logger(__name__)


@router.get("")
async def get_public_status(request: Request) -> dict[str, Any]:
    """Public system health: ``operational`` | ``degraded`` | ``unknown``.

    ``unknown`` when no dependency checks are registered or they could not be
    run — the API answering this request is not evidence its dependencies work.
    Component details carry only up/down, never error text.
    """
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
