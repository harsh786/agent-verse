"""``GET /models/resolution`` — what each capability and agent role runs on NOW.

Read-only and built only on the resolvers the runtime itself calls, so the page
shows the truth rather than a re-implementation:

* reasoning roles — the goal runtime's precedence (``ModelOrchestratorAdapter
  .model_for`` / ``role_preference.resolve_role_model``): the tenant's
  routing-policy pin > the saved reasoning order (``preferred_role_model``) >
  the deployment role map (env ``DEFAULT_*_MODEL`` pins, then the hybrid /
  on-prem / NVIDIA profile) > the cheapest configured model > the provider's
  default model. Roles the routers do not route (``judge``) skip the pin and the
  role map: saved order > env pin > provider default;
* embeddings — the embedder this process resolved at startup
  (``app.state.embedder_resolution``), else ``selection.resolve_embed_model``;
* vision / OCR / rerank — the single-resolver facade ``app.ai_router.resolve``
  (``resolve_vision`` / ``resolve_ocr`` / ``resolve_reranker``): registry order,
  then env pins, then a local tier (Tesseract OCR, the local cross-encoder),
  else nothing — vision never falls back to the reasoning model.

A capability whose call sites are not routed through the registry would be
reported with ``routed: false``; every capability listed here is routed on this
version.
"""

from __future__ import annotations

from typing import Any

from app.ai_router.models import ModelCapability, TaskType
from app.ai_router.registry import model_registry
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Task types in display order, with a plain-words label.
_TASK_LABELS: dict[str, str] = {
    "planning": "Planning",
    "execution": "Execution (tool use)",
    "verification": "Verification",
    "supervisor": "Supervisor",
    "think": "Think",
    "reflection": "Reflection",
    "classification": "Classification / routing",
    "judge": "Judges",
}

_SOURCE_LABELS: dict[str, str] = {
    "tenant_pin": "Tenant routing policy",
    "registry_order": "Registry order",
    "registry_cheapest": "Registry (cheapest first)",
    "deployment_profile": "Deployment profile",
    "env_pin": "Environment pin",
    "local_default": "Local default",
    "default": "Provider default",
    "none": "Nothing resolves",
}


def _source(source: str) -> dict[str, str]:
    return {"source": source, "source_label": _SOURCE_LABELS.get(source, source)}


def _configured_by_id() -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for m in model_registry.list_configured():
        out.setdefault(str(m.model_id), []).append(m)
    return out


def _describe(model_id: str, index: dict[str, list[Any]]) -> dict[str, Any] | None:
    """``{model_id, provider, servable}`` for a resolved model id (``None`` = empty)."""
    if not model_id:
        return None
    from app.api.model_registry import _row_servable

    entries = index.get(model_id) or []
    servable: bool | None = None
    provider: str | None = None
    if entries:
        provider = str(entries[0].provider)
        try:
            servable = any(_row_servable(m) for m in entries)
        except Exception:  # pragma: no cover - never fail the read
            servable = None
    return {"model_id": model_id, "provider": provider, "servable": servable}


def _registry_source(capability: ModelCapability, model: Any) -> str:
    from app.ai_router.selection import model_key

    order = model_registry.preference_order(capability)
    return "registry_order" if model_key(model) in order else "registry_cheapest"


def _not_servable_warning(entry: dict[str, Any] | None, what: str) -> str | None:
    if entry and entry.get("servable") is False:
        return (
            f"{entry['model_id']} is resolved for {what}, but nothing in this deployment "
            "serves it (no API key or endpoint URL): calls fail over or fail."
        )
    return None


# ── capabilities ─────────────────────────────────────────────────────────────


def _capability(
    capability: str,
    label: str,
    *,
    model: dict[str, Any] | None,
    source: str,
    fallbacks: list[dict[str, Any]] | None = None,
    warning: str | None = None,
    note: str | None = None,
    routed: bool = True,
) -> dict[str, Any]:
    return {
        "capability": capability,
        "label": label,
        "routed": routed,
        "model": model,
        **_source(source if model else "none"),
        "fallbacks": fallbacks or [],
        "warning": warning,
        "note": note,
    }


