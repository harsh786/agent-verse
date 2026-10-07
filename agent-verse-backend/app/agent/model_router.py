"""Per-goal role router — which model serves each agent role.

Every reasoning role resolves through the ONE reasoning resolver,
:func:`app.ai_router.resolve.resolve_reasoning`: per-agent / per-goal override >
the tenant's routing-policy pin > the Model Registry's saved text-generation
order > ``DEFAULT_<ROLE>_MODEL`` > the deployment role map > the env default
model > the registry's configured models > the provider default (empty registry
only). There are no built-in per-vendor model profiles: a model the deployment
does not serve is never chosen because of a hardcoded slug.

An explicit :class:`ModelRouterConfig` (an agent's own configured model) acts as
that agent's override for the roles it names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai_router.deployment_roles import ROLE_ALIASES as _ROLE_ALIASES
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ModelRouterConfig:
    """An agent's / tenant's explicitly configured role models ("" = not set)."""

    planning_model: str = ""
    execution_model: str = ""
    verification_model: str = ""
    fallback_model: str = ""


# Role → the config field that pins it (then ``fallback_model``).
_CONFIG_FIELDS: dict[str, str] = {
    "planning": "planning_model",
    "reflection": "planning_model",
    "think": "planning_model",
    "thinking": "planning_model",
    "supervisor": "planning_model",
    "execution": "execution_model",
    "classification": "execution_model",
    "verification": "verification_model",
    "judge": "verification_model",
}


class ModelRouter:
    """Routes task types to models through the reasoning resolver."""

    def __init__(
        self,
        provider_name: str = "",
        config: ModelRouterConfig | None = None,
    ) -> None:
        self._provider = (provider_name or "").lower()
        self._config = config or ModelRouterConfig()
        self._override = ""
        self._role_map: dict[str, str] = {}
        self._policy_roles: dict[str, str] = {}
        self._bound_provider: Any = None

    def bind_provider(self, provider: Any) -> None:
        """The goal's provider: a tenant's BYOK provider keeps its own model, and
        the role map / provider default follow what it serves."""
        self._bound_provider = provider

    def set_role_map(self, role_map: dict[str, str]) -> None:
        """Pin roles to models the goal's provider serves (deployment_roles)."""
        self._role_map = dict(role_map)

    def set_policy_roles(self, roles: dict[str, str]) -> None:
        """The tenant's own routing-policy role pins (PUT /models/routing-policies).

        More specific than the deployment-wide reasoning order, so they win over
        it; they also stay in the role map (fallback chains read it)."""
        self._policy_roles = dict(roles)

    @property
    def role_map(self) -> dict[str, str]:
        return dict(self._role_map)

    def _configured(self, task: str) -> str:
        name = _CONFIG_FIELDS.get(task)
        value = str(getattr(self._config, name, "") or "") if name else ""
        return value or str(self._config.fallback_model or "")

    def model_for(self, task_type: str, fallback: str = "") -> str:
        """The model for *task_type* (a routed task type or any known role label).

        ``""`` when nothing is configured anywhere (the provider then reports
        the honest "no LLM configured" error).
        """
        from app.ai_router.resolve import (
            ModelNotConfiguredError,
            reasoning_task_type,
            resolve_reasoning,
        )

        task = reasoning_task_type(task_type)
        pinned = self._override or self._policy_roles.get(_ROLE_ALIASES.get(task, task), "")
        configured = "" if pinned else self._configured(task)
        try:
            model = resolve_reasoning(
                task, override=configured, router=self, provider=self._bound_provider
            ).model
        except ModelNotConfiguredError:
            model = fallback
        if model:
            logger.debug("model_router_selected", task_type=task_type, model=model)
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
            # Downgrade: use execution model for simple planning (cheaper). Through
            # model_for so an override / the role map applies (the raw profile slug
            # could name a model the goal's provider cannot serve).
            cheaper = self.model_for("execution")
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
            fallback_model=model,
        )
        new_router._config = new_config
        new_router._override = model
        return new_router


def get_router_for_tenant(tenant_cfg: dict[str, Any]) -> ModelRouter:
    """A ModelRouter for an agent / tenant LLM config dict.

    Its ``default_model`` (when set) is that agent's own model for every role;
    otherwise every role follows the reasoning resolver.
    """
    provider = str(tenant_cfg.get("provider", "") or "")
    default_model = str(tenant_cfg.get("default_model", "") or "")
    config = ModelRouterConfig(fallback_model=default_model) if default_model else None
    return ModelRouter(provider_name=provider, config=config)
