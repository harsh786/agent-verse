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
    payload = {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        # Alias for backward-compat: old frontend code reads `health.dependencies`
        "dependencies": checks,
        # Non-fatal capabilities: reported for visibility, never counted toward
        # readiness (a deployment without embeddings is legitimate). Lets an
        # operator see "embedder unavailable" without uploading a document.
        "capabilities": {"embedder": _embedder_capability(request)},
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: 503 until the lifespan's essential phase AND every gating
    background warm-up (app.core.startup) has finished, then the dependency checks.

    Uvicorn binds as soon as essential state is loaded; warm caches, catalogue
    seeding and memory hydration continue in the background. A load balancer or
    orchestrator should route traffic on this endpoint; ``/health`` stays the
    fast liveness/dependency probe.
    """
    tracker = getattr(request.app.state, "startup", None)
    if tracker is not None and not tracker.ready:
        snapshot = tracker.snapshot()
        return JSONResponse(
            {
                "status": "starting",
                "essential_done": snapshot["essential_done"],
                "pending": snapshot["pending"],
            },
            status_code=503,
        )
    registry: HealthRegistry = request.app.state.health
    healthy, checks = await registry.run()
    payload: dict[str, Any] = {"status": "ready" if healthy else "unhealthy", "checks": checks}
    if tracker is not None:
        payload["startup_seconds"] = tracker.snapshot()["startup_seconds"]
    return JSONResponse(payload, status_code=200 if healthy else 503)


def _embedder_capability(request: Request) -> dict[str, Any]:
    """Embedder status for the unauthenticated /health route (no error text)."""
    resolution = getattr(request.app.state, "embedder_resolution", None)
    if resolution is not None and hasattr(resolution, "public_summary"):
        summary: dict[str, Any] = resolution.public_summary()
        if summary.get("status") != "available" and getattr(request.app.state, "embedder", None):
            # An embedder injected after resolution (tests / custom wiring).
            summary["status"] = "available"
        return summary
    available = getattr(request.app.state, "embedder", None) is not None
    return {"status": "available" if available else "unknown"}


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    # Negotiate: Prometheus sends Accept: application/openmetrics-text → we return
    # OpenMetrics (with trace exemplars); everything else gets legacy text/plain.
    body, content_type = render_metrics(request.headers.get("accept", ""))
    return Response(content=body, media_type=content_type)


@router.get("/.well-known/jwks.json")
async def jwks_endpoint(request: Request, tenant_id: str | None = None) -> dict[str, Any]:
    """Public key set for verifying agent JWT tokens (RS256).

    Without ``tenant_id``: the platform-wide set, cached in Redis for 10 minutes
    and invalidated when a credential is issued or revoked. With ``tenant_id``
    (the tenant is in every token's ``iss``): only that tenant's keys, read under
    its RLS context and not cached (revocation must take effect immediately).
    """
    from app.auth.agent_identity import _build_jwks

    # agent_credentials is FORCE-RLS; the request factory under the NOBYPASSRLS
    # role saw no rows without a tenant GUC, so this endpoint served an EMPTY key
    # set. The cross-tenant read uses the maintenance (BYPASSRLS) factory.
    svc = getattr(request.app.state, "agent_identity_service", None)
    db_factory = getattr(request.app.state, "system_db_session_factory", None) or (
        getattr(svc, "_db", None) if svc else None
    )

    if tenant_id:
        tenant_keys = await _build_jwks(db_factory, tenant_id=tenant_id) if db_factory else []
        return {"keys": tenant_keys}

    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    cache_key = "jwks:cache"

    # Try Redis cache first
    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                import json as _json

                cached_data = _json.loads(cached.decode() if isinstance(cached, bytes) else cached)
                # Never serve a cached EMPTY set (the warm-up task used to cache
                # one for 10 minutes); rebuild from the DB instead.
                if isinstance(cached_data, dict) and cached_data.get("keys"):
                    return cached_data
        except Exception:
            pass

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


@router.get("/providers/catalog")
async def get_provider_catalog_endpoint() -> dict[str, Any]:
    """Return available LLM provider configs (no API keys exposed).

    Reads from the provider registry — returns whichever providers are
    configured via environment variables on this instance.
    """
    from app.providers.registry import get_provider_catalog

    return {"providers": get_provider_catalog()}
