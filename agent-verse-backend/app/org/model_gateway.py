"""Model Intelligence Gateway — selects the optimal LLM for each task.

Picks a *profile* by role family (executive/engineering/creative/analytical/…),
quality requirement, cost budget, latency SLO and context length. A profile
names the reasoning ROLE it needs, never a model: the model is resolved from the
Model Registry by :func:`app.ai_router.resolve.resolve_reasoning` (saved order,
env pins, role map, env default, registry), and the rest of that order is the
fallback. Unhealthy models are skipped; nothing configured yields ``""`` (the
provider then reports the honest "no LLM configured" error).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Model profiles
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ModelProfile:
    role: str  # reasoning role label resolved by resolve_reasoning
    cost_tier: str = "standard"  # economy | standard | premium
    min_quality: float = 0.80
    latency_slo: float = 10.0  # seconds — soft SLO
    tools: list[str] = field(default_factory=list)
    context: str = "32k"
    vision: bool = False


MODEL_PROFILES: dict[str, ModelProfile] = {
    "premium": ModelProfile(
        role="planning", cost_tier="premium", min_quality=0.95, latency_slo=30.0
    ),
    "smart": ModelProfile(
        role="planning", cost_tier="standard", min_quality=0.85, latency_slo=15.0
    ),
    "coding": ModelProfile(
        role="execution", cost_tier="standard", min_quality=0.85, latency_slo=15.0
    ),
    "creative": ModelProfile(
        role="synthesis", cost_tier="standard", min_quality=0.80, latency_slo=20.0
    ),
    "analytical": ModelProfile(
        role="verification", cost_tier="standard", min_quality=0.97, latency_slo=20.0
    ),
    "fast": ModelProfile(
        role="classification", cost_tier="economy", min_quality=0.70, latency_slo=2.0
    ),
    "research": ModelProfile(
        role="planning",
        cost_tier="standard",
        tools=["web_search"],
        context="128k",
        min_quality=0.85,
        latency_slo=30.0,
    ),
    "expert": ModelProfile(
        role="planning", cost_tier="premium", min_quality=0.99, latency_slo=60.0
    ),
    "worker": ModelProfile(
        role="classification", cost_tier="economy", min_quality=0.65, latency_slo=5.0
    ),
    # The multimodal profile names no model: its primary and fallback are the
    # Model Registry's vision chain, resolved per selection (resolve_vision).
    "vision": ModelProfile(
        role="vision", cost_tier="premium", vision=True, min_quality=0.88, latency_slo=20.0
    ),
}

def _registry_vision_chain() -> tuple[str, ...]:
    """The Model Registry vision chain (head, then fallbacks), ``()`` when none."""
    from app.ai_router.resolve import ModelNotConfiguredError, resolve_vision

    try:
        res = resolve_vision()
    except ModelNotConfiguredError:
        return ()
    return tuple(m for m in (res.model, *res.fallbacks) if m)


def _candidates(profile: ModelProfile) -> list[str]:
    """The profile's model and its fallbacks, in order (``[]``: nothing configured)."""
    if profile.vision:
        vision = _registry_vision_chain()
        return list(vision)
    from app.ai_router.resolve import ModelNotConfiguredError, resolve_reasoning

    try:
        resolution = resolve_reasoning(profile.role)
    except ModelNotConfiguredError:
        return []
    return [m for m in (resolution.model, *resolution.fallbacks) if m]


def _is_openai(model: str) -> bool:
    from app.ai_router.model_orchestrator import provider_for_model

    return provider_for_model(model) == "openai"


# Cost tier score: higher = cheaper (better score for budget-constrained calls)
_COST_TIER_SCORE: dict[str, float] = {
    "economy": 1.0,
    "standard": 0.7,
    "premium": 0.3,
}

