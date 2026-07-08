"""ModelOrchestrator — per-role model selection with budget-ratio downgrade.

STATUS: Implemented but not wired into graph.py production path.
The graph uses ModelRouter (app/agent/model_router.py) for model selection.

TODO (Phase 6): Wire ModelOrchestrator into goal_service.py graph construction
as a replacement for the simpler ModelRouter, enabling:
- Budget-ratio downgrade for high-cost goals
- Provider failover across OpenAI/Anthropic/Groq
- Role-specific quality/cost tradeoffs

Until then, AIRouter (app/ai_router/router.py) handles tenant-level
model policy (health, quotas) separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.ai_router.cost_latency_quality_policy import CostLatencyQualityPolicy
from app.ai_router.provider_health_policy import ProviderHealthPolicy
from app.ai_router.role_policy import AgentRole, RolePolicy

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
    "claude-3-5-sonnet": "anthropic",
    "claude-3-haiku": "anthropic",
    "gemini-2.5-pro": "google",
    "text-embedding-3-large": "openai",
    "text-embedding-3-small": "openai",
    "voyage-3-lite": "voyage",
}

_FALLBACK_MODELS: dict[str, str] = {
    "openai": "claude-3-5-sonnet",
    "anthropic": "gpt-4o",
    "google": "gpt-4o",
}

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
        config: "PatternConfig",
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
        )

    def select_for_content_type(self, content_type: "ContentType") -> MultimodalModelAssignment:
        modality = _CONTENT_TYPE_MODALITY.get(content_type.value, "text")
        spec = _MULTIMODAL_MODELS.get(modality, _MULTIMODAL_MODELS["text"])
        return MultimodalModelAssignment(
            modality=modality,
            extractor_model=self._with_failover(spec["extractor"]),
            reasoner_model=self._with_failover(spec["reasoner"]),
            requires_vision=spec.get("requires_vision", False),
            requires_audio=modality == "audio",
        )

    def _with_failover(self, model: str) -> str:
        provider = _MODEL_PROVIDER.get(model, "openai")
        if not self._health_policy.check(provider).circuit_open:
            return model
        fallback_provider = _FALLBACK_MODELS.get(provider, "openai")
        if not self._health_policy.check(fallback_provider).circuit_open:
            for m, p in _MODEL_PROVIDER.items():
                if p == fallback_provider and "embedding" not in m and "mini" not in m:
                    return m
        return "gpt-4o-mini"


class ModelOrchestratorAdapter:
    """Adapter that wraps ModelOrchestrator to implement the model_for() interface
    expected by graph.py, with lazy PatternConfig resolution from agent state context.

    This allows ModelOrchestrator to be used as a drop-in replacement for ModelRouter
    without changing any graph.py call sites.
    """

    def __init__(
        self,
        orchestrator: "ModelOrchestrator | None" = None,
        *,
        default_tier: str = "medium",
    ) -> None:
        self._orchestrator = orchestrator or ModelOrchestrator()
        self._default_tier = default_tier
        self._cached_assignment: "ModelRoleAssignment | None" = None
        self._last_budget_ratio: float = 0.0

    def update_from_profile(
        self,
        runtime_profile: Any,
        budget_spent_ratio: float = 0.0,
    ) -> None:
        """Update model assignment from a GoalRuntimeProfile.
        Called by _node_plan when dynamic orchestration is active."""
        try:
            from app.agent.pattern_config import PatternConfig, GoalProperties  # noqa: F401
            pattern_config = PatternConfig(
                goal_properties=runtime_profile.properties,
                model_planner=getattr(runtime_profile.model_plan, "planner", "") or "",
                model_executor=getattr(runtime_profile.model_plan, "executor", "") or "",
                model_verifier=getattr(runtime_profile.model_plan, "verifier", "") or "",
                model_classifier="",
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
        """Return the best model for a task type, using orchestrator's tier selection."""
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
