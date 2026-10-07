"""Model Registry API - expose model catalog, health, and routing policies."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.ai_router.models import ModelCapability, ModelRoutePolicy, RoutingMode, TaskType
from app.ai_router.registry import model_registry
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/models", tags=["model-registry"])

# Providers we can actually resolve to an adapter — reject anything else so a
# registered model can never point selection at an unresolvable provider.
_ALLOWED_PROVIDERS = frozenset(
    {
        "anthropic", "openai", "openai_compatible", "azure_openai", "nvidia",
        "gemini", "google", "voyage", "groq", "xai", "ollama", "onprem", "openrouter",
        "bedrock", "vertex", "mistral", "cohere", "custom",
    }
)


def _require_tenant(request: Request) -> TenantContext:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


_REGISTRY_ADMIN_FORBIDDEN = (
    "Only a platform admin can modify the model registry: sign in as an admin of "
    "the operator tenant, or enter the platform admin key"
)


def _admin_tenant_ids() -> set[str] | None:
    """``PLATFORM_ADMIN_TENANT_IDS`` as a set; ``None`` when unset."""
    raw = os.getenv("PLATFORM_ADMIN_TENANT_IDS")
    if raw is None or not raw.strip():
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() == "production"


def _registry_access(request: Request) -> tuple[bool, str | None, str, int]:
    """Who may change the GLOBAL model registry: ``(allowed, via, reason, status)``.

    The configured registry is deployment-wide (every tenant's goals select from
    it), so changing it is a platform-operator action. Two ways to be one:

    * **tenant admin of an operator tenant** — the caller has the ``admin`` role
      and its tenant is listed in ``PLATFORM_ADMIN_TENANT_IDS`` (``*`` = any
      tenant, for a single-tenant deployment). While the variable is unset, any
      tenant admin is allowed outside production; production requires the list.
      The logged-in admin used to be refused with "Platform admin privileges
      required" because only the separate admin key was accepted.
    * **the platform admin key** (``PLATFORM_ADMIN_KEY`` via ``X-Admin-Key``).

    Status codes describe the caller: 403 when not authorized (a wrong key is
    named as such), 503 when a key is presented but the deployment has none.
    """
    from app.tenancy.rbac import has_role

    ctx = getattr(request.state, "tenant", None)
    if ctx is not None and getattr(ctx, "roles", None) and has_role(ctx, "admin"):
        allowed = _admin_tenant_ids()
        if allowed is None and not _is_production():
            return True, "tenant_admin", "", 200
        if allowed is not None and ("*" in allowed or ctx.tenant_id in allowed):
            return True, "tenant_admin", "", 200
        tenant_reason = (
            "this tenant is not a platform operator tenant (add it to "
            "PLATFORM_ADMIN_TENANT_IDS) or enter the platform admin key"
        )
    else:
        tenant_reason = _REGISTRY_ADMIN_FORBIDDEN

    presented = request.headers.get("x-admin-key", "")
    if not presented:
        return False, None, tenant_reason, 403
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        return (
            False,
            None,
            "Platform admin key is not configured on this deployment (set "
            "PLATFORM_ADMIN_KEY), so it cannot be used",
            503,
        )
    if not hmac.compare_digest(presented.encode(), admin_key.encode()):
        return False, None, "The platform admin key is incorrect", 403
    return True, "admin_key", "", 200


def _require_platform_admin(request: Request) -> str:
    """Authorize a GLOBAL registry mutation (see :func:`_registry_access`)."""
    allowed, via, reason, status = _registry_access(request)
    if not allowed:
        raise HTTPException(status, reason)
    return via or ""


def _health_dict(provider: str) -> dict[str, Any]:
    h = model_registry.get_provider_health(provider)
    return {
        "is_healthy": h.is_healthy,  # None = unverified (never checked)
        "last_checked_at": h.last_checked_at,
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


@router.get("/plan-cap")
async def model_plan_cap(
    request: Request, model_id: str = Query(..., min_length=1)
) -> dict[str, Any]:
    """Whether *model_id* is above the caller's plan tier and would be clamped (PROV-18)."""
    tenant = _require_tenant(request)
    from app.ai_router.model_orchestrator import (
        _TIER_RANK,
        model_quality_tier,
        plan_tier_cap,
    )

    plan = str(getattr(tenant.plan, "value", tenant.plan) or "")
    tier = model_quality_tier(model_id)
    cap = plan_tier_cap(plan)
    return {
        "model_id": model_id,
        "model_tier": tier,
        "plan": plan,
        "plan_cap": cap,
        "clamped": cap is not None and _TIER_RANK[tier] > _TIER_RANK[cap],
    }


