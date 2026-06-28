"""System endpoints: liveness/readiness health and Prometheus metrics."""

from __future__ import annotations

from typing import Any

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


@router.get("/.well-known/jwks.json")
async def jwks_endpoint(request: Request) -> dict[str, Any]:
    """Public key set for verifying agent JWT tokens (RS256).

    Cached in Redis for 10 minutes. Invalidated when a credential is issued or revoked.
    """
    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    cache_key = "jwks:cache"

    # Try Redis cache first
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                import json as _json
                return _json.loads(cached.decode() if isinstance(cached, bytes) else cached)
        except Exception:
            pass

    # Build from DB via AgentIdentityService
    from app.auth.agent_identity import _build_jwks

    svc = getattr(request.app.state, "agent_identity_service", None)
    db_factory = getattr(svc, "_db", None) if svc else None

    keys: list[dict[str, Any]] = []
    if db_factory:
        keys = await _build_jwks(db_factory)

    response_data: dict[str, Any] = {"keys": keys}

    # Cache for 10 minutes when Redis is available and keys were found
    if redis_client and keys:
        try:
            import json as _json
            await redis_client.setex(cache_key, 600, _json.dumps(response_data))
        except Exception:
            pass

    return response_data