# Rough estimated USD cost per 1k tokens for each cost tier — used both to
# populate ModelSelection.estimated_cost_usd_per_1k and to enforce cost_budget_usd.
_COST_TIER_USD_PER_1K: dict[str, float] = {
    "economy": 0.0006,
    "standard": 0.003,
    "premium": 0.02,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Selection output
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ModelSelection:
    model_id: str
    profile_name: str
    fallback_model: str
    reasoning: str
    estimated_cost_usd_per_1k: float = 0.0
    estimated_latency_s: float = 5.0


# ─────────────────────────────────────────────────────────────────────────────
#  Gateway
# ─────────────────────────────────────────────────────────────────────────────


class ModelGateway:
    """Select the optimal model profile for an LLM call and handle fallback."""

    def __init__(self) -> None:
        self._health: dict[str, bool] = {}  # model_id → available (True by default)

    def _usable(self, profile: ModelProfile, *, has_pii: bool) -> list[str]:
        """The profile's healthy candidate models (PII: non-OpenAI first)."""
        healthy = [m for m in _candidates(profile) if self._health.get(m, True)]
        if has_pii:
            healthy = [m for m in healthy if not _is_openai(m)]
        return healthy

    async def select_model(
        self,
        role_profile: str,
        task_type: str = "general",
        quality_req: float = 0.80,
        latency_budget_ms: int = 30_000,
        cost_budget_usd: float = 1.0,
        context_length: int = 4096,
        has_pii: bool = False,
    ) -> ModelSelection:
        with _tracer.start_as_current_span("model_gateway.select_model") as span:
            span.set_attribute("role_profile", role_profile)
            span.set_attribute("task_type", task_type)
            span.set_attribute("quality_req", quality_req)
            span.set_attribute("latency_budget_ms", latency_budget_ms)
            span.set_attribute("has_pii", has_pii)

            latency_budget_s = latency_budget_ms / 1000.0
            best_profile_name = role_profile if role_profile in MODEL_PROFILES else "smart"
            best_score = -1.0
            best_models: list[str] = []
            skip_reasons: list[str] = []

            vision_models = _registry_vision_chain()
            # A vision (multimodal) request is only ever served by a vision
            # profile — never silently by a text model.
            require_vision = MODEL_PROFILES.get(role_profile, MODEL_PROFILES["smart"]).vision
            if require_vision and not vision_models:
                from app.ai_router.resolve import ModelNotConfiguredError

                raise ModelNotConfiguredError(
                    "vision",
                    "add a vision-capable model in the Model Registry or set "
                    "VISION_MODEL / NVIDIA_VISION_MODEL",
                )
            for name, profile in MODEL_PROFILES.items():
                if require_vision and not profile.vision:
                    continue
                if profile.vision and not vision_models:
                    skip_reasons.append(f"{name}: no vision model is configured")
                    continue
                # Hard constraints
                if quality_req > profile.min_quality + 0.05:
                    skip_reasons.append(
                        f"{name}: quality floor {profile.min_quality:.2f} < req {quality_req:.2f}"
                    )
                    continue
                if latency_budget_s > 0 and profile.latency_slo > latency_budget_s * 1.5:
                    skip_reasons.append(
                        f"{name}: latency SLO {profile.latency_slo}s exceeds budget {latency_budget_s}s"  # noqa: E501
                    )
                    continue
                estimated_cost = _COST_TIER_USD_PER_1K.get(profile.cost_tier, 0.003)
                if estimated_cost > cost_budget_usd:
                    skip_reasons.append(
                        f"{name}: estimated cost ${estimated_cost:.4f}/1k exceeds "
                        f"budget ${cost_budget_usd:.4f}/1k"
                    )
                    continue
                models = self._usable(profile, has_pii=has_pii)
                if not models:
                    skip_reasons.append(f"{name}: no healthy configured model")
                    continue

                score = self._score(profile, quality_req, latency_budget_s, cost_budget_usd)
                if score > best_score:
                    best_score = score
                    best_profile_name = name
                    best_models = models

            profile = MODEL_PROFILES.get(best_profile_name, MODEL_PROFILES["smart"])
            if not best_models:
                best_models = self._usable(profile, has_pii=has_pii)
            model_id = best_models[0] if best_models else ""
            fallback = best_models[1] if len(best_models) > 1 else ""
            reasoning = (
                f"Selected '{best_profile_name}' (score={best_score:.3f}, "
                f"model={model_id or 'unconfigured'}). " + "; ".join(skip_reasons[:2])
            )

            _log.info(
                "model_gateway.selected",
                profile=best_profile_name,
                model=model_id,
                score=round(best_score, 3),
                task_type=task_type,
            )
            span.set_attribute("selected_profile", best_profile_name)
            span.set_attribute("selected_model", model_id)

            return ModelSelection(
                model_id=model_id,
                profile_name=best_profile_name,
                fallback_model=fallback,
                reasoning=reasoning,
                estimated_cost_usd_per_1k=_COST_TIER_USD_PER_1K.get(profile.cost_tier, 0.003),
                estimated_latency_s=profile.latency_slo,
            )

    def _score(
        self,
        profile: ModelProfile,
        quality_req: float,
        latency_budget_s: float,
        cost_budget_usd: float,
    ) -> float:
        q = min(profile.min_quality / max(quality_req, 0.01), 1.0)
        lat = (
            min(latency_budget_s / max(profile.latency_slo, 0.01), 1.0)
            if latency_budget_s > 0
            else 1.0
        )
        cost = _COST_TIER_SCORE.get(profile.cost_tier, 0.5)
        reliability = 1.0

        return 0.40 * q + 0.25 * lat + 0.25 * cost + 0.10 * reliability

    @asynccontextmanager
    async def with_fallback(self, model_id: str) -> AsyncGenerator[str, None]:
        """Context manager: yields model_id, on exception marks unhealthy and re-raises.

        The caller should catch and retry with the fallback obtained from ModelSelection.
        """
        try:
            yield model_id
        except Exception as exc:
            _log.warning(
                "model_gateway.primary_failed",
                model=model_id,
                error=str(exc),
            )
            self._health[model_id] = False
            raise

    async def mark_healthy(self, model_id: str) -> None:
        self._health[model_id] = True

    async def mark_unhealthy(self, model_id: str) -> None:
        self._health[model_id] = False


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton factory
# ─────────────────────────────────────────────────────────────────────────────
_gateway: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    """Return the process-level ModelGateway singleton."""
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway
