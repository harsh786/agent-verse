"""Model Registry - catalog of available models with health tracking."""

from __future__ import annotations

import logging

from app.ai_router.models import (
    ModelCapability,
    ModelEndpoint,
    ModelRoutePolicy,
    ProviderHealth,
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
        """Best-effort (input, output) per-1k price for a model slug from the
        reference catalog; (0.0, 0.0) when unknown (self-hosted)."""
        want = model_id.split("/")[-1].lower()
        for m in BUILTIN_MODELS:
            if m.model_id.split("/")[-1].lower() == want:
                return (m.cost_per_1k_input, m.cost_per_1k_output)
        return (0.0, 0.0)

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
        self._tenant_policies.setdefault(tenant_id, {})[task_type.value] = policy

    def get_route_policy(self, tenant_id: str, task_type: TaskType) -> ModelRoutePolicy | None:
        return self._tenant_policies.get(tenant_id, {}).get(task_type.value)


# Module-level singleton
model_registry = ModelRegistry()