def _reasoning(index: dict[str, list[Any]]) -> dict[str, Any]:
    from app.ai_router.selection import ordered_configured_models

    ordered = ordered_configured_models(TaskType.TEXT_GENERATION)
    if ordered:
        head = ordered[0]
        model = _describe(str(head.model_id), index)
        fallbacks = [
            d
            for d in (_describe(str(m.model_id), index) for m in ordered[1:4])
            if d and d["model_id"] != head.model_id
        ]
        return _capability(
            "reasoning",
            "Reasoning",
            model=model,
            source=_registry_source(ModelCapability.TEXT_GENERATION, head),
            fallbacks=fallbacks,
            warning=_not_servable_warning(model, "reasoning"),
            note="Each agent role below may resolve differently (tenant pins, tool use, "
            "deployment profile).",
        )
    # Nothing in the registry: what resolve_reasoning falls back to (env pins /
    # role map / env default), never a separate reading of the env.
    from app.ai_router.resolve import ModelNotConfiguredError, resolve_reasoning

    try:
        env_model = resolve_reasoning("planning").model
    except ModelNotConfiguredError:
        env_model = ""
    if env_model:
        return _capability(
            "reasoning",
            "Reasoning",
            model=_describe(env_model, index),
            source="env_pin",
            warning=f"No reasoning model is registered: the env model ({env_model}) is used.",
        )
    return _capability(
        "reasoning",
        "Reasoning",
        model=None,
        source="none",
        warning="No reasoning model: add one, or goals cannot run.",
    )


def _embedding(request: Any, index: dict[str, list[Any]]) -> dict[str, Any]:
    from app.ai_router.selection import select_configured_model_id

    resolution = getattr(request.app.state, "embedder_resolution", None)
    if resolution is not None:
        status = resolution.status
        if status == "available" and resolution.model:
            model = _describe(str(resolution.model), index)
            if model and not model.get("provider"):
                model["provider"] = resolution.provider or None
            if resolution.source == "registry":
                entry = next(iter(index.get(str(resolution.model)) or []), None)
                source = (
                    _registry_source(ModelCapability.EMBEDDING, entry)
                    if entry is not None
                    else "registry_order"
                )
            else:
                source = "env_pin"
            endpoints = [str(e) for e in (resolution.endpoints or [])][1:]
            warning = (
                f"The registry's preferred embedding model was refused: "
                f"{resolution.registry_refusal}"
                if resolution.registry_refusal
                else None
            )
            return _capability(
                "embedding",
                "Embeddings",
                model=model,
                source=source,
                fallbacks=[
                    {"model_id": str(resolution.model), "provider": e, "servable": True}
                    for e in endpoints
                ],
                warning=warning,
                note=(f"Vector width {resolution.dimension}-d. " if resolution.dimension else "")
                + "The default embedder; a knowledge collection can be bound to its own "
                "embedding model. Embeddings fail over only between endpoints serving the "
                "same model.",
            )
        reason = ""
        try:
            reason = str(resolution.reason() or "")
        except Exception:  # pragma: no cover - never fail the read
            reason = ""
        return _capability(
            "embedding",
            "Embeddings",
            model=None,
            source="none",
            warning=(
                "No embedder is running: knowledge ingestion and search are unavailable."
                + (f" {reason}" if reason else "")
            ),
        )
    # No resolution in this process (it did not build an embedder at startup):
    # report what the registry / env would choose.
    from app.providers.model_defaults import configured_embed_model

    reg = select_configured_model_id(TaskType.EMBEDDING)
    if reg:
        entry = next(iter(index.get(reg) or []), None)
        model = _describe(reg, index)
        return _capability(
            "embedding",
            "Embeddings",
            model=model,
            source=(
                _registry_source(ModelCapability.EMBEDDING, entry) if entry else "registry_order"
            ),
            warning=_not_servable_warning(model, "embeddings"),
            note="This process has not resolved its embedder; shown from the registry.",
        )
    env_model = configured_embed_model("")
    if env_model:
        return _capability(
            "embedding",
            "Embeddings",
            model=_describe(env_model, index),
            source="env_pin",
            note="This process has not resolved its embedder; shown from the environment.",
        )
    return _capability(
        "embedding",
        "Embeddings",
        model=None,
        source="none",
        warning="No embedding model: knowledge ingestion and search are unavailable.",
    )


