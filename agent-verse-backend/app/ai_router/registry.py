"""Model Registry - catalog of available models with health tracking."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_router.models import (
    ModelCapability,
    ModelEndpoint,
    ModelRoutePolicy,
    ProviderHealth,
    RoutingMode,
    TaskType,
)

_log = logging.getLogger(__name__)

_TG = ModelCapability.TEXT_GENERATION
_TU = ModelCapability.TOOL_USE
_VI = ModelCapability.VISION
_SO = ModelCapability.STRUCTURED_OUTPUT
_EM = ModelCapability.EMBEDDING
_VD = ModelCapability.VIDEO_UNDERSTANDING

# Built-in model catalog
BUILTIN_MODELS: list[ModelEndpoint] = [
    # Anthropic
    ModelEndpoint(
        provider="anthropic",
        model_id="claude-opus-4-5",
        display_name="Claude Opus 4.5",
        capabilities=[_TG, _TU, _VI, _SO],
        context_window=200000,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        quality_score=0.97,
        compliance_ready=True,
    ),
    ModelEndpoint(
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        display_name="Claude Sonnet 4.5",
        capabilities=[_TG, _TU, _VI, _SO],
        context_window=200000,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        quality_score=0.92,
        compliance_ready=True,
    ),
    ModelEndpoint(
        provider="anthropic",
        model_id="claude-haiku-3-5",
        display_name="Claude Haiku 3.5",
        capabilities=[_TG, _TU, _VI],
        context_window=200000,
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        supports_streaming=True,
        supports_tools=True,
        quality_score=0.82,
    ),
    # OpenAI
    ModelEndpoint(
        provider="openai",
        model_id="gpt-5.2",
        display_name="GPT-5.2",
        capabilities=[_TG, _TU, _VI, _SO],
        context_window=128000,
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        supports_structured_output=True,
        quality_score=0.94,
    ),
    ModelEndpoint(
        provider="openai",
        model_id="gpt-4o-mini",
        display_name="GPT-4o Mini",
        capabilities=[_TG, _TU, _VI],
        context_window=128000,
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        supports_streaming=True,
        supports_tools=True,
        quality_score=0.80,
    ),
    ModelEndpoint(
        provider="openai",
        model_id="text-embedding-3-large",
        display_name="Text Embedding 3 Large",
        capabilities=[_EM],
        context_window=8192,
        cost_per_1k_input=0.00013,
        supports_streaming=False,
        supports_tools=False,
        quality_score=0.93,
    ),
    # Gemini
    ModelEndpoint(
        provider="gemini",
        model_id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        capabilities=[_TG, _TU, _VI],
        context_window=1000000,
        cost_per_1k_input=0.0001,
        cost_per_1k_output=0.0004,
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        quality_score=0.85,
    ),
    ModelEndpoint(
        provider="gemini",
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        capabilities=[_TG, _TU, _VI, _VD],
        context_window=2000000,
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.01,
        supports_streaming=True,
        supports_tools=True,
        supports_vision=True,
        quality_score=0.95,
    ),
    # Groq
    ModelEndpoint(
        provider="groq",
        model_id="llama-3.3-70b-versatile",
        display_name="Llama 3.3 70B (Groq)",
        capabilities=[_TG, _TU],
        context_window=128000,
        cost_per_1k_input=0.00059,
        cost_per_1k_output=0.00079,
        supports_streaming=True,
        supports_tools=True,
        quality_score=0.86,
        avg_latency_ms=200,
    ),
    ModelEndpoint(
        provider="groq",
        model_id="llama-3.1-8b-instant",
        display_name="Llama 3.1 8B Instant (Groq)",
        capabilities=[_TG, _TU],
        context_window=128000,
        cost_per_1k_input=0.00005,
        cost_per_1k_output=0.00008,
        supports_streaming=True,
        supports_tools=True,
        quality_score=0.72,
        avg_latency_ms=100,
    ),
    # Voyage (embedding)
    ModelEndpoint(
        provider="voyage",
        model_id="voyage-3-large",
        display_name="Voyage 3 Large",
        capabilities=[_EM],
        context_window=32000,
        cost_per_1k_input=0.00018,
        supports_streaming=False,
        quality_score=0.95,
    ),
]


class ModelRegistry:
    """Tenant-scoped model registry with health tracking."""

    def __init__(self) -> None:
        self._models: dict[str, ModelEndpoint] = {
            f"{m.provider}/{m.model_id}": m for m in BUILTIN_MODELS
        }
        self._health: dict[str, ProviderHealth] = {}
        # tenant_id → task_value → policy
        self._tenant_policies: dict[str, dict[str, ModelRoutePolicy]] = {}
        # Models the deployment actually CONFIGURED (seeded from env/Settings/
        # LLMConfigStore + UI). Kept separate from the static BUILTIN_MODELS cloud
        # catalog so capability selection only ever picks a model this deployment
        # can actually serve. See app/ai_router/seeder.py.
        self._configured: dict[str, ModelEndpoint] = {}

    def register_configured(self, endpoint: ModelEndpoint) -> None:
        """Register (or replace) a model this deployment is configured to serve."""
        self._configured[f"{endpoint.provider}/{endpoint.model_id}"] = endpoint

    def remove_configured(self, provider: str, model_id: str) -> bool:
        """Remove a configured model; return True if it existed."""
        return self._configured.pop(f"{provider}/{model_id}", None) is not None

    def clear_configured(self) -> None:
        """Drop all configured models (idempotent re-seeding)."""
        self._configured.clear()

    def list_configured(self, capability: ModelCapability | None = None) -> list[ModelEndpoint]:
        """Configured, available models, optionally filtered by capability."""
        models = [m for m in self._configured.values() if m.is_available]
        if capability is not None:
            models = [m for m in models if capability in m.capabilities]
        return models

    def price_for(self, model_id: str) -> tuple[float, float]:
        """(input, output) per-1k price for a model slug.

        Same source as the ledger (:func:`app.intelligence.cost_tracker.model_pricing`),
        so routing cost estimates and budget charges agree — including the
        configurable fallback for unknown / self-hosted models."""
        from app.intelligence.cost_tracker import model_pricing

        inp, out = model_pricing(model_id)
        return (inp / 1000, out / 1000)

    def list_models(
        self,
        capability: ModelCapability | None = None,
        provider: str | None = None,
    ) -> list[ModelEndpoint]:
        models = list(self._models.values())
        if capability:
            models = [m for m in models if capability in m.capabilities]
        if provider:
            models = [m for m in models if m.provider == provider]
        return models

    def get_model(self, provider: str, model_id: str) -> ModelEndpoint | None:
        return self._models.get(f"{provider}/{model_id}")

    def add_custom_model(self, tenant_id: str, endpoint: ModelEndpoint) -> None:
        key = f"tenant:{tenant_id}/{endpoint.provider}/{endpoint.model_id}"
        self._models[key] = endpoint

    def update_health(
        self,
        provider: str,
        latency_ms: float = 0,
        error: bool = False,
        error_msg: str = "",
    ) -> None:
        h = self._health.setdefault(provider, ProviderHealth(provider=provider))
        if error:
            h.error_rate_5m = min(1.0, h.error_rate_5m + 0.1)
            h.last_error = error_msg
        else:
            h.avg_latency_ms = h.avg_latency_ms * 0.9 + latency_ms * 0.1
            h.error_rate_5m = max(0.0, h.error_rate_5m - 0.01)
        import datetime

        h.last_checked_at = datetime.datetime.now(datetime.UTC).isoformat()
        h.is_healthy = h.error_rate_5m < 0.5

    def get_provider_health(self, provider: str) -> ProviderHealth:
        return self._health.get(provider, ProviderHealth(provider=provider))

    def set_route_policy(
        self, tenant_id: str, task_type: TaskType, policy: ModelRoutePolicy
    ) -> None:
        """Save a tenant routing policy — durably when the Redis store is wired.

        Raises when the durable write fails (the API maps it to 503) instead of
        keeping a policy only this process can see.
        """
        from app.ai_router.registry_store import get_model_registry_store

        store = get_model_registry_store()
        if store is not None:
            store.set_route_policy(tenant_id, task_type.value, _policy_to_dict(policy))
        self._tenant_policies.setdefault(tenant_id, {})[task_type.value] = policy

    def list_route_policies(self, tenant_id: str) -> dict[str, ModelRoutePolicy]:
        """All of a tenant's policies; the shared store is authoritative when wired."""
        from app.ai_router.registry_store import get_model_registry_store

        store = get_model_registry_store()
        if store is None:
            return dict(self._tenant_policies.get(tenant_id, {}))
        out: dict[str, ModelRoutePolicy] = {}
        for task, data in store.get_route_policies(tenant_id).items():
            policy = _policy_from_dict(task, data)
            if policy is not None:
                out[task] = policy
        return out

    def get_route_policy(self, tenant_id: str, task_type: TaskType) -> ModelRoutePolicy | None:
        try:
            return self.list_route_policies(tenant_id).get(task_type.value)
        except Exception:
            # Read path used for model *selection*: degrade to this process's copy.
            return self._tenant_policies.get(tenant_id, {}).get(task_type.value)


