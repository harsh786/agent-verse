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

# Reasoning roles (planner / executor / verifier / judge / classifier) resolve
# through app.ai_router.resolve.resolve_reasoning — never a table. Embeddings are
# not routed here: every embedding uses the Model Registry embedder
# (app.providers.embedder_factory.resolve_embedder).
_REASONING_ROLE_TASK: dict[str, str] = {
    "planner": "planning",
    "executor": "execution",
    "verifier": "verification",
    "judge": "judge",
    "classifier": "classification",
}

_MODEL_PROVIDER: dict[str, str] = {
    "gpt-5.2": "openai",
    "gpt-4o": "openai",
    "gpt-4o-mini": "openai",
    "gpt-4o-audio": "openai",
    "claude-3-5-sonnet": "anthropic",
    "claude-3-haiku": "anthropic",
    "gemini-2.5-pro": "google",
}


_UNKNOWN_PROVIDER = "unknown"


def provider_for_model(model: str) -> str:
    """The provider serving *model*: the configured registry first (the deployment's
    own models, e.g. NVIDIA / on-prem), then the reference slugs, else ``"unknown"``.

    Unknown models used to map to ``"openai"``, so an NVIDIA / on-prem model's
    failures were recorded against openai and health failover targeted the wrong
    provider.
    """
    if not model:
        return _UNKNOWN_PROVIDER
    try:
        from app.ai_router.registry import model_registry

        for endpoint in model_registry.list_configured():
            if endpoint.model_id == model:
                return endpoint.provider
    except Exception:  # pragma: no cover - never block selection
        pass
    return _MODEL_PROVIDER.get(model, _UNKNOWN_PROVIDER)


def _configured_for_tier(tier: str, task: str = "planning") -> str:
    """Best CONFIGURED model for *task* within quality *tier* (highest quality,
    then cheaper), or ``""``.

    Used only when no explicit choice (override, tenant pin, saved registry
    order, env pin, role map, env default) decides the role: the goal's
    complexity then picks among the deployment's configured models.
    """
    try:
        from app.ai_router.selection import ordered_configured_models

        allowed = [
            m
            for m in ordered_configured_models(task)
            if _TIER_RANK[model_quality_tier(m.model_id)] <= _TIER_RANK[tier]
        ]
    except Exception:  # pragma: no cover - never block selection
        return ""
    if not allowed:
        return ""
    return str(max(allowed, key=lambda m: (m.quality_score, -m.cost_per_1k_input)).model_id)


# Health-based failover target: when a provider's circuit is open, prefer this
# provider first. The full search order also sweeps the remaining chat providers.
_FALLBACK_PROVIDER: dict[str, str] = {
    "openai": "anthropic",
    "anthropic": "openai",
    "google": "openai",
    "voyage": "openai",
}

# Vision (image / video) extraction has no per-provider literal: the extractor
# and its failover chain are the Model Registry's vision models
# (app.ai_router.resolve.resolve_vision). Reasoning failover is the registry
# reasoning order (resolve_fallback_models), never a per-provider chat model.

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

