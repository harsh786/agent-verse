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

from dataclasses import dataclass
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
        planning_model="gpt-4o",
        execution_model="gpt-4o-mini",
        verification_model="gpt-4o-mini",
        fallback_model="gpt-4o",
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
}


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

    def model_for(self, task_type: str, fallback: str = "") -> str:
        """Return the optimal model name for the given task type.

        task_type: "planning" | "execution" | "verification" | "embedding" | "classification"
        """
        mapping = {
            "planning": self._config.planning_model,
            "execution": self._config.execution_model,
            "verification": self._config.verification_model,
            "embedding": self._config.embedding_model,
            "classification": self._config.execution_model,  # reuse execution model
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
