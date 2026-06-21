"""System endpoints: liveness/readiness health and Prometheus metrics."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.observability.health import HealthRegistry
from app.observability.metrics import render_metrics

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Readiness check across all registered dependencies (503 if any are down)."""
    registry: HealthRegistry = request.app.state.health
    healthy, checks = await registry.run()
    payload = {"status": "healthy" if healthy else "unhealthy", "checks": checks}
    return JSONResponse(payload, status_code=200 if healthy else 503)


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
