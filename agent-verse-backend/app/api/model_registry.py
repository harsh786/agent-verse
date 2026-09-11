"""Model Registry API - expose model catalog, health, and routing policies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.ai_router.models import ModelCapability, ModelRoutePolicy, RoutingMode, TaskType
from app.ai_router.registry import model_registry
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/models", tags=["model-registry"])


def _require_tenant(request: Request) -> TenantContext:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        from fastapi import HTTPException

        raise HTTPException(401, "Unauthorized")
    return ctx


def _health_dict(provider: str) -> dict[str, Any]:
    h = model_registry.get_provider_health(provider)
    return {
        "is_healthy": h.is_healthy,
        "circuit_open": h.circuit_open,
        "error_rate_5m": round(h.error_rate_5m, 3),
        "avg_latency_ms": round(h.avg_latency_ms, 1),
    }


@router.get("")
async def list_models(
    request: Request,
    capability: str | None = Query(default=None),
    provider: str | None = Query(default=None),
) -> dict[str, Any]:
    """List all available models with capabilities and health."""
    _require_tenant(request)
    cap = ModelCapability(capability) if capability else None
    models = model_registry.list_models(capability=cap, provider=provider)
    return {
        "models": [
            {
                "provider": m.provider,
                "model_id": m.model_id,
                "display_name": m.display_name,
                "capabilities": [c.value for c in m.capabilities],
                "context_window": m.context_window,
                "cost_per_1k_input": m.cost_per_1k_input,
                "cost_per_1k_output": m.cost_per_1k_output,
                "supports_streaming": m.supports_streaming,
                "supports_tools": m.supports_tools,
                "supports_vision": m.supports_vision,
                "quality_score": m.quality_score,
                "avg_latency_ms": m.avg_latency_ms,
                "is_available": m.is_available,
                "health": _health_dict(m.provider),
            }
            for m in models
        ],
        "total": len(models),
    }


def _prettify_model_id(model_id: str) -> str:
    """Human-friendly label for a slug like ``moonshotai/kimi-k3`` → ``Kimi K3``."""
    tail = model_id.rsplit("/", 1)[-1]
    words = [w for w in tail.replace("_", "-").split("-") if w]
    return " ".join(w.upper() if len(w) <= 2 else w.capitalize() for w in words) or model_id


@router.get("/active")
async def get_active_model(request: Request) -> dict[str, Any]:
    """Return the model the platform will actually run goals with.

    Reflects the *configured* default provider/model (env-driven, via the
    provider registry) rather than the top of the static quality-ranked
    catalogue — so the UI shows the real active model (e.g. the configured
    NVIDIA model) instead of a hardcoded-looking default.
    """
    _require_tenant(request)
    from app.core.config import get_settings
    from app.providers.model_defaults import configured_default_model

    settings = get_settings()
    model_id = configured_default_model()
    provider = (settings.default_llm_provider or "").strip()

    display_name = ""
    if model_id:
        catalogued = model_registry.get_model(provider, model_id)
        display_name = catalogued.display_name if catalogued else _prettify_model_id(model_id)

    return {
        "provider": provider,
        "model_id": model_id,
        "display_name": display_name or _prettify_model_id(model_id) or "Auto-routed",
        "configured": bool(model_id),
    }


@router.get("/health")
async def get_provider_health(request: Request) -> dict[str, Any]:
    """Get health status for all providers."""
    _require_tenant(request)
    providers = sorted({m.provider for m in model_registry.list_models()})
    return {
        "providers": [
            {"provider": p, **vars(model_registry.get_provider_health(p))} for p in providers
        ]
    }


@router.post("/test")
async def test_model(request: Request) -> dict[str, Any]:
    """Test a specific model with a simple ping."""
    _require_tenant(request)
    body = await request.json()
    provider = body.get("provider", "")
    model_id = body.get("model_id", "")

    model = model_registry.get_model(provider, model_id)
    if model is None:
        from fastapi import HTTPException

        raise HTTPException(404, f"Model {provider}/{model_id} not found")

    app_provider = getattr(request.app.state, "_app_provider", None)
    if app_provider is None:
        return {"status": "skipped", "reason": "No provider configured", "model": model_id}

    import time

    start = time.monotonic()
    try:
        from app.providers.base import CompletionRequest, Message

        resp = await app_provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content="Reply with just the word 'OK'")],
                model=model_id,
                max_tokens=10,
            )
        )
        latency_ms = (time.monotonic() - start) * 1000
        model_registry.update_health(provider, latency_ms=latency_ms, error=False)
        return {
            "status": "ok",
            "latency_ms": round(latency_ms, 1),
            "model": model_id,
            "response": resp.content[:50],
        }
    except Exception as exc:
        model_registry.update_health(provider, error=True, error_msg=str(exc))
        return {"status": "error", "error": str(exc), "model": model_id}


@router.get("/routing-policies")
async def get_routing_policies(request: Request) -> dict[str, Any]:
    """Get the tenant's model routing policies."""
    tenant = _require_tenant(request)
    policies = model_registry._tenant_policies.get(tenant.tenant_id, {})
    return {
        "policies": [
            {
                "task_type": task,
                "routing_mode": p.routing_mode.value,
                "preferred_provider": p.preferred_provider,
                "preferred_model": p.preferred_model,
                "fallback_chain": p.fallback_chain,
            }
            for task, p in policies.items()
        ]
    }


@router.put("/routing-policies/{task_type}")
async def set_routing_policy(request: Request, task_type: str) -> dict[str, Any]:
    """Set a routing policy for a specific task type."""
    tenant = _require_tenant(request)
    body = await request.json()

    try:
        tt = TaskType(task_type)
    except ValueError as _b904_exc:
        from fastapi import HTTPException

        raise HTTPException(400, f"Invalid task type: {task_type}") from _b904_exc

    policy = ModelRoutePolicy(
        task_type=tt,
        routing_mode=RoutingMode(body.get("routing_mode", "tenant_default")),
        preferred_provider=body.get("preferred_provider"),
        preferred_model=body.get("preferred_model"),
        fallback_chain=body.get("fallback_chain", []),
    )
    model_registry.set_route_policy(tenant.tenant_id, tt, policy)
    return {"status": "saved", "task_type": task_type}
