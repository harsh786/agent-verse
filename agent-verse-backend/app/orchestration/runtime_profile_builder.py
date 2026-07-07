"""RuntimeProfileBuilder — assembles GoalRuntimeProfile for a goal."""
from __future__ import annotations

import time
from typing import Any

from app.orchestration.decision_trace import DecisionTrace
from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.orchestration.strategy_registry import StrategyRegistry, build_default_registry


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
        self._provider = llm_provider

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

        try:
            props.kb_state = KnowledgeState(kb_state)  # type: ignore[attr-defined]
        except ValueError:
            pass

        agent_cfg = self._selector.select_agent_patterns(props)
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
            "",
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
            assembly_latency_ms=total_ms,
        )
        return profile, trace