# Roles the agent graph routes, keyed by the TaskType a policy is saved under.
_POLICY_GRAPH_ROLES: dict[str, str] = {
    "planning": "planning",
    "execution": "execution",
    "verification": "verification",
}


def _policy_to_dict(policy: ModelRoutePolicy) -> dict[str, Any]:
    return {
        "routing_mode": policy.routing_mode.value,
        "preferred_provider": policy.preferred_provider,
        "preferred_model": policy.preferred_model,
        "fallback_chain": list(policy.fallback_chain),
    }


def _policy_from_dict(task: str, data: Any) -> ModelRoutePolicy | None:
    if not isinstance(data, dict):
        return None
    try:
        return ModelRoutePolicy(
            task_type=TaskType(task),
            routing_mode=RoutingMode(data.get("routing_mode", "tenant_default")),
            preferred_provider=data.get("preferred_provider"),
            preferred_model=data.get("preferred_model"),
            fallback_chain=list(data.get("fallback_chain") or []),
        )
    except ValueError:
        return None


def policy_is_enforced(policy: ModelRoutePolicy) -> bool:
    """True when goals actually honour *policy* (see tenant_policy_role_models)."""
    return policy.task_type.value in _POLICY_GRAPH_ROLES and bool(policy.preferred_model)


def tenant_policy_role_models(
    tenant_id: str, *, servable: set[str] | None = None
) -> dict[str, str]:
    """``{role: model}`` pins from the tenant's routing policies for the agent graph.

    A planning/execution/verification policy with a ``preferred_model`` pins that
    role. With ``servable`` (a multi-endpoint provider), a model the provider
    cannot route is skipped. Other routing modes (cheapest, fastest, …) are not
    enforced on goals — the API reports that when the policy is saved.
    """
    roles: dict[str, str] = {}
    for task, policy in model_registry.list_route_policies(tenant_id).items():
        role = _POLICY_GRAPH_ROLES.get(task)
        model = (policy.preferred_model or "").strip()
        if role is None or not model:
            continue
        if servable is not None and model not in servable:
            continue
        roles[role] = model
    return roles


# Module-level singleton
model_registry = ModelRegistry()
