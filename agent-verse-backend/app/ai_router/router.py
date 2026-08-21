"""AI Router - select the best model for a given task."""

from __future__ import annotations

import logging

from app.ai_router.models import ModelCapability, ModelEndpoint, RoutingMode, TaskType
from app.ai_router.registry import model_registry

_log = logging.getLogger(__name__)


class AIRouter:
    """Routes requests to the optimal model based on task type, policy, and health."""

    def select_model(
        self,
        task_type: TaskType,
        tenant_id: str,
        *,
        require_vision: bool = False,
        require_tools: bool = False,
        require_structured: bool = False,
        max_cost_per_1k: float | None = None,
        model_override: str | None = None,
    ) -> ModelEndpoint | None:
        """Select the best available model for the given task and constraints."""

        # 1. Model override takes priority
        if model_override:
            parts = model_override.split("/", 1)
            if len(parts) == 2:
                model = model_registry.get_model(parts[0], parts[1])
                if model:
                    return model

        # 2. Check tenant routing policy
        policy = model_registry.get_route_policy(tenant_id, task_type)
        if policy and policy.preferred_provider and policy.preferred_model:
            model = model_registry.get_model(policy.preferred_provider, policy.preferred_model)
            if model and self._meets_constraints(
                model, require_vision, require_tools, max_cost_per_1k
            ):
                return model

        # 3. Filter available models
        candidates = model_registry.list_models()
        candidates = [m for m in candidates if m.is_available]
        candidates = [
            m for m in candidates if not model_registry.get_provider_health(m.provider).circuit_open
        ]

        # Apply task requirements
        if task_type == TaskType.EMBEDDING:
            candidates = [m for m in candidates if ModelCapability.EMBEDDING in m.capabilities]
        elif task_type == TaskType.OCR:
            candidates = [
                m
                for m in candidates
                if ModelCapability.OCR in m.capabilities or ModelCapability.VISION in m.capabilities
            ]
        else:
            candidates = [
                m for m in candidates if ModelCapability.TEXT_GENERATION in m.capabilities
            ]

        if require_vision:
            candidates = [m for m in candidates if m.supports_vision]
        if require_tools:
            candidates = [m for m in candidates if m.supports_tools]
        if require_structured:
            candidates = [m for m in candidates if m.supports_structured_output]
        if max_cost_per_1k is not None:
            candidates = [m for m in candidates if m.cost_per_1k_input <= max_cost_per_1k]

        if not candidates:
            _log.warning(
                "No suitable model found for task=%s, constraints=%s",
                task_type,
                {"vision": require_vision, "tools": require_tools, "max_cost": max_cost_per_1k},
            )
            return None

        # 4. Routing mode selection
        routing_mode = policy.routing_mode if policy else RoutingMode.HIGHEST_QUALITY

        if routing_mode == RoutingMode.CHEAPEST:
            return min(candidates, key=lambda m: m.cost_per_1k_input)
        elif routing_mode == RoutingMode.FASTEST:
            return min(candidates, key=lambda m: m.avg_latency_ms or 1000)
        else:  # HIGHEST_QUALITY (default)
            return max(candidates, key=lambda m: m.quality_score)

    def _meets_constraints(
        self,
        model: ModelEndpoint,
        require_vision: bool,
        require_tools: bool,
        max_cost: float | None,
    ) -> bool:
        if require_vision and not model.supports_vision:
            return False
        if require_tools and not model.supports_tools:
            return False
        if max_cost is not None and model.cost_per_1k_input > max_cost:
            return False
        return True

    def record_call(
        self, provider: str, latency_ms: float, success: bool, error_msg: str = ""
    ) -> None:
        model_registry.update_health(
            provider, latency_ms=latency_ms, error=not success, error_msg=error_msg
        )


# Module-level singleton
ai_router = AIRouter()