# Sources of app.ai_router.resolve → this payload's vocabulary.
_FACADE_SOURCES: dict[str, str] = {
    "registry_preference": "registry_order",
    "registry_cheapest": "registry_cheapest",
    "env_pin": "env_pin",
    "local_default": "local_default",
    "deployment_role_map": "deployment_profile",
    "degraded": "none",
}


def _fallback_refs(ids: Any, index: dict[str, list[Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mid in ids or ():
        ref = _describe(str(mid), index)
        if ref:
            out.append(ref)
    return out


def _vision_like(capability: str, index: dict[str, list[Any]]) -> dict[str, Any]:
    """Vision or OCR, from the runtime's own resolver (``app.ai_router.resolve``)."""
    from app.ai_router import resolve

    is_vision = capability == "vision"
    label = "Vision" if is_vision else "OCR"
    try:
        res = resolve.resolve_vision() if is_vision else resolve.resolve_ocr()
    except resolve.ModelNotConfiguredError as exc:
        return _capability(
            capability,
            label,
            model=None,
            source="none",
            warning=f"No {label} model: "
            + ("image understanding" if is_vision else "OCR of scanned documents and images")
            + f" is unavailable — {exc.hint}.",
        )
    source = _FACADE_SOURCES.get(res.source, res.source)
    model = _describe(res.model, index)
    if model is not None and not model.get("provider") and res.provider:
        model["provider"] = res.provider
    note: str | None = None
    if res.source == "local_default":
        note = "The local Tesseract tier (OCR_TESSERACT_ENABLED): text only, no layout."
    elif not is_vision and res.model and model is not None:
        caps = {
            str(getattr(c, "value", c))
            for m in index.get(res.model) or []
            for c in (m.capabilities or [])
        }
        if caps and ModelCapability.OCR.value not in caps:
            note = "No OCR model is registered: the Vision order is used."
    return _capability(
        capability,
        label,
        model=model,
        source=source,
        fallbacks=_fallback_refs(res.fallbacks, index),
        warning=_not_servable_warning(model, label),
        note=note,
    )


def _rerank(request: Any, index: dict[str, list[Any]]) -> dict[str, Any]:
    """Rerank, from the runtime's own resolver (``resolve.resolve_reranker``)."""
    from app.ai_router import resolve

    settings = getattr(request.app.state, "settings", None)
    try:
        rr = resolve.resolve_reranker(settings)
    except Exception as exc:  # pragma: no cover - never fail the read
        logger.warning("model_resolution_rerank_failed error=%s", str(exc)[:160])
        return _capability(
            "rerank",
            "Rerank",
            model=None,
            source="none",
            warning=f"The reranker could not be resolved: {str(exc)[:160]}",
        )
    res = rr.resolution
    if rr.tier == "degraded" or not res.model:
        return _capability(
            "rerank",
            "Rerank",
            model=None,
            source="none",
            warning="No reranker: retrieval results stay in score order (rerank_degraded) — "
            f"{resolve._RERANK_HINT}.",
        )
    model = _describe(res.model, index)
    if model is not None and not model.get("provider") and res.provider:
        model["provider"] = res.provider
    # Rerank fallbacks are endpoint labels ("provider/model", "local/<model>").
    fallbacks = [
        {"model_id": str(lbl), "provider": None, "servable": None} for lbl in res.fallbacks
    ]
    note = (
        "The local cross-encoder (RAG_CROSS_ENCODER_MODEL): no hosted reranker is configured."
        if rr.tier == "local"
        else "Registry rerank models in order, then the env endpoints"
        + (", then the local cross-encoder." if rr.local_available else ".")
    )
    return _capability(
        "rerank",
        "Rerank",
        model=model,
        source=_FACADE_SOURCES.get(res.source, res.source),
        fallbacks=fallbacks,
        warning=_not_servable_warning(model, "rerank"),
        note=note,
    )


# ── roles ────────────────────────────────────────────────────────────────────


def _roles(
    tenant_id: str, index: dict[str, list[Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    from types import SimpleNamespace

    from app.ai_router.deployment_roles import deployment_role_models
    from app.ai_router.registry import tenant_policy_role_models
    from app.ai_router.resolve import ModelNotConfiguredError, resolve_reasoning
    from app.ai_router.role_preference import ROLE_TASK_TYPES, ROUTED_TASK_TYPES, env_pin_model

    try:
        pins = tenant_policy_role_models(tenant_id)
    except Exception as exc:
        logger.warning("model_resolution_tenant_pins_failed error=%s", str(exc)[:160])
        pins = {}
        warnings.append("Your tenant's routing policies could not be read; pins are not shown.")
    try:
        role_map = deployment_role_models()
    except Exception as exc:  # pragma: no cover - never fail the read
        logger.warning("model_resolution_role_map_failed error=%s", str(exc)[:160])
        role_map = {}

    # What a goal's router carries for this tenant: its pins and the role map.
    router = SimpleNamespace(_override="", _policy_roles=dict(pins), _role_map=dict(role_map))
    # resolve_reasoning's source -> this panel's source vocabulary.
    source_names = {
        "override": "tenant_pin",
        "tenant_pin": "tenant_pin",
        "registry_preference": "registry_order",
        "registry_cheapest": "registry_cheapest",
        "deployment_role_map": "deployment_profile",
        "provider_default": "default",
        "local_default": "local_default",
    }

    by_task: dict[str, list[str]] = {}
    for role, task in ROLE_TASK_TYPES.items():
        by_task.setdefault(task, []).append(role)
    order = [t for t in _TASK_LABELS if t in by_task] + sorted(
        t for t in by_task if t not in _TASK_LABELS
    )
    out: list[dict[str, Any]] = []
    for task in order:
        routed = task in ROUTED_TASK_TYPES
        # The ONE reasoning resolver decides — this panel only displays it.
        try:
            resolution = resolve_reasoning(task, router=router)
            model_id = resolution.model
            source = source_names.get(resolution.source, "")
            if resolution.source == "env_pin":
                source = "env_pin" if model_id == env_pin_model(task) else "default"
            fallback_ids = [m for m in resolution.fallbacks if m != model_id][:3]
        except ModelNotConfiguredError:
            model_id, source, fallback_ids = "", "none", []
        model = _describe(model_id, index)
        warning = None
        if not model_id:
            warning = f"No model resolves for {_TASK_LABELS.get(task, task).lower()}."
        else:
            warning = _not_servable_warning(model, _TASK_LABELS.get(task, task).lower())
        if warning:
            warnings.append(warning)
        out.append(
            {
                "task_type": task,
                "label": _TASK_LABELS.get(task, task.replace("_", " ").title()),
                "roles": sorted(by_task[task]),
                "routed_by_goal_router": routed,
                "model": model,
                **_source(source),
                "fallbacks": [d for d in (_describe(f, index) for f in fallback_ids) if d],
                "warning": warning,
            }
        )
    return out


def build_resolution(request: Any, tenant_id: str) -> dict[str, Any]:
    """The full resolution payload of ``GET /models/resolution``."""
    from app.ai_router.selection import _ensure_seeded

    _ensure_seeded(model_registry)
    index = _configured_by_id()
    capabilities: list[dict[str, Any]] = []
    for build in (
        lambda: _reasoning(index),
        lambda: _embedding(request, index),
        lambda: _vision_like("vision", index),
        lambda: _vision_like("ocr", index),
        lambda: _rerank(request, index),
    ):
        capabilities.append(build())
    warnings = [str(c["warning"]) for c in capabilities if c.get("warning")]
    roles = _roles(tenant_id, index, warnings)
    return {
        "capabilities": capabilities,
        "roles": roles,
        "warnings": list(dict.fromkeys(warnings)),
        "sources": _SOURCE_LABELS,
    }