@router.get("/shadow-log")
async def get_shadow_log(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    """Recent shadow-evaluation results (platform admin): primary vs candidate model."""
    _require_tenant(request)
    _require_platform_admin(request)
    from app.ai_router.shadow_router import shadow_config, shadow_log

    model, rate = shadow_config()
    return {"shadow_model": model, "sample_rate": rate, "entries": shadow_log(limit)}


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
    """Test a specific model with a simple ping. Platform-admin only.

    It calls the PLATFORM provider (platform spend) and writes the result into
    the GLOBAL provider health that routing reads. Any tenant could call it, so a
    tenant could burn platform LLM calls and mark a provider unhealthy for all.
    """
    _require_tenant(request)
    _require_platform_admin(request)
    body = await request.json()
    provider = body.get("provider", "")
    model_id = body.get("model_id", "")

    # An embedding model is tested with an EMBEDDING (a chat ping proves
    # nothing about /v1/embeddings), on the endpoint it is configured with.
    from app.ai_router.selection import _ensure_seeded

    _ensure_seeded(model_registry)
    configured = model_registry.get_configured(provider, model_id)
    if configured is not None and _is_embedding_only(configured.capabilities):
        return await _probe_configured_embedder(request, configured)

    model = model_registry.get_model(provider, model_id)
    if model is None:
        from fastapi import HTTPException

        raise HTTPException(404, f"Model {provider}/{model_id} not found")

    app_provider = getattr(request.app.state, "_app_provider", None)
    if app_provider is None:
        return {"status": "skipped", "reason": "No provider configured", "model": model_id}

    # Only the provider actually backing the app can be pinged: the ping always
    # went to the app provider, but its result was recorded against whichever
    # provider was NAMED, so probing "anthropic" on an NVIDIA deployment marked
    # anthropic healthy (or broken) without ever calling it.
    from app.ai_router.health_feed import provider_label

    attributable = provider_label(app_provider)
    backing = attributable or ""
    if not backing:
        from app.core.config import get_settings

        backing = (get_settings().default_llm_provider or "").strip()
    if backing != provider:
        return {
            "status": "skipped",
            "reason": (
                f"provider {provider!r} does not back this deployment (the app provider "
                f"is {backing or 'unknown'}); health left unchanged"
            ),
            "model": model_id,
        }

    import time

    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import (
        complete_decision,
        system_job_scope,
        tenant_charge_scope,
    )

    start = time.monotonic()
    try:
        # A platform-operator probe: an uncharged system job (not the admin's
        # tenant spend), still with the circuit breaker and a bounded timeout.
        # complete_decision records the outcome in the shared provider health.
        with system_job_scope("model_probe"), tenant_charge_scope(None):
            resp = await complete_decision(
                app_provider,
                CompletionRequest(
                    messages=[Message(role="user", content="Reply with just the word 'OK'")],
                    model=model_id,
                    max_tokens=10,
                ),
                role="model_probe",
            )
        latency_ms = (time.monotonic() - start) * 1000
        if attributable is None:  # the feed could not attribute it: record here
            model_registry.update_health(provider, latency_ms=latency_ms, error=False)
        return {
            "status": "ok",
            "latency_ms": round(latency_ms, 1),
            "model": model_id,
            "response": resp.content[:50],
        }
    except Exception as exc:
        if attributable is None:
            model_registry.update_health(provider, error=True, error_msg=str(exc))
        return {"status": "error", "error": str(exc), "model": model_id}


# Capabilities answered by a chat completion; a model with none of them (and
# EMBEDDING) is probed with an embedding instead.
_CHAT_CAPABILITIES = frozenset(
    {
        ModelCapability.TEXT_GENERATION.value,
        ModelCapability.STRUCTURED_OUTPUT.value,
        ModelCapability.TOOL_USE.value,
        ModelCapability.VISION.value,
        ModelCapability.OCR.value,
        ModelCapability.LLM_JUDGE.value,
        ModelCapability.VIDEO_UNDERSTANDING.value,
    }
)


def _is_embedding_only(capabilities: Any) -> bool:
    caps = {str(getattr(c, "value", c)) for c in capabilities or []}
    return ModelCapability.EMBEDDING.value in caps and not (caps & _CHAT_CAPABILITIES)


def _settings_of(request: Request) -> Any:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return settings


def _dimension_report(request: Request, dims: int | None) -> dict[str, Any]:
    """``{dimensions, index_dimension, dimension_mismatch, dimension_reason}``."""
    from app.providers.embedder_factory import target_embedding_dim

    target = target_embedding_dim(_settings_of(request))
    mismatch = bool(dims and target and dims != target)
    return {
        "dimensions": dims,
        "index_dimension": target,
        "dimension_mismatch": mismatch,
        "dimension_reason": (
            f"the model returns {dims}-d vectors but the vector index is {target}-d "
            f"(EMBEDDING_DIM): it will be refused for embeddings until EMBEDDING_DIM={dims} "
            "and existing collections are re-embedded"
            if mismatch
            else ""
        ),
    }


async def _probe_configured_embedder(request: Request, model: Any) -> dict[str, Any]:
    """POST /models/test for an embedding model: one real embedding call on the
    endpoint the model is configured with (its own base_url, else its provider);
    the measured width is recorded for dimension safety."""
    import contextlib
    import time

    from app.providers.base import EmbedRequest
    from app.providers.registry_embedder import (
        build_registry_model_embedder,
        record_embedding_dimension,
    )

    start = time.monotonic()
    embedder: Any = None
    try:
        embedder = build_registry_model_embedder(model, _settings_of(request))
        resp = await embedder.embed(EmbedRequest(texts=["ping"], model=model.model_id))
        vec = resp.embeddings[0] if resp.embeddings else []
    except Exception as exc:
        return {
            "status": "error",
            "probe": "embedding",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "model": model.model_id,
        }
    finally:
        close = getattr(embedder, "aclose", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()
    latency_ms = (time.monotonic() - start) * 1000
    dims = len(vec) or None
    if dims:
        record_embedding_dimension(model.model_id, getattr(model, "base_url", None), dims)
    return {
        "status": "ok" if dims else "error",
        "probe": "embedding",
        "latency_ms": round(latency_ms, 1),
        "model": model.model_id,
        **({} if dims else {"error": "the endpoint returned no embedding"}),
        **_dimension_report(request, dims),
    }


# ── Configured registry (the models selection actually picks from) ─────────────

# Capability → the task used to compute which model is currently SELECTED (the
# cheapest qualifying one) for display in the UI.
_CAPABILITY_SELECT_TASK = {
    ModelCapability.TEXT_GENERATION: TaskType.EXECUTION,
    ModelCapability.EMBEDDING: TaskType.EMBEDDING,
    ModelCapability.VISION: TaskType.VISION,
    ModelCapability.OCR: TaskType.OCR,
    ModelCapability.RERANK: TaskType.RERANK,
}


_CAPABILITY_NOTES = {
    ModelCapability.EMBEDDING: (
        "Embeddings use the first eligible model in this order, at its own endpoint URL "
        "when it has one (else on its provider), when its vector width matches the index "
        "(EMBEDDING_DIM; measured by Test connection or on first use); otherwise the "
        "deployment's configured embedding endpoint is used, and only without one an "
        "added model with its own endpoint URL. Failover happens only "
        "between endpoints serving that SAME model id (e.g. NVIDIA then on-prem), "
        "never to another model: vectors of different models are not comparable, so "
        "changing the embedding model needs a re-index."
    ),
    ModelCapability.RERANK: (
        "The reranker endpoint serves one provider: the first model of that "
        "provider in this order is used."
    ),
}


def _configured_dict(m: Any, *, rank: int = 0) -> dict[str, Any]:
    from app.ai_router.selection import is_eligible, model_key

    return {
        "key": model_key(m),
        "provider": m.provider,
        "model_id": m.model_id,
        "display_name": m.display_name,
        "capabilities": [c.value for c in m.capabilities],
        "cost_per_1k_input": m.cost_per_1k_input,
        "cost_per_1k_output": m.cost_per_1k_output,
        "supports_tools": m.supports_tools,
        "supports_vision": m.supports_vision,
        "supports_structured_output": m.supports_structured_output,
        "quality_score": m.quality_score,
        "is_available": m.is_available,
        # False: the provider has no credentials here, so selection skips it.
        "provider_ready": is_eligible(m),
        "source": (m.extra or {}).get("source", "env"),
        "origin": (m.extra or {}).get("origin", ""),
        "base_url": getattr(m, "base_url", None),
        # Whether the entry carries its own (vault-encrypted) endpoint credential.
        # Neither the key nor its ciphertext is ever returned.
        "has_api_key": bool((m.extra or {}).get("api_key_encrypted")),
        "rank": rank,
    }


def _annotate_embedding_rows(request: Request, rows: list[dict[str, Any]]) -> None:
    """Add each embedding row's vector width and whether it fits the index."""
    from app.providers.embedder_factory import target_embedding_dim
    from app.providers.registry_embedder import embedding_dimension_status

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    target = target_embedding_dim(settings)
    for row in rows:
        row.update(
            embedding_dimension_status(row["provider"], row["model_id"], target, settings)
        )
        row["index_dimension"] = target


def _embedding_selected(rows: list[dict[str, Any]], active: dict[str, Any]) -> str | None:
    """The embedding model the listing presents as selected — never a refused one.

    A row whose vector width does not fit the index (``dimension_mismatch``) is
    refused for embeddings (the embedder skips it), so it is marked ``refused``
    with the reason and is never ``selected``, even when it ranks first in the
    preference order. ``selected`` is the model the process actually embeds
    with (``active_embedder``) when that is known; otherwise the first eligible
    row that is not refused. Each row also gets ``selected: bool``.
    """
    for row in rows:
        refused = bool(row.get("dimension_mismatch"))
        row["refused"] = refused
        row["refusal_reason"] = str(row.get("dimension_reason") or "") if refused else ""
    selected: str | None
    if active.get("status") == "available":
        selected = str(active.get("model") or "") or None
    elif active.get("status") == "unknown":
        selected = next(
            (r["model_id"] for r in rows if r.get("provider_ready") and not r["refused"]),
            None,
        )
    else:  # not_configured / unavailable: nothing embeds, so nothing is selected
        selected = None
    for row in rows:
        row["selected"] = bool(selected) and row["model_id"] == selected and not row["refused"]
    return selected


def _active_embedder_status(request: Request) -> dict[str, Any]:
    """The embedder this process embeds with, and why a registry model was refused."""
    resolution = getattr(request.app.state, "embedder_resolution", None)
    if resolution is None:
        return {"status": "unknown"}
    return {
        "status": resolution.status,
        "source": resolution.source,
        "provider": resolution.provider or None,
        "model": resolution.model or None,
        "dimension": resolution.dimension,
        "registry_refusal": resolution.registry_refusal or None,
    }


@router.get("/configured/access")
async def registry_access(request: Request) -> dict[str, Any]:
    """Whether the caller may change the model registry, and how (UI gating)."""
    _require_tenant(request)
    allowed, via, reason, _status = _registry_access(request)
    return {
        "can_modify": allowed,
        "via": via,
        # The admin-key field only helps when the caller is not a tenant admin
        # of an operator tenant.
        "needs_admin_key": not allowed,
        "reason": reason,
    }


@router.get("/configured")
async def list_configured_models(request: Request) -> dict[str, Any]:
    """The deployment's configured models per capability, in EXECUTION order
    (the operator's preference order, then cheapest first), with the model
    currently selected and the failover chain after it."""
    _require_tenant(request)
    from app.ai_router.selection import (
        _ensure_seeded,
        order_models,
        resolve_fallback_models,
        select_configured_model_id,
    )

    _ensure_seeded(model_registry)
    groups: list[dict[str, Any]] = []
    for cap, task in _CAPABILITY_SELECT_TASK.items():
        models = model_registry.list_configured(cap)
        if not models:
            continue
        ordered = order_models(models, cap)
        selected = select_configured_model_id(task)
        preference = model_registry.preference_order(cap)
        rows = [_configured_dict(m, rank=i + 1) for i, m in enumerate(ordered)]
        fallback = resolve_fallback_models(task, selected) if selected else []
        active: dict[str, Any] | None = None
        if cap is ModelCapability.EMBEDDING:
            _annotate_embedding_rows(request, rows)
            # Embeddings never fail over to another model, only to other
            # endpoints of the selected one.
            fallback = []
            active = _active_embedder_status(request)
            selected = _embedding_selected(rows, active)
        group: dict[str, Any] = {
            "capability": cap.value,
            "selected_model_id": selected,
            "fallback_model_ids": fallback,
            "order_mode": "preference" if preference else "cost",
            "preference": preference,
            "models": rows,
        }
        if cap is ModelCapability.EMBEDDING:
            # The endpoints (providers) serving the selected model, failover order.
            group["failover_providers"] = [
                r["provider"] for r in rows if r["model_id"] == selected and r["provider_ready"]
            ]
            group["active_embedder"] = active
            group["refused_model_ids"] = sorted(
                {r["model_id"] for r in rows if r.get("refused")}
            )
        if cap in _CAPABILITY_NOTES:
            group["note"] = _CAPABILITY_NOTES[cap]
        groups.append(group)
    return {"capabilities": groups, "total": len(model_registry.list_configured())}


# ── Catalog (reference models per provider) ──────────────────────────────────


@router.get("/catalog")
async def list_catalog(request: Request) -> dict[str, Any]:
    """Reference models per provider (NVIDIA, Groq, xAI, Claude, OpenAI, Gemini,
    Qwen on-prem, Ollama, Voyage) with whether this deployment has the
    provider's credentials and whether each model is already configured."""
    _require_tenant(request)
    from app.ai_router.model_catalog import catalog_providers, provider_ready
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    configured = {(e.get("provider"), e.get("model_id")) for e in (store.list() if store else [])}
    configured |= {(m.provider, m.model_id) for m in model_registry.list_configured()}
    return {
        "providers": [
            {
                "provider": cp.provider,
                "label": cp.label,
                "ready": provider_ready(cp.provider),
                "env_hint": cp.env_hint,
                "models": [
                    {
                        **{k: v for k, v in m.endpoint(cp.provider).items() if k != "provider"},
                        "already_configured": (cp.provider, m.model_id) in configured,
                    }
                    for m in cp.models
                ],
            }
            for cp in catalog_providers()
        ]
    }


@router.post("/catalog/import")
async def import_catalog(request: Request) -> dict[str, Any]:
    """Copy catalog models into the configured registry (platform admin).

    Body ``{"providers": [...], "model_ids": [...]}``; both optional (omitted =
    everything). Models already configured keep the operator's edits. Models of a
    provider without credentials are imported but skipped by selection until the
    key is set.
    """
    _require_tenant(request)
    _require_platform_admin(request)
    from app.ai_router.model_catalog import catalog_endpoints
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config

    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    providers = [str(p) for p in body.get("providers") or [] if p]
    model_ids = [str(m) for m in body.get("model_ids") or [] if m]
    endpoints = catalog_endpoints(providers or None, model_ids or None)
    if not endpoints:
        raise HTTPException(400, "no catalog models match the requested providers/models")
    store = get_model_registry_store()
    if store is None:
        raise HTTPException(503, "model registry store unavailable")
    imported = store.upsert_many(endpoints)
    seed_registry_from_config()
    return {"status": "imported", "imported": imported, "skipped": len(endpoints) - imported}


# ── Preference order per capability ──────────────────────────────────────────


def _capability_or_400(capability: str) -> ModelCapability:
    try:
        cap = ModelCapability(capability)
    except ValueError as exc:
        raise HTTPException(400, f"invalid capability: {capability}") from exc
    if cap not in _CAPABILITY_SELECT_TASK:
        raise HTTPException(400, f"capability {capability} has no preference order")
    return cap


@router.get("/preferences")
async def get_preferences(request: Request) -> dict[str, Any]:
    """The saved preference order per capability (missing = cheapest first)."""
    _require_tenant(request)
    from app.ai_router.selection import _ensure_seeded

    _ensure_seeded(model_registry)
    return {
        "preferences": {
            cap.value: model_registry.preference_order(cap) for cap in _CAPABILITY_SELECT_TASK
        }
    }


@router.put("/preferences/{capability}")
async def set_preference(request: Request, capability: str) -> dict[str, Any]:
    """Save the execution order for one capability (platform admin).

    Body ``{"order": ["provider/model_id", ...]}``: the first eligible model is
    used, the next ones are the failover chain; configured models not listed
    follow cheapest first.
    """
    _require_tenant(request)
    _require_platform_admin(request)
    cap = _capability_or_400(capability)
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config
    from app.ai_router.selection import _ensure_seeded, model_key

    body = await request.json()
    raw = body.get("order") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not all(isinstance(k, str) for k in raw):
        raise HTTPException(400, 'body must be {"order": ["provider/model_id", ...]}')
    order: list[str] = []
    for k in raw:
        if k.strip() and k.strip() not in order:
            order.append(k.strip())
    _ensure_seeded(model_registry)
    known = {model_key(m) for m in model_registry.list_configured(cap)}
    unknown = [k for k in order if k not in known]
    if unknown:
        raise HTTPException(
            400, f"not configured for {cap.value}: {', '.join(unknown[:5])}"
        )
    store = get_model_registry_store()
    if store is None:
        raise HTTPException(503, "model registry store unavailable")
    store.set_preference(cap.value, order)
    seed_registry_from_config()
    return {"status": "saved", "capability": cap.value, "order": order}


@router.delete("/preferences/{capability}")
async def reset_preference(request: Request, capability: str) -> dict[str, Any]:
    """Drop the saved order for one capability: back to cheapest first."""
    _require_tenant(request)
    _require_platform_admin(request)
    cap = _capability_or_400(capability)
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config

    store = get_model_registry_store()
    if store is None:
        raise HTTPException(503, "model registry store unavailable")
    store.set_preference(cap.value, [])
    seed_registry_from_config()
    return {"status": "reset", "capability": cap.value}


def _checked_base_url(raw: Any) -> str:
    """A model's own endpoint URL, normalised, or 400 when it may not be used."""
    from app.ai_router.model_endpoints import ModelEndpointError, check_model_endpoint

    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        return check_model_endpoint(value)
    except ModelEndpointError as exc:
        raise HTTPException(400, str(exc)) from exc


_PROBE_TIMEOUT_S = 30.0


@router.post("/configured/test-endpoint")
async def test_model_endpoint(request: Request) -> dict[str, Any]:
    """Check that a model's own endpoint answers (platform admin).

    Body ``{provider, model_id, base_url, capabilities, api_key?}``. Lists the
    server's models (``GET {base_url}/models``), then makes one real call for the
    capability: a short chat completion (reasoning / vision / OCR), an embedding
    (``POST {base_url}/embeddings``) for an embedding model, or a rerank.
    ``ok: false`` (HTTP 200) carries the error; 400 = URL refused.

    The credential is ``api_key`` when given (used for this call only, never
    stored or echoed), else the one saved with the registry entry, else the
    provider's env key. An embedding probe reports the measured width against
    the index (``dimensions`` / ``dimension_mismatch``) and records it, so the
    embedder refuses a model whose width does not fit before it writes a vector.
    Connections are SSRF-pinned (the host is re-checked at connect time).
    """
    _require_tenant(request)
    _require_platform_admin(request)
    import time

    import httpx

    from app.ai_router.model_endpoints import (
        ModelEndpointError,
        endpoint_api_key,
        endpoint_http_client,
        onprem_extra_body,
    )

    body = await request.json()
    body = body if isinstance(body, dict) else {}
    model_id = str(body.get("model_id", "") or "").strip()
    provider = str(body.get("provider", "") or "").strip() or "custom"
    caps = {str(c) for c in (body.get("capabilities") or []) if c}
    base = _checked_base_url(body.get("base_url"))
    if not model_id or not base:
        raise HTTPException(400, "model_id and base_url are required")

    chat = bool(caps & _CHAT_CAPABILITIES)
    probe = (
        "embedding"
        if _is_embedding_only(caps)
        else "rerank"
        if ModelCapability.RERANK.value in caps and not chat
        else "chat"
    )
    typed_key = str(body.get("api_key", "") or "").strip()
    if typed_key:
        api_key = typed_key
    else:
        from app.ai_router.selection import _ensure_seeded

        _ensure_seeded(model_registry)
        saved = model_registry.get_configured(provider, model_id)
        same_endpoint = saved is not None and (
            str(getattr(saved, "base_url", "") or "").rstrip("/") == base
        )
        try:
            api_key = endpoint_api_key(provider, saved if same_endpoint else None)
        except ModelEndpointError as exc:
            raise HTTPException(400, str(exc)) from exc
    headers = {"Authorization": f"Bearer {api_key}"}
    result: dict[str, Any] = {
        "ok": False,
        "latency_ms": 0.0,
        "probe": probe,
        "model_listed": None,
        "served_models": [],
        "detail": "",
        "error": None,
    }
    start = time.monotonic()
    try:
        # Redirects are never followed and every connect re-checks the host.
        async with endpoint_http_client(timeout=_PROBE_TIMEOUT_S) as client:
            try:
                listed = await client.get(f"{base}/models", headers=headers)
                if listed.status_code == 200:
                    ids = [
                        str(m.get("id"))
                        for m in (listed.json().get("data") or [])
                        if isinstance(m, dict) and m.get("id")
                    ]
                    result["served_models"] = ids[:50]
                    result["model_listed"] = model_id in ids
            except (httpx.HTTPError, ValueError):
                pass  # not every server lists models; the real call decides

            if probe == "chat":
                payload: dict[str, Any] = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Reply with just the word OK"}],
                    "max_tokens": 16,
                }
                payload.update(onprem_extra_body(provider) or {})
                resp = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
            elif probe == "embedding":
                resp = await client.post(
                    f"{base}/embeddings", json={"model": model_id, "input": ["ping"]},
                    headers=headers,
                )
            else:
                url = base if base.endswith("/rerank") else f"{base}/rerank"
                resp = await client.post(
                    url,
                    json={"model": model_id, "query": "ping", "documents": ["ping", "pong"]},
                    headers=headers,
                )
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return result
        data = resp.json()
        if probe == "chat":
            choice = (data.get("choices") or [{}])[0]
            text = str((choice.get("message") or {}).get("content") or "").strip()
            result["detail"] = f"replied: {text[:80]!r}" if text else "empty reply"
        elif probe == "embedding":
            vec = ((data.get("data") or [{}])[0]).get("embedding") or []
            if not isinstance(vec, list) or not vec:
                result["error"] = "the endpoint returned no embedding"
                return result
            dims = len(vec)
            result["detail"] = f"{dims}-dimension embedding"
            result.update(_dimension_report(request, dims))
            from app.providers.registry_embedder import record_embedding_dimension

            record_embedding_dimension(model_id, base, dims)
        else:
            result["detail"] = f"{len(data.get('results') or [])} documents scored"
        result["ok"] = True
    except httpx.HTTPError as exc:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300] or 'connection failed'}"
    except ModelEndpointError as exc:  # the pinned client refused the resolved address
        result["error"] = str(exc)[:300]
    except ValueError as exc:  # also SSRFError raised at connect time
        result["error"] = f"refused or invalid response: {str(exc)[:200]}"
    return result