# Extractor reference slugs per modality (vision / audio: owned by the vision and
# speech resolvers). Text / code extraction and every reasoner are reasoning
# roles: resolve_reasoning.
_MULTIMODAL_MODELS: dict[str, dict[str, Any]] = {
    # Vision extractors ("") are resolved from the Model Registry, never a literal;
    # text / code extraction is a reasoning role (resolve_reasoning).
    "image": {"extractor": "", "requires_vision": True},
    "audio": {"extractor": "gpt-4o-audio", "requires_vision": False},
    "video": {"extractor": "", "requires_vision": True},
    "code": {"extractor": "", "requires_vision": False},
    "text": {"extractor": "", "requires_vision": False},
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

# Price bands (USD per 1M input tokens, from the single pricing source) that
# place an arbitrary model in a quality tier: lets plan caps and budget
# downgrades apply to pinned / role-mapped models, not just tier-table slugs.
_TIER_PRICE_CEILING: dict[str, float] = {"low": 1.0, "medium": 5.0}
_ROLE_FOR_TASK_BASE: dict[str, str] = {
    "reflection": "planning", "think": "planning", "thinking": "planning",
    "supervisor": "planning",
}


def model_quality_tier(model: str) -> str:
    """Quality tier of *model* by its price (``low`` / ``medium`` / ``high``)."""
    from app.intelligence.cost_tracker import model_pricing

    price_in, _ = model_pricing(model)
    if price_in <= _TIER_PRICE_CEILING["low"]:
        return "low"
    if price_in <= _TIER_PRICE_CEILING["medium"]:
        return "medium"
    return "high"


def plan_tier_cap(plan: str) -> str | None:
    """Highest model tier a plan may use (``None`` = no plan cap)."""
    return _PLAN_TIER_CAP.get((plan or "").strip().lower())


def _configured_within_cap(task: str, cap: str) -> str:
    """Cheapest CONFIGURED model eligible for *task* whose tier is within *cap* (or "").

    Only models the deployment actually serves: never a reference slug.
    """
    try:
        from app.ai_router.selection import ordered_configured_models

        allowed = [
            m
            for m in ordered_configured_models(task)
            if _TIER_RANK[model_quality_tier(m.model_id)] <= _TIER_RANK[cap]
        ]
    except Exception:  # pragma: no cover - never block selection
        return ""
    if not allowed:
        return ""
    return str(min(allowed, key=lambda m: m.cost_per_1k_input).model_id)


def reference_role_model(tier: str, role: str, vendor: str = "") -> str:
    """The model for *role* when no profile / hint names one.

    Reasoning roles: :func:`app.ai_router.resolve.reasoning_model` (the Model
    Registry, env pins, role map — never a vendor table; ``""`` when nothing is
    configured, so the provider reports the honest error). Any other role:
    ``""`` (embeddings use the Model Registry embedder, not a role table).
    """
    del vendor  # the resolver decides from what is configured, not the vendor
    task = _REASONING_ROLE_TASK.get(role)
    if task:
        from app.ai_router.resolve import reasoning_model

        return reasoning_model(task)
    del tier
    return ""


@dataclass
class ModelRoleAssignment:
    planner: str
    executor: str
    verifier: str
    judge: str
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
    # Models to fail over to after ``extractor_model``, in order (vision: the rest
    # of the Model Registry vision chain). Empty for a vision content type with no
    # vision model configured — ``extractor_model`` is then ``""`` too.
    extractor_fallbacks: tuple[str, ...] = ()


class ModelOrchestrator:
    def __init__(self, *, health_policy: ProviderHealthPolicy | None = None) -> None:
        self._cost_policy = CostLatencyQualityPolicy()
        self._health_policy = health_policy or ProviderHealthPolicy()
        self._role_policy = RolePolicy()

    def select_models(
        self,
        config: PatternConfig,
        budget_spent_ratio: float = 0.0,
        *,
        vendor: str = "",
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

        # The plan / budget ceiling (the complexity-chosen tier is no ceiling).
        caps = [c for c in (plan_cap,) if c is not None]
        if budget_spent_ratio >= _BUDGET_90:
            caps.append("low")
        elif budget_spent_ratio >= _BUDGET_75:
            caps.append("medium")
        cap = min(caps, key=lambda t: _TIER_RANK[t]) if caps else None

        def resolve(role: str, hint: str) -> str:
            # An explicit hint (agent / runtime profile) wins; otherwise the ONE
            # reasoning resolver (override > tenant pin > saved registry order >
            # env pins > role map > env default). Only when nothing explicit
            # decided (the resolver fell to the unranked registry) does the goal's
            # quality tier pick among the configured models; a plan / budget tier
            # cap clamps any choice.
            task = _REASONING_ROLE_TASK[role]
            model = hint if hint and hint != "default" else ""
            if not model:
                from app.ai_router.resolve import ModelNotConfiguredError, resolve_reasoning

                try:
                    resolution = resolve_reasoning(task)
                except ModelNotConfiguredError:
                    resolution = None
                if resolution is not None:
                    model = resolution.model
                    if resolution.source == "registry_cheapest":
                        model = _configured_for_tier(tier, task) or model
            over_cap = cap is not None and bool(model) and (
                _TIER_RANK[model_quality_tier(model)] > _TIER_RANK[cap]
            )
            if over_cap and cap is not None:
                model = _configured_within_cap(task, cap) or model
            return self._with_failover(model, task=task) if model else ""

        latency_class = "realtime" if time_sens == "realtime" else "interactive"
        return ModelRoleAssignment(
            planner=resolve("planner", config.model_planner),
            executor=resolve("executor", config.model_executor),
            verifier=resolve("verifier", config.model_verifier),
            judge=resolve("judge", ""),
            classifier=resolve("classifier", config.model_classifier),
            quality_tier=tier,
            latency_class=latency_class,
            plan_tier=plan_tier,
        )

    def select_for_content_type(self, content_type: ContentType) -> MultimodalModelAssignment:
        """Pick a vision/audio-aware extractor+reasoner pair for a classified content type.

        Image / video extraction uses the Model Registry's vision model
        (:func:`app.ai_router.resolve.resolve_vision`) with the rest of the
        vision chain as ``extractor_fallbacks``; ``extractor_model`` is ``""``
        when no vision model is configured. Called by the multimodal pipeline.
        """
        modality = _CONTENT_TYPE_MODALITY.get(content_type.value, "text")
        spec = _MULTIMODAL_MODELS.get(modality, _MULTIMODAL_MODELS["text"])
        requires_vision = bool(spec.get("requires_vision", False))
        requires_audio = modality == "audio"
        from app.ai_router.resolve import reasoning_model

        # The reasoner reasons over already-extracted text: a reasoning role.
        reasoner = self._with_failover(reasoning_model("synthesis"))
        if requires_vision:
            # The extractor is the Model Registry's vision model; its failover
            # chain is the rest of the registry vision order. A model whose
            # provider circuit is open moves behind the healthy ones.
            extractor, fallbacks = self._vision_extractor()
            return MultimodalModelAssignment(
                modality=modality,
                extractor_model=extractor,
                reasoner_model=reasoner,
                requires_vision=True,
                requires_audio=False,
                extractor_fallbacks=fallbacks,
            )
        # The extractor must preserve the modality capability across a failover;
        # the reasoner reasons over already-extracted text, so a plain chat model is fine.
        return MultimodalModelAssignment(
            modality=modality,
            extractor_model=self._with_failover(
                str(spec["extractor"]) or reasoning_model("extraction"),
                requires_audio=requires_audio,
            ),
            reasoner_model=reasoner,
            requires_vision=False,
            requires_audio=requires_audio,
        )

    def _vision_extractor(self) -> tuple[str, tuple[str, ...]]:
        """``(model, fallbacks)`` from the registry vision chain; ``("", ())`` when
        no vision model is configured (the caller fails honestly)."""
        from app.ai_router.resolve import ModelNotConfiguredError, resolve_vision

        try:
            res = resolve_vision()
        except ModelNotConfiguredError:
            return "", ()
        chain = [res.model, *res.fallbacks]
        healthy = [m for m in chain if not self._model_provider_open(m)]
        ordered = healthy + [m for m in chain if m not in healthy]
        return ordered[0], tuple(ordered[1:])

    def _model_provider_open(self, model: str) -> bool:
        provider = provider_for_model(model)
        return provider != _UNKNOWN_PROVIDER and self._provider_open(provider)

    def provider_for_model(self, model: str) -> str:
        """Map a model name to its provider (``"unknown"`` when it cannot be told)."""
        return provider_for_model(model)

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
            # Vision failover is the registry vision chain (_vision_extractor).
            return None
        return None

    def _with_failover(
        self,
        model: str,
        *,
        requires_vision: bool = False,
        requires_audio: bool = False,
        task: str = "planning",
    ) -> str:
        provider = provider_for_model(model)
        if provider == _UNKNOWN_PROVIDER or not self._provider_open(provider):
            return model

        if not (requires_vision or requires_audio):
            # A reasoning model whose provider's circuit is open: the next model
            # of the registry order (resolve_fallback_models) on a healthy provider.
            try:
                from app.ai_router.selection import resolve_fallback_models

                for candidate in resolve_fallback_models(task, model, limit=8):
                    cand_provider = provider_for_model(candidate)
                    if cand_provider == _UNKNOWN_PROVIDER or not self._provider_open(
                        cand_provider
                    ):
                        return candidate
            except Exception:  # pragma: no cover - never block selection
                pass
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
        self._override = ""
        self._role_map: dict[str, str] = {}
        self._policy_roles: dict[str, str] = {}
        self._plan_tier = ""
        self._vendor = ""
        self._bound_provider: Any = None

    def set_provider_vendor(self, vendor: str) -> None:
        """The vendor of the goal's provider ("anthropic", "nvidia", ...).

        Last-resort models (nothing configured in the registry) come from this
        vendor's profile, never from the OpenAI tier table (a01-F022-02)."""
        self._vendor = str(vendor or "").strip().lower()

    def set_plan_tier(self, plan: str) -> None:
        """The tenant's plan: caps every model this adapter returns (PROV-18)."""
        self._plan_tier = str(getattr(plan, "value", plan) or "").strip().lower()

    def _tier_cap(self) -> str | None:
        caps: list[str] = []
        plan = self._plan_tier or (
            self._cached_assignment.plan_tier if self._cached_assignment else ""
        )
        plan_cap = plan_tier_cap(plan)
        if plan_cap is not None:
            caps.append(plan_cap)
        if self._last_budget_ratio >= _BUDGET_90:
            caps.append("low")
        elif self._last_budget_ratio >= _BUDGET_75:
            caps.append("medium")
        return min(caps, key=lambda t: _TIER_RANK[t]) if caps else None

    def _capped(self, model: str, task_type: str) -> str:
        """Clamp *model* to the plan / budget tier cap (overrides and role maps
        used to bypass both). Prefers the cheapest configured model within the
        cap, else the cap's tier model; the reason is logged."""
        cap = self._tier_cap()
        if not model or cap is None:
            return model
        tier = model_quality_tier(model)
        if _TIER_RANK[tier] <= _TIER_RANK[cap]:
            return model
        clamped = _configured_within_cap(task_type, cap)
        import logging

        if not clamped:
            # Nothing configured fits the cap: a model the deployment does not
            # serve would only fail.
            logging.getLogger(__name__).warning(
                "model_cap_unenforceable model=%s tier=%s cap=%s plan=%s vendor=%s",
                model, tier, cap, self._plan_tier, self._vendor,
            )
            return model
        logging.getLogger(__name__).info(
            "model_clamped_by_plan_or_budget model=%s tier=%s cap=%s plan=%s "
            "budget_ratio=%.2f -> %s",
            model, tier, cap, self._plan_tier, self._last_budget_ratio, clamped,
        )
        return clamped

    def set_role_map(self, role_map: dict[str, str]) -> None:
        """Pin roles to models the goal's provider serves (deployment_roles)."""
        self._role_map = dict(role_map)

    def set_policy_roles(self, roles: dict[str, str]) -> None:
        """The tenant's own routing-policy role pins: they win over the
        deployment-wide reasoning order (see ModelRouter.set_policy_roles)."""
        self._policy_roles = dict(roles)

    @property
    def role_map(self) -> dict[str, str]:
        return dict(self._role_map)

    def with_override(self, model: str) -> ModelOrchestratorAdapter:
        """Copy-on-write adapter with every reasoning role pinned to *model*.

        Missing before: goal_service calls ``with_override`` for an agent's
        ``model_override`` inside ``suppress(Exception)``, so on this adapter
        (the one goals actually use) every per-agent override was silently
        ignored.
        """
        from copy import copy

        clone = copy(self)
        clone._override = model
        clone._role_map = dict(self._role_map)
        return clone

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
                pattern_config, budget_spent_ratio, vendor=self._vendor
            )
            self._last_budget_ratio = budget_spent_ratio
        except Exception:
            self._cached_assignment = None

    def bind_provider(self, provider: Any) -> None:
        """The goal's provider: a tenant's BYOK provider keeps its own model, and
        the role map / provider default follow what it serves."""
        self._bound_provider = provider

    def model_for(
        self,
        task_type: str,
        *,
        fallback: str = "",
        goal: str = "",
    ) -> str:
        """The model for *task_type* (a routed task type or any known role label).

        Resolves through the ONE reasoning resolver
        (:func:`app.ai_router.resolve.resolve_reasoning`: override > tenant pin >
        saved registry order > ``DEFAULT_<ROLE>_MODEL`` > role map > env default >
        registry > provider default), then applies the plan / budget tier cap.
        With nothing configured: the runtime profile's explicit hint, else
        *fallback*, else ``""`` (the provider reports the honest error).
        """
        del goal
        from app.ai_router.resolve import (
            ModelNotConfiguredError,
            reasoning_task_type,
            resolve_reasoning,
        )

        task = reasoning_task_type(task_type)
        try:
            model = resolve_reasoning(
                task, router=self, provider=getattr(self, "_bound_provider", None)
            ).model
        except ModelNotConfiguredError:
            model = ""
        if model:
            return self._capped(model, task)
        assignment = self._cached_assignment
        if assignment is not None:
            hinted = {
                "planning": assignment.planner,
                "execution": assignment.executor,
                "verification": assignment.verifier,
                "classification": assignment.classifier,
                "judge": assignment.judge,
            }.get(_ROLE_FOR_TASK_BASE.get(task, task), assignment.planner)
            if hinted:
                return hinted
        return fallback

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
        if provider == _UNKNOWN_PROVIDER:
            return  # never attribute an unidentified model's outcome to some provider
        self._orchestrator.record_provider_result(provider, ok=ok, latency_ms=latency_ms)
