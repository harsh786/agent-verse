"""Model Intelligence Gateway — selects the optimal LLM for each task.

Routes every LLM call by role family (executive/engineering/creative/analytical/…),
quality requirement, cost budget, latency SLO, and context length.
Falls back gracefully when primary models are unavailable.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Model profiles
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelProfile:
    primary: str
    fallback: str = ""
    cost_tier: str = "standard"   # economy | standard | premium
    min_quality: float = 0.80
    latency_slo: float = 10.0     # seconds — soft SLO
    tools: list[str] = field(default_factory=list)
    context: str = "32k"
    vision: bool = False


MODEL_PROFILES: dict[str, ModelProfile] = {
    "premium": ModelProfile(
        primary="claude-opus-4", fallback="gpt-4o",
        cost_tier="premium", min_quality=0.95, latency_slo=30.0,
    ),
    "smart": ModelProfile(
        primary="claude-sonnet-4-5", fallback="gpt-4o",
        cost_tier="standard", min_quality=0.85, latency_slo=15.0,
    ),
    "coding": ModelProfile(
        primary="claude-sonnet-4-5", fallback="gpt-4o",
        cost_tier="standard", min_quality=0.85, latency_slo=15.0,
    ),
    "creative": ModelProfile(
        primary="claude-sonnet-4-5", fallback="gpt-4o",
        cost_tier="standard", min_quality=0.80, latency_slo=20.0,
    ),
    "analytical": ModelProfile(
        primary="gpt-4o", fallback="claude-sonnet-4-5",
        cost_tier="standard", min_quality=0.97, latency_slo=20.0,
    ),
    "fast": ModelProfile(
        primary="gpt-4o-mini", fallback="claude-haiku",
        cost_tier="economy", min_quality=0.70, latency_slo=2.0,
    ),
    "research": ModelProfile(
        primary="claude-sonnet-4-5", fallback="gpt-4o",
        cost_tier="standard", tools=["web_search"],
        context="128k", min_quality=0.85, latency_slo=30.0,
    ),
    "expert": ModelProfile(
        primary="claude-opus-4", fallback="gpt-4o",
        cost_tier="premium", min_quality=0.99, latency_slo=60.0,
    ),
    "worker": ModelProfile(
        primary="gpt-4o-mini", fallback="claude-haiku",
        cost_tier="economy", min_quality=0.65, latency_slo=5.0,
    ),
    "vision": ModelProfile(
        primary="gpt-4o", fallback="claude-opus-4",
        cost_tier="premium", vision=True, min_quality=0.88, latency_slo=20.0,
    ),
}

# Cost tier score: higher = cheaper (better score for budget-constrained calls)
_COST_TIER_SCORE: dict[str, float] = {
    "economy": 1.0,
    "standard": 0.7,
    "premium": 0.3,
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
            skip_reasons: list[str] = []

            for name, profile in MODEL_PROFILES.items():
                # Hard constraints
                if quality_req > profile.min_quality + 0.05:
                    skip_reasons.append(
                        f"{name}: quality floor {profile.min_quality:.2f} < req {quality_req:.2f}"
                    )
                    continue
                if latency_budget_s > 0 and profile.latency_slo > latency_budget_s * 1.5:
                    skip_reasons.append(
                        f"{name}: latency SLO {profile.latency_slo}s exceeds budget {latency_budget_s}s"
                    )
                    continue
                if has_pii and "gpt" in profile.primary.lower():
                    # Prefer Anthropic when PII is involved
                    skip_reasons.append(f"{name}: PII constraint — preferring non-OpenAI")
                    continue
                if not self._health.get(profile.primary, True):
                    skip_reasons.append(f"{name}: primary model marked unhealthy")
                    continue

                score = self._score(profile, quality_req, latency_budget_s, cost_budget_usd)
                if score > best_score:
                    best_score = score
                    best_profile_name = name

            profile = MODEL_PROFILES.get(best_profile_name, MODEL_PROFILES["smart"])
            reasoning = (
                f"Selected '{best_profile_name}' (score={best_score:.3f}, "
                f"model={profile.primary}). "
                + "; ".join(skip_reasons[:2])
            )

            _log.info(
                "model_gateway.selected",
                profile=best_profile_name,
                model=profile.primary,
                score=round(best_score, 3),
                task_type=task_type,
            )
            span.set_attribute("selected_profile", best_profile_name)
            span.set_attribute("selected_model", profile.primary)

            return ModelSelection(
                model_id=profile.primary,
                profile_name=best_profile_name,
                fallback_model=profile.fallback,
                reasoning=reasoning,
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
        lat = min(latency_budget_s / max(profile.latency_slo, 0.01), 1.0) if latency_budget_s > 0 else 1.0
        cost = _COST_TIER_SCORE.get(profile.cost_tier, 0.5)
        reliability = 1.0 if self._health.get(profile.primary, True) else 0.0

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
