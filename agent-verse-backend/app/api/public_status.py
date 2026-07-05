"""Public status page API — no authentication required."""
from __future__ import annotations
import time
from typing import Any
from fastapi import APIRouter, Request

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
async def get_public_status(request: Request) -> dict[str, Any]:
    """Public system health — used by the status page, no auth required."""
    health_registry = getattr(request.app.state, "health_registry", None)
    checks: dict[str, Any] = {}
    overall = "operational"

    if health_registry is not None:
        try:
            results = await health_registry.run_all()
            for name, result in results.items():
                healthy = getattr(result, "healthy", True)
                latency = getattr(result, "latency_ms", 0.0)
                status = "operational" if healthy else "degraded"
                checks[name] = {"status": status, "latency_ms": round(float(latency or 0), 2)}
                if not healthy:
                    overall = "degraded"
        except Exception:
            overall = "unknown"
            checks["api"] = {"status": "unknown"}
    else:
        checks["api"] = {"status": "operational"}

    return {
        "status": overall,
        "components": checks,
        "timestamp": time.time(),
        "page_title": "AgentVerse System Status",
    }
