"""RuntimeProfileBuilder — assembles GoalRuntimeProfile for a goal."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import suppress
from typing import Any

from app.orchestration.compatibility import CompatibilityEvaluator
from app.orchestration.decision_trace import DecisionTrace
from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile,
    StrategyRejection,
    default_pattern_limits,
)
from app.orchestration.strategy_contracts import PatternLimits
from app.orchestration.strategy_registry import StrategyRegistry, build_default_registry


class InvalidStrategyOverrideError(ValueError):
    """An explicit strategy request cannot be resolved or admitted."""


class RuntimeProfileBuilder:
    def __init__(
        self,
        *,
        registry: StrategyRegistry | None = None,
        llm_provider: Any = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._classifier = GoalClassifier()
        self._selector = PatternSelector(registry=self._registry)
        self._compatibility = CompatibilityEvaluator(self._registry)
        self._provider = llm_provider
        revision_payload = [
            {
                "id": capability.strategy_id,
                "version": capability.adapter_version,
                "schema": capability.state_schema_version,
                "state": capability.state.value,
            }
            for capability in sorted(self._registry.list_all(), key=lambda item: item.strategy_id)
        ]
        digest = hashlib.sha256(
            json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._registry_revision = f"sha256:{digest}"

    @staticmethod
    def _effective_limits(config: dict[str, Any]) -> tuple[PatternLimits, PatternLimits | None]:
        defaults = default_pattern_limits()
        requested = PatternLimits.model_validate(config.get("limits") or defaults.model_dump())
        ceiling_payload = config.get("tenant_limit_ceiling")
        if ceiling_payload is None:
            return requested, None
        ceiling = PatternLimits.model_validate(ceiling_payload)
        effective = PatternLimits.model_validate(
            {
                field_name: min(getattr(requested, field_name), getattr(ceiling, field_name))
                for field_name in PatternLimits.model_fields
            }
        )
        return effective, ceiling

    async def build(
        self,
        goal: str,
        *,
        tenant_id: str,
        goal_id: str,
        kb_state: str = "unknown",
        agent_config: dict[str, Any] | None = None,
    ) -> GoalRuntimeProfile:
        profile, _ = await self.build_with_trace(
            goal,
            tenant_id=tenant_id,
            goal_id=goal_id,
            kb_state=kb_state,
            agent_config=agent_config,
        )
        return profile

    async def build_with_trace(
        self,
        goal: str,
        *,
        tenant_id: str,
        goal_id: str,
        kb_state: str = "unknown",
        agent_config: dict[str, Any] | None = None,
    ) -> tuple[GoalRuntimeProfile, DecisionTrace]:
        t0 = time.perf_counter()
        trace = DecisionTrace(goal_id=goal_id, tenant_id=tenant_id)

        t_cls = time.perf_counter()
        props = self._classifier.classify_fast(goal)
        cls_ms = (time.perf_counter() - t_cls) * 1000
        trace.add(
            "GoalClassifier",
            "properties",
            props.complexity.value,
            f"fast classification: confidence={props.classifier_confidence:.2f}",
            latency_ms=cls_ms,
        )

        from app.orchestration.runtime_profile import KnowledgeState

        with suppress(ValueError):
            props.kb_state = KnowledgeState(kb_state)

        config = agent_config or {}
        agent_cfg = self._selector.select_agent_patterns(props)
        if "max_iterations" in config:
            agent_cfg.max_iterations = max(1, int(config["max_iterations"]))
        if "persistence_mode" in config:
            agent_cfg.persistence_mode = bool(config["persistence_mode"])
        if "max_persistence_attempts" in config:
            agent_cfg.max_persistence_attempts = max(1, int(config["max_persistence_attempts"]))
        trace.add(
            "PatternSelector",
            "agent_patterns",
            agent_cfg.reasoning,
            str(agent_cfg.selection_reasons),
        )

        rag_cfg = self._selector.select_rag_strategy(props)
        trace.add(
            "PatternSelector",
            "rag_strategy",
            rag_cfg.strategy,
            f"sources={rag_cfg.sources}",
        )

        model_cfg = self._selector.select_model_plan(props)
        trace.add(
            "PatternSelector",
            "model_plan",
            model_cfg.cost_class,
            f"latency={model_cfg.latency_class}",
        )

        security_cfg = self._selector.select_security_profile(props)
        trace.add(
            "PatternSelector",
            "security",
            f"hitl={security_cfg.hitl_required}",
            f"risk={props.risk.value} audit={security_cfg.audit_level}",
        )

        mem_cfg = self._selector.select_memory_cache_policy(props)
        trace.add(
            "PatternSelector",
            "memory_cache",
            f"ltm={mem_cfg.use_long_term_memory}",
            f"kg={mem_cfg.use_knowledge_graph} reflexion={mem_cfg.reflexion_enabled}",
        )

        eval_cfg = self._selector.select_eval_config(props)
        trace.add(
            "PatternSelector",
            "eval_config",
            eval_cfg.eval_suite,
            f"threshold={eval_cfg.score_threshold}",
        )

        total_ms = (time.perf_counter() - t0) * 1000
        trace.total_latency_ms = total_ms

        explicit_primary = config.get("primary_strategy")
        requested_primary = str(explicit_primary or agent_cfg.reasoning[0])
        try:
            resolution = self._registry.resolve(requested_primary)
        except LookupError as exc:
            raise InvalidStrategyOverrideError(
                f"unknown primary strategy: {requested_primary}"
            ) from exc
        if explicit_primary and resolution.capability.adapter_descriptor is None:
            raise InvalidStrategyOverrideError(
                f"primary strategy is not executable: {requested_primary}"
            )
        trace.add(
            "StrategyRegistry",
            "primary_strategy_resolution",
            {
                "requested_id": resolution.requested_id,
                "resolved_id": resolution.canonical_id,
                "configuration": (
                    {"mode": "zero_shot"} if resolution.requested_id == "zero_shot_cot" else {}
                ),
            },
            "canonical alias resolution"
            if resolution.was_aliased
            else "canonical strategy selected",
        )

        ready_config = config.get("ready_strategy_ids")
        if ready_config is None:
            ready_ids = frozenset(
                capability.strategy_id
                for capability in self._registry.list_all()
                if capability.adapter_descriptor is not None
            )
        else:
            ready_ids = frozenset(str(item) for item in ready_config)
        if explicit_primary and resolution.canonical_id not in ready_ids:
            raise InvalidStrategyOverrideError(
                f"primary strategy is not ready: {requested_primary}"
            )

        auxiliary_candidates = list(agent_cfg.reasoning[1:])
        auxiliary_candidates.extend(str(item) for item in config.get("auxiliary_strategies", ()))
        pre_rejections: list[StrategyRejection] = []
        if "peer_review" in auxiliary_candidates:
            reviewer_identity = str(config.get("reviewer_model", ""))
            executor_identity = str(config.get("executor_model", model_cfg.executor))
            if not reviewer_identity or reviewer_identity == executor_identity:
                auxiliary_candidates = [
                    item for item in auxiliary_candidates if item != "peer_review"
                ]
                pre_rejections.append(StrategyRejection("peer_review", "reviewer_not_independent"))
        auxiliary_ids = tuple(dict.fromkeys(auxiliary_candidates))
        compatibility = self._compatibility.compose(
            primary_id=requested_primary,
            candidate_auxiliary_ids=auxiliary_ids,
            ready_ids=ready_ids,
            sandbox_ready=bool(config.get("sandbox_ready", False)),
            coordination_ready=bool(config.get("coordination_ready", False)),
        )
        rejections = [*pre_rejections, *compatibility.rejected]
        if not explicit_primary and compatibility.primary_rejection is not None:
            rejections.insert(
                0,
                StrategyRejection(
                    compatibility.primary.strategy_id,
                    f"{compatibility.primary_rejection}_fallback",
                ),
            )
        effective_limits, tenant_ceiling = self._effective_limits(config)
        profile_identity = "|".join(
            (
                tenant_id,
                goal_id,
                self._registry_revision,
                compatibility.primary.strategy_id,
                *(item.strategy_id for item in compatibility.accepted),
            )
        )
        profile_id = uuid.uuid5(uuid.NAMESPACE_URL, profile_identity).hex
        readiness_digest = hashlib.sha256("\n".join(sorted(ready_ids)).encode()).hexdigest()

        profile = GoalRuntimeProfile(
            goal_id=goal_id,
            tenant_id=tenant_id,
            properties=props,
            agent_patterns=agent_cfg,
            rag_strategy=rag_cfg,
            model_plan=model_cfg,
            security=security_cfg,
            memory_cache=mem_cfg,
            eval_config=eval_cfg,
            profile_id=profile_id,
            assembly_latency_ms=total_ms,
            registry_revision=self._registry_revision,
            primary_strategy=compatibility.primary,
            auxiliary_strategies=compatibility.accepted,
            execution_tier=compatibility.execution_tier,
            effective_limits=effective_limits,
            tenant_limit_ceiling=tenant_ceiling,
            readiness_snapshot_ref=f"sha256:{readiness_digest}",
            policy_snapshot_ref=str(config.get("policy_snapshot_ref", "policy:default")),
            budget_snapshot_ref=str(config.get("budget_snapshot_ref", "budget:default")),
            selected_alternatives=(compatibility.primary, *compatibility.accepted),
            rejected_alternatives=tuple(rejections),
            model_role_assignments=tuple(
                (
                    ("planner", model_cfg.planner),
                    ("executor", str(config.get("executor_model", model_cfg.executor))),
                    ("verifier", model_cfg.verifier),
                    ("embedder", model_cfg.embedder),
                    ("classifier", model_cfg.classifier),
                )
                + (
                    (("reviewer", str(config["reviewer_model"])),)
                    if config.get("reviewer_model")
                    else ()
                )
            ),
        )
        # Emit orchestration profile counters
        try:
            from app.observability.metrics import (
                orchestration_pattern_selected_total,
                orchestration_profile_built_total,
                orchestration_rag_strategy_total,
            )

            orchestration_profile_built_total.labels(
                complexity=props.complexity.value,
                risk=props.risk.value,
                tenant_plan="unknown",
            ).inc()
            orchestration_rag_strategy_total.labels(
                strategy=rag_cfg.strategy,
            ).inc()
            for _pattern in agent_cfg.reasoning:
                orchestration_pattern_selected_total.labels(
                    pattern_id=_pattern,
                    category="reasoning",
                ).inc()
        except Exception:
            pass
        return profile, trace