@router.post("/configured")
async def upsert_configured_model(request: Request) -> dict[str, Any]:
    """Add or override a configured model. Persists to the store and takes effect
    immediately (registry re-seeded). Platform-admin only — the registry is
    deployment-global (shared across tenants)."""
    _require_tenant(request)
    _require_platform_admin(request)
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config

    body = await request.json()
    model_id = str(body.get("model_id", "") or "").strip()
    caps = [str(c) for c in (body.get("capabilities") or []) if c]
    if not model_id or not caps:
        raise HTTPException(400, "model_id and at least one capability are required")
    try:
        valid_caps = [ModelCapability(c).value for c in caps]
    except ValueError as exc:
        raise HTTPException(400, f"invalid capability: {exc}") from exc

    provider = str(body.get("provider", "") or "").strip() or "custom"
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(
            400, f"unknown provider '{provider}'; must be a registered adapter"
        )

    endpoint = {
        "provider": provider,
        "model_id": model_id,
        "display_name": str(body.get("display_name", "") or model_id),
        "capabilities": valid_caps,
        "cost_per_1k_input": float(body.get("cost_per_1k_input", 0.0) or 0.0),
        "cost_per_1k_output": float(body.get("cost_per_1k_output", 0.0) or 0.0),
        "supports_tools": bool(body.get("supports_tools", False)),
        "supports_vision": bool(body.get("supports_vision", False)),
        "supports_structured_output": bool(body.get("supports_structured_output", False)),
        "quality_score": float(body.get("quality_score", 0.7) or 0.7),
        "is_available": bool(body.get("is_available", True)),
    }
    base_url = _checked_base_url(body.get("base_url"))
    if base_url:
        endpoint["base_url"] = base_url
    store = get_model_registry_store()
    if store is None:
        raise HTTPException(503, "model registry store unavailable")
    existing = store.get(provider, model_id) or {}
    # The endpoint credential: encrypted by the credential vault, never stored
    # (or returned) in plaintext. Omitted = keep the saved one (an edit of the
    # price must not drop it); ``clear_api_key`` removes it.
    api_key = str(body.get("api_key", "") or "").strip()
    if api_key:
        from app.ai_router.model_endpoints import ModelEndpointError, encrypt_endpoint_api_key

        try:
            endpoint["api_key_encrypted"] = encrypt_endpoint_api_key(api_key)
        except ModelEndpointError as exc:
            raise HTTPException(503, str(exc)) from exc
    elif not body.get("clear_api_key") and existing.get("api_key_encrypted"):
        endpoint["api_key_encrypted"] = existing["api_key_encrypted"]
    if ModelCapability.EMBEDDING.value in valid_caps:
        # The width measured by "Test connection" (or a previous save at the
        # same endpoint): the embedder's dimension-safety check reads it.
        dims = store.probed_dimension(model_id, base_url or None)
        if dims is None and str(existing.get("base_url") or "") == base_url:
            dims = existing.get("dimensions")
        if isinstance(dims, int) and dims > 0:
            endpoint["dimensions"] = dims
    store.upsert(endpoint)
    seed_registry_from_config()  # reload env + overrides so it takes effect now
    return {"status": "saved", "model_id": model_id}


