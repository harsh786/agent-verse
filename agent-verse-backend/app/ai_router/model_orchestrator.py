"""Canonical live model assignment owner for every agent role.

Runtime profiles supply desired constraints. ModelOrchestrator resolves those constraints
against provider health, budget consumption, latency, quality, and fallback policy. The legacy
``ModelRouter`` remains a compatibility facade for callers on the previous interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.ai_router.cost_latency_quality_policy import CostLatencyQualityPolicy
from app.ai_router.provider_health_policy import ProviderHealthPolicy
from app.ai_router.role_policy import RolePolicy

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig
    from app.ingestion.content_classifier import ContentType

_TIER_MODELS: dict[str, dict[str, str]] = {
    "high": {
        "planner": "gpt-5.2",
        "executor": "gpt-5.2",
        "verifier": "gpt-5.2",
        "judge": "gpt-5.2",
        "embedder": "text-embedding-3-large",
        "reranker": "gpt-4o-mini",
        "classifier": "gpt-4o-mini",
    },
    "medium": {
        "planner": "gpt-4o",
        "executor": "gpt-4o",
        "verifier": "gpt-4o",
        "judge": "gpt-4o",
        "embedder": "text-embedding-3-small",
        "reranker": "gpt-4o-mini",
        "classifier": "gpt-4o-mini",
    },
    "low": {
        "planner": "gpt-4o-mini",
        "executor": "gpt-4o-mini",
        "verifier": "gpt-4o-mini",
        "judge": "gpt-4o-mini",
        "embedder": "voyage-3-lite",
        "reranker": "gpt-4o-mini",
        "classifier": "gpt-4o-mini",
    },
}

_MODEL_PROVIDER: dict[str, str] = {
    "gpt-5.2": "openai",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4o-audio": "openai",
    "claude-3-5-sonnet": "anthropic",
    "claude-3-haiku": "anthropic",
    "gemini-2.5-pro": "google",
    "text-embedding-3-large": "openai",
    "text-embedding-3-small": "openai",
    "voyage-3-lite": "voyage",
}


def provider_for_model(model: str) -> str:
    """Module-level model→provider map (defaults to ``openai`` for unknown models).

    Mirror of :meth:`ModelOrchestrator.provider_for_model`; exposed so call sites
    that only have a model name (e.g. the executor's provider-health wiring) can
    resolve the provider without an orchestrator instance.
    """
    return _MODEL_PROVIDER.get(model, "openai")


# Health-based failover target: when a provider's circuit is open, prefer this
# provider first. The full search order also sweeps the remaining chat providers.
_FALLBACK_PROVIDER: dict[str, str] = {
    "openai": "anthropic",
    "anthropic": "openai",
    "google": "openai",
    "voyage": "openai",
}

# Representative chat model per provider (generic failover target).
_PROVIDER_CHAT_MODEL: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet",
    "google": "gemini-2.5-pro",
}

# Vision-capable model per provider (failover for image/video extraction).
_PROVIDER_VISION_MODEL: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet",
    "google": "gemini-2.5-pro",
}

# Audio-capable model per provider (failover for audio extraction). Anthropic has
# no audio model, so it is deliberately absent — audio fails over to google.
_PROVIDER_AUDIO_MODEL: dict[str, str] = {
    "openai": "gpt-4o-audio",
    "google": "gemini-2.5-pro",
}

# Ordered provider preference used when sweeping for a healthy fallback.
_PROVIDER_ORDER: tuple[str, ...] = ("openai", "anthropic", "google")

_CONTENT_TYPE_MODALITY: dict[str, str] = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "code": "code",
    "text": "text",
    "pdf": "text",
    "docx": "text",
    "markdown": "text",
    "html": "text",
    "csv": "text",
    "json": "text",
}

_MULTIMODAL_MODELS: dict[str, dict[str, Any]] = {
    "image": {"extractor": "gpt-4o", "reasoner": "gpt-5.2", "requires_vision": True},
    "audio": {"extractor": "gpt-4o-audio", "reasoner": "gpt-5.2", "requires_vision": False},
    "video": {"extractor": "gemini-2.5-pro", "reasoner": "gpt-5.2", "requires_vision": True},
    "code": {"extractor": "gpt-5.2", "reasoner": "gpt-5.2", "requires_vision": False},
    "text": {"extractor": "gpt-4o", "reasoner": "gpt-5.2", "requires_vision": False},
}

_BUDGET_75 = 0.75
_BUDGET_90 = 0.90

# Plan-tier-aware model ceiling. FREE tenants are hard-capped at the cheapest
# quality tier regardless of budget headroom (cost control for a non-paying
# tier); STARTER cannot reach "high" even when complexity/risk would warrant
# it; PROFESSIONAL and ENTERPRISE have no plan-based ceiling — they can still
# be downgraded by the existing budget-spent logic above, but plan alone never
# raises the tier past what complexity/risk/budget already selected.
_PLAN_TIER_CAP: dict[str, str] = {
    "free": "low",
    "starter": "medium",
    "professional": "high",
    "enterprise": "high",
}
_TIER_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass
class ModelRoleAssignment:
    planner: str
    executor: str
    verifier: str
    judge: str
    embedder: str
    reranker: str
    classifier: str
    quality_tier: str = "medium"
    latency_class: str = "interactive"
    plan_tier: str = ""


@dataclass
class MultimodalModelAssignment:
    modality: str
    extractor_model: str
    reasoner_model: str
    requires_vision: bool = False
    requires_audio: bool = False


class ModelOrchestrator:
    def __init__(self, *, health_policy: ProviderHealthPolicy | None = None) -> None:
        self._cost_policy = CostLatencyQualityPolicy()
        self._health_policy = health_policy or ProviderHealthPolicy()
        self._role_policy = RolePolicy()

    def select_models(
        self,
        config: PatternConfig,
        budget_spent_ratio: float = 0.0,
    ) -> ModelRoleAssignment:
        from app.agent.pattern_config import Complexity, RiskLevel

        props = config.goal_properties
        complexity = props.complexity if props else Complexity.MEDIUM
        risk = props.risk if props else RiskLevel.LOW
        time_sens = getattr(props, "time_sensitivity", "interactive") if props else "interactive"

        tier = self._cost_policy.select_tier(
            complexity=complexity, risk=risk, latency_requirement=time_sens
        )
        if budget_spent_ratio >= _BUDGET_90:
            tier = "low"
        elif budget_spent_ratio >= _BUDGET_75 and tier == "high":
            tier = "medium"

        plan_tier = (config.plan_tier or "").strip().lower()
        plan_cap = _PLAN_TIER_CAP.get(plan_tier)
        if plan_cap is not None and _TIER_RANK[tier] > _TIER_RANK[plan_cap]:
            tier = plan_cap

        tier_models = _TIER_MODELS[tier]

        def resolve(role: str, hint: str) -> str:
            model = hint if hint and hint != "default" else tier_models.get(role, "gpt-4o-mini")
            return self._with_failover(model)

        latency_class = "realtime" if time_sens == "realtime" else "interactive"
        return ModelRoleAssignment(
            planner=resolve("planner", config.model_planner),
            executor=resolve("executor", config.model_executor),
            verifier=resolve("verifier", config.model_verifier),
            judge=tier_models["judge"],
            embedder=tier_models["embedder"],
            reranker=tier_models["reranker"],
            classifier=resolve("classifier", config.model_classifier),
            quality_tier=tier,
            latency_class=latency_class,
            plan_tier=plan_tier,
        )

    def select_for_content_type(self, content_type: ContentType) -> MultimodalModelAssignment:
        """Pick a vision/audio-aware extractor+reasoner pair for a classified content type.

        TODO(D-14 wiring): call this from the multimodal ingestion path in
        ``app/ingestion/`` (the multimodal parser/transcription dispatch, e.g. where
        ``ContentType.IMAGE``/``AUDIO``/``VIDEO`` are routed to a parser) to choose the
        extraction model instead of a hardcoded one. Do NOT edit the ingestion package
        as part of this ai_router change.
        """
        modality = _CONTENT_TYPE_MODALITY.get(content_type.value, "text")
        spec = _MULTIMODAL_MODELS.get(modality, _MULTIMODAL_MODELS["text"])
        requires_vision = bool(spec.get("requires_vision", False))
        requires_audio = modality == "audio"
        # Prefer the cheapest CONFIGURED vision/OCR model from the generic registry
        # for image/vision extraction, instead of the hardcoded cloud slug — so the
        # deployment's actual model is used. Falls back to the spec when none is
        # configured.
        _extractor = str(spec["extractor"])
        if requires_vision:
            try:
                from app.ai_router.selection import resolve_vision_model

                _extractor = resolve_vision_model(_extractor)
            except Exception:  # pragma: no cover - never block selection
                pass
        # The extractor must preserve the modality capability across a failover;
        # the reasoner reasons over already-extracted text, so a plain chat model is fine.
        return MultimodalModelAssignment(
            modality=modality,
            extractor_model=self._with_failover(
                _extractor,
                requires_vision=requires_vision,
                requires_audio=requires_audio,
            ),
            reasoner_model=self._with_failover(str(spec["reasoner"])),
            requires_vision=requires_vision,
            requires_audio=requires_audio,
        )

    def provider_for_model(self, model: str) -> str:
        """Map a model name to its provider (defaults to ``openai`` for unknown models)."""
        return _MODEL_PROVIDER.get(model, "openai")

    def record_provider_result(
        self,
        provider: str,
        *,
        ok: bool,
        latency_ms: float | None = None,
    ) -> None:
        """Record the outcome of a provider call so the circuit breaker can trip/recover.

        This is the public entry point the agent loop invokes at each real
        provider-call site. On success it feeds latency to the health policy; on
        failure it advances the provider toward an open circuit, after which
        :meth:`select_models` / :meth:`select_for_content_type` route away from it.

        TODO(D-13 wiring): call this from the executor/verifier/planner provider-call
        site in ``app/agent/nodes/executor_mixin.py`` (around the LLM ``complete``/
        ``_active_breaker`` block, ~line 941) — pass ``provider_for_model(model)`` and
        the measured latency. Do NOT edit the mixin as part of this ai_router change.
        """
        if ok:
            self._health_policy.record_success(
                provider, latency_ms if latency_ms is not None else 500.0
            )
        else:
            self._health_policy.record_failure(provider)

    def _provider_open(self, provider: str) -> bool:
        return self._health_policy.check(provider).circuit_open

    def _capability_model(self, provider: str, *, vision: bool, audio: bool) -> str | None:
        if audio:
            return _PROVIDER_AUDIO_MODEL.get(provider)
        if vision:
            return _PROVIDER_VISION_MODEL.get(provider)
        return _PROVIDER_CHAT_MODEL.get(provider)

    def _with_failover(
        self,
        model: str,
        *,
        requires_vision: bool = False,
        requires_audio: bool = False,
    ) -> str:
        provider = _MODEL_PROVIDER.get(model, "openai")
        if not self._provider_open(provider):
            return model

        # Sweep providers (preferred fallback first) for a healthy one that still
        # offers the required capability.
        preferred = _FALLBACK_PROVIDER.get(provider, "anthropic")
        order = [preferred, *(p for p in _PROVIDER_ORDER if p != preferred and p != provider)]
        for candidate in order:
            if self._provider_open(candidate):
                continue
            fallback = self._capability_model(
                candidate, vision=requires_vision, audio=requires_audio
            )
            if fallback:
                return fallback

        # Every candidate is unhealthy or lacks the capability — keep the original
        # model as a last resort so selection is never empty.
        return model


class ModelOrchestratorAdapter:
    """Adapter that wraps ModelOrchestrator to implement the model_for() interface
    expected by graph.py, with lazy PatternConfig resolution from agent state context.

    This allows ModelOrchestrator to be used as a drop-in replacement for ModelRouter
    without changing any graph.py call sites.
    """

    def __init__(
        self,
        orchestrator: ModelOrchestrator | None = None,
        *,
        default_tier: str = "medium",
    ) -> None:
        self._orchestrator = orchestrator or ModelOrchestrator()
        self._default_tier = default_tier
        self._cached_assignment: ModelRoleAssignment | None = None
        self._last_budget_ratio: float = 0.0

    def update_from_profile(
        self,
        runtime_profile: Any,
        budget_spent_ratio: float = 0.0,
    ) -> None:
        """Update model assignment from a GoalRuntimeProfile.
        Called by _node_plan when dynamic orchestration is active."""
        try:
            from app.agent.pattern_config import GoalProperties, PatternConfig  # noqa: F401

            pattern_config = PatternConfig(
                goal_properties=runtime_profile.properties,
                model_planner=getattr(runtime_profile.model_plan, "planner", "") or "",
                model_executor=getattr(runtime_profile.model_plan, "executor", "") or "",
                model_verifier=getattr(runtime_profile.model_plan, "verifier", "") or "",
                model_classifier="",
                # D-xx wiring: thread the tenant's plan tier (GoalRuntimeProfile.tenant_plan,
                # C5) into model selection so free/starter tenants are capped to
                # cheaper tiers regardless of budget headroom. Missing/unknown
                # attribute falls back to "" (no plan-based cap).
                plan_tier=str(getattr(runtime_profile, "tenant_plan", "") or ""),
            )
            self._cached_assignment = self._orchestrator.select_models(
                pattern_config, budget_spent_ratio
            )
            self._last_budget_ratio = budget_spent_ratio
        except Exception:
            self._cached_assignment = None

    def model_for(
        self,
        task_type: str,
        *,
        fallback: str = "",
        goal: str = "",
    ) -> str:
        """Return the best model for a task type.

        Reasoning roles first consult the generic cost-aware model registry
        (cheapest CONFIGURED model for the capability). This replaces the
        hardcoded ``_TIER_MODELS`` cloud slugs (e.g. ``gpt-4o``) with a model the
        deployment can actually serve, and picks the cheapest when several are
        configured. Falls back to the tier assignment when nothing is registered.
        """
        if task_type in (
            "planning", "execution", "verification", "classification",
            "reflection", "think", "thinking",
        ):
            try:
                from app.ai_router.selection import select_configured_model_id

                _choice = select_configured_model_id(task_type)
                if _choice:
                    return _choice
            except Exception:  # pragma: no cover - never block on the registry
                pass

        assignment = self._cached_assignment
        if assignment is None:
            # No profile yet — use default tier models
            tier_models = _TIER_MODELS.get(self._default_tier, _TIER_MODELS["medium"])
            mapping = {
                "planning": tier_models["planner"],
                "execution": tier_models["executor"],
                "verification": tier_models["verifier"],
                "reflection": tier_models["planner"],
                "think": tier_models["planner"],
                "thinking": tier_models["planner"],
                "classification": tier_models["classifier"],
                "judge": tier_models["judge"],
            }
            return mapping.get(task_type, tier_models["planner"])

        mapping = {
            "planning": assignment.planner,
            "execution": assignment.executor,
            "verification": assignment.verifier,
            "reflection": assignment.planner,
            "think": assignment.planner,
            "thinking": assignment.planner,
            "classification": assignment.classifier,
            "judge": assignment.judge,
        }
        result = mapping.get(task_type, assignment.planner)
        return result or fallback or "gpt-4o-mini"

    def model_for_goal(self, task_type: str, *, goal: str = "") -> str:
        """Alias for model_for() with goal context (unused in orchestrator path)."""
        return self.model_for(task_type, goal=goal)

    def provider_for_model(self, model: str) -> str:
        """Delegate model→provider mapping to the wrapped orchestrator."""
        return self._orchestrator.provider_for_model(model)

    def record_provider_result(self, model: str, ok: bool, latency_ms: float = 0.0) -> None:
        """D-13: feed a live provider-call outcome into the health policy so failover
        learns. Accepts a *model* name (the executor's call site has the model, not the
        provider), maps it to its provider, and delegates to the orchestrator."""
        provider = self._orchestrator.provider_for_model(model)
        self._orchestrator.record_provider_result(provider, ok=ok, latency_ms=latency_ms)
