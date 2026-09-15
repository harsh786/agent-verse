"""Multi-model router — selects the optimal model for each task type.

Strategy:
- Planning: Largest/most capable model (best reasoning)
- Execution: Mid-tier model (good enough, cheaper)
- Verification: Fastest/cheapest model (yes/no answer only)
- Embedding: Dedicated embedding model
- Classification: Smallest capable model

Falls back to the tenant's configured default_model when a specific
task-type model is not configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ModelRouterConfig:
    """Per-tenant model routing configuration."""

    planning_model: str = ""
    execution_model: str = ""
    verification_model: str = ""
    embedding_model: str = ""
    fallback_model: str = ""


# Built-in defaults per provider
_PROVIDER_DEFAULTS: dict[str, ModelRouterConfig] = {
    "anthropic": ModelRouterConfig(
        planning_model="claude-opus-4-8",
        execution_model="claude-sonnet-4-5",
        verification_model="claude-haiku-3-5",
        fallback_model="claude-opus-4-8",
    ),
    "openai": ModelRouterConfig(
        planning_model="gpt-5.2",
        execution_model="gpt-4o-mini",
        verification_model="gpt-4o-mini",
        fallback_model="gpt-5.2",
    ),
    "groq": ModelRouterConfig(
        planning_model="llama-3.1-70b-versatile",
        execution_model="llama-3.1-8b-instant",
        verification_model="llama-3.1-8b-instant",
        fallback_model="llama-3.1-70b-versatile",
    ),
    "ollama": ModelRouterConfig(
        planning_model="llama3.2",
        execution_model="llama3.2",
        verification_model="llama3.2",
        fallback_model="llama3.2",
    ),
    # Self-hosted vLLM cluster: capable Qwen for planning/execution, small/fast
    # Gemma for verification, dedicated Qwen embedding. (Model names match the
    # config defaults; override via the env model overrides if you serve others.)
    "onprem": ModelRouterConfig(
        planning_model="Qwen/Qwen3.5-4B",
        execution_model="Qwen/Qwen3.5-4B",
        verification_model="google/gemma-4-E2B",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        fallback_model="Qwen/Qwen3.5-4B",
    ),
    # NVIDIA cloud as the top model for every reasoning role + fallback.
    "nvidia": ModelRouterConfig(
        planning_model="nvidia/llama-3.1-nemotron-70b-instruct",
        execution_model="nvidia/llama-3.1-nemotron-70b-instruct",
        verification_model="nvidia/llama-3.1-nemotron-70b-instruct",
        fallback_model="nvidia/llama-3.1-nemotron-70b-instruct",
    ),
    # Hybrid: NVIDIA (top) for planning + fallback, on-prem Qwen for execution,
    # fast on-prem Gemma for verification, on-prem Qwen for embeddings. Per-role
    # env overrides (DEFAULT_*_MODEL) refine this at boot for the exact model ids.
    "hybrid": ModelRouterConfig(
        planning_model="nvidia/llama-3.1-nemotron-70b-instruct",
        execution_model="Qwen/Qwen3.5-4B",
        verification_model="google/gemma-4-E2B",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        fallback_model="nvidia/llama-3.1-nemotron-70b-instruct",
    ),
}


def _apply_env_model_overrides(base: ModelRouterConfig) -> ModelRouterConfig:
    """Apply env-configured model overrides on top of a provider's default profile.

    Two mechanisms, both non-breaking (they only override when set):

    * **Per-role** ``DEFAULT_PLANNING_MODEL`` / ``DEFAULT_EXECUTION_MODEL`` /
      ``DEFAULT_VERIFICATION_MODEL`` always win when set — explicit operator intent.
    * **Single self-hosted model mode**: when ``OPENAI_BASE_URL`` points at a
      non-official (self-hosted) endpoint, ``OPENAI_MODEL``/``DEFAULT_MODEL`` fills
      every role a per-role var didn't set — so a vLLM/Qwen deployment that serves
      exactly one model never routes to a cloud slug (gpt-5.2, claude-…) it can't serve.

    A cloud deployment (official base_url or none) is unaffected unless it sets the
    explicit per-role vars, so multi-model routing keeps working.
    """
    base_url = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/").lower()
    is_self_hosted = bool(base_url) and base_url != "https://api.openai.com/v1"
    # A single configured model (NVIDIA, self-hosted vLLM, …) should serve every
    # role, so no hardcoded profile slug (gpt-5.2, claude-…) is ever routed to an
    # endpoint that cannot serve it. NVIDIA is included even without OPENAI_BASE_URL.
    is_single_model = is_self_hosted or bool(os.getenv("NVIDIA_API_KEY"))
    single = ""
    if is_single_model:
        single = (
            os.getenv("NVIDIA_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("DEFAULT_MODEL")
            or ""
        )

    overrides = {
        "planning_model": os.getenv("DEFAULT_PLANNING_MODEL") or single,
        "execution_model": os.getenv("DEFAULT_EXECUTION_MODEL") or single,
        "verification_model": os.getenv("DEFAULT_VERIFICATION_MODEL") or single,
        "fallback_model": single,
    }
    applied = {k: v for k, v in overrides.items() if v}
    return replace(base, **applied) if applied else base


class ModelRouter:
    """Routes task types to optimal models for a given provider."""

    def __init__(
        self,
        provider_name: str = "anthropic",
        config: ModelRouterConfig | None = None,
    ) -> None:
        self._provider = provider_name.lower()
        self._config = config or _PROVIDER_DEFAULTS.get(
            self._provider,
            ModelRouterConfig(),
        )
        self._config = _apply_env_model_overrides(self._config)

    # Reasoning roles that route through the generic configured-model registry.
    _REGISTRY_TASKS = frozenset(
        {"planning", "execution", "verification", "classification",
         "reflection", "think", "thinking"}
    )

    def model_for(self, task_type: str, fallback: str = "") -> str:
        """Return the optimal model name for the given task type.

        task_type: "planning" | "execution" | "verification" | "embedding" | "classification"

        Reasoning roles first consult the generic cost-aware model registry
        (cheapest configured model for the capability); when the registry has no
        configured candidate it falls back to the env/provider-profile resolution
        below — so behavior is unchanged until models are registered.
        """
        # Explicit per-role operator intent wins over the cost-aware registry — so a
        # hybrid deployment can pin NVIDIA to planning, Qwen to execution, Gemma to
        # verification (the registry would otherwise pick the cheapest for every role).
        _explicit_env = {
            "planning": "DEFAULT_PLANNING_MODEL",
            "execution": "DEFAULT_EXECUTION_MODEL",
            "verification": "DEFAULT_VERIFICATION_MODEL",
        }.get(task_type)
        if _explicit_env:
            _pinned = (os.getenv(_explicit_env) or "").strip()
            if _pinned:
                return _pinned

        if task_type in self._REGISTRY_TASKS:
            try:
                from app.ai_router.selection import select_configured_model_id

                _choice = select_configured_model_id(task_type)
                if _choice:
                    return _choice
            except Exception:  # pragma: no cover - never block on the registry
                pass
        mapping = {
            "planning": self._config.planning_model,
            "execution": self._config.execution_model,
            "verification": self._config.verification_model,
            "embedding": self._config.embedding_model,
            "classification": self._config.execution_model,  # reuse execution model
            # Reflection and chain-of-thought reasoning need planning-tier quality
            "reflection": self._config.planning_model,
            "think": self._config.planning_model,
            "thinking": self._config.planning_model,
        }
        model = mapping.get(task_type, "")
        if not model:
            model = fallback or self._config.fallback_model
        if model:
            logger.debug(
                "model_router_selected",
                task_type=task_type,
                model=model,
                provider=self._provider,
            )
        return model

    @classmethod
    def from_provider_name(cls, provider_name: str) -> ModelRouter:
        return cls(provider_name=provider_name)

    def complexity_tier(self, goal: str) -> str:
        """Compatibility facade over the canonical orchestration classifier."""
        from app.orchestration.goal_classifier import GoalClassifier
        from app.orchestration.runtime_profile import Complexity

        complexity = GoalClassifier().classify_fast(goal).complexity
        if complexity in {Complexity.COMPLEX, Complexity.EXPERT}:
            return "complex"
        if complexity is Complexity.SIMPLE:
            return "simple"
        return "medium"

    def model_for_goal(self, task_type: str, goal: str = "") -> str:
        """
        Like model_for() but downgrades to a cheaper model for simple goals.
        For planning: simple goals use execution_model instead of planning_model.
        For verification: always uses verification_model regardless of complexity.
        """
        base_model = self.model_for(task_type)
        if not goal or task_type == "verification":
            return base_model
        tier = self.complexity_tier(goal)
        if tier == "simple" and task_type == "planning":
            # Downgrade: use execution model for simple planning (cheaper)
            cheaper = self._config.execution_model
            if cheaper:
                logger.debug(
                    "model_downgraded_simple_goal",
                    original=base_model,
                    downgraded=cheaper,
                    goal_prefix=goal[:50],
                )
                return cheaper
        return base_model

    def with_override(self, model: str) -> "ModelRouter":  # noqa: UP037
        """Return a NEW ModelRouter (copy-on-write) with all task types overridden to `model`.
        The original router is unchanged — prevents per-goal override from leaking across goals.
        """
        from copy import copy

        new_router = copy(self)
        new_config = ModelRouterConfig(
            planning_model=model,
            execution_model=model,
            verification_model=model,
            embedding_model=self._config.embedding_model,  # keep embedding model
            fallback_model=model,
        )
        new_router._config = new_config
        return new_router


def get_router_for_tenant(tenant_cfg: dict[str, Any]) -> ModelRouter:
    """Build a ModelRouter from a tenant's LLM config dict."""
    provider = tenant_cfg.get("provider", "anthropic")
    default_model = tenant_cfg.get("default_model", "")

    base_config = _PROVIDER_DEFAULTS.get(provider, ModelRouterConfig())

    # If tenant specified a model, use it as fallback
    if default_model:
        config = ModelRouterConfig(
            planning_model=base_config.planning_model or default_model,
            execution_model=base_config.execution_model or default_model,
            verification_model=base_config.verification_model or default_model,
            fallback_model=default_model,
        )
    else:
        config = base_config

    return ModelRouter(provider_name=provider, config=config)