@router.delete("/configured/{provider}/{model_id:path}")
async def delete_configured_model(request: Request, provider: str, model_id: str) -> dict[str, Any]:
    """Remove a configured model override. Platform-admin only (global registry)."""
    _require_tenant(request)
    _require_platform_admin(request)
    from app.ai_router.registry_store import get_model_registry_store
    from app.ai_router.seeder import seed_registry_from_config

    store = get_model_registry_store()
    removed_store = store.remove(provider, model_id) if store is not None else False
    removed_reg = model_registry.remove_configured(provider, model_id)
    seed_registry_from_config()
    return {"status": "deleted", "removed": removed_store or removed_reg}


@router.post("/configured/reseed")
async def reseed_configured_models(request: Request) -> dict[str, Any]:
    """Re-seed the configured registry from env + persisted overrides.
    Platform-admin only (global registry)."""
    _require_tenant(request)
    _require_platform_admin(request)
    from app.ai_router.seeder import seed_registry_from_config

    count = seed_registry_from_config()
    return {"status": "reseeded", "configured_models": count}


@router.get("/routing-policies")
async def get_routing_policies(request: Request) -> dict[str, Any]:
    """Get the tenant's model routing policies."""
    from fastapi import HTTPException

    from app.ai_router.registry import policy_is_enforced

    tenant = _require_tenant(request)
    try:
        # Shared (Redis) store when wired — it used to read this process's dict,
        # so another replica answered with an empty list.
        policies = model_registry.list_route_policies(tenant.tenant_id)
    except Exception as exc:
        raise HTTPException(503, "Routing policy store unavailable") from exc
    return {
        "policies": [
            {
                "task_type": task,
                "routing_mode": p.routing_mode.value,
                "preferred_provider": p.preferred_provider,
                "preferred_model": p.preferred_model,
                "fallback_chain": p.fallback_chain,
                "enforced": policy_is_enforced(p),
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
    from fastapi import HTTPException

    from app.ai_router.registry import policy_is_enforced

    try:
        model_registry.set_route_policy(tenant.tenant_id, tt, policy)
    except Exception as exc:
        raise HTTPException(503, "Routing policy could not be saved; nothing changed") from exc
    enforced = policy_is_enforced(policy)
    return {
        "status": "saved",
        "task_type": task_type,
        # Honest scope: goals pin the planning/execution/verification role to
        # preferred_model; other task types and routing modes are stored only.
        "enforced": enforced,
        **(
            {}
            if enforced
            else {
                "note": "Stored but not enforced on goals: only a preferred_model for "
                "planning/execution/verification is applied."
            }
        ),
    }
