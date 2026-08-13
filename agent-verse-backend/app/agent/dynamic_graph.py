"""DynamicGraphAssembler — builds a per-goal LangGraph from PatternConfig."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.pattern_config import PatternConfig
    from app.providers.base import LLMProvider


class DynamicGraphAssembler:
    """Translates PatternConfig into an AgentGraph instance."""

    def assemble(
        self,
        pattern_config: PatternConfig,
        *,
        planner: LLMProvider,
        executor: LLMProvider,
        verifier: LLMProvider,
        **kwargs: Any,
    ) -> Any:
        reasoning = pattern_config.reasoning_patterns if pattern_config else []
        canonical_reasoning = tuple(
            "chain_of_thought" if item == "cot" else item for item in reasoning
        )
        if not canonical_reasoning:
            canonical_reasoning = ("react",)
        from app.orchestration.graph_factory import GraphFactory
        from app.orchestration.runtime_profile import (
            AgentPatternConfig,
            EvalConfig,
            GoalProperties,
            GoalRuntimeProfile,
            MemoryCacheConfig,
            ModelPlanConfig,
            RAGStrategyConfig,
            SecurityConfig,
            StrategySelection,
        )

        profile = GoalRuntimeProfile(
            goal_id="legacy-dynamic-graph",
            tenant_id="legacy",
            properties=GoalProperties(raw_goal="legacy dynamic graph assembly"),
            agent_patterns=AgentPatternConfig(
                reasoning=list(canonical_reasoning),
                multi_agent=list(pattern_config.multi_agent_patterns),
            ),
            rag_strategy=RAGStrategyConfig(
                strategy=(
                    pattern_config.rag_patterns[0]
                    if pattern_config.rag_patterns
                    else "hybrid"
                )
            ),
            model_plan=ModelPlanConfig(
                planner=pattern_config.model_planner,
                executor=pattern_config.model_executor,
                verifier=pattern_config.model_verifier,
                classifier=pattern_config.model_classifier,
            ),
            security=SecurityConfig(),
            memory_cache=MemoryCacheConfig(),
            eval_config=EvalConfig(),
            primary_strategy=StrategySelection(canonical_reasoning[0], "1.0.0"),
            auxiliary_strategies=tuple(
                StrategySelection(item, "1.0.0") for item in canonical_reasoning[1:]
            ),
        )
        services = {
            "planner": planner,
            "executor": executor,
            "verifier": verifier,
            "max_iterations": getattr(pattern_config, "max_iterations", 100),
            "enable_goal_tree": "goal_tree" in pattern_config.multi_agent_patterns,
            **kwargs,
        }
        graph = GraphFactory().create(profile, services)
        graph._pattern_config = pattern_config  # type: ignore[attr-defined]
        return graph

    def get_active_nodes(self, config: PatternConfig) -> list[str]:
        nodes = ["initialize", "rag_prime", "plan", "execute", "verify"]
        if any(p in ("chain_of_thought", "cot") for p in config.reasoning_patterns):
            nodes.append("think")
        if "reflection" in config.reasoning_patterns:
            nodes.append("reflect")
        if "self_refine" in config.reasoning_patterns:
            nodes.append("refine")
        if "debate" in config.multi_agent_patterns:
            nodes.append("debate")
        if "goal_tree" in config.multi_agent_patterns:
            nodes.append("goal_tree_plan")
        if "hitl" in config.safety_patterns:
            nodes.append("hitl_check")
        if len(config.rag_patterns) > 1 or "agentic_rag" in config.rag_patterns:
            nodes.append("rag_remediate")
        return nodes

    def _wire_edges(self, config: PatternConfig) -> dict[str, list[str]]:
        edges: dict[str, list[str]] = {
            "initialize": ["rag_prime"],
            "rag_prime": (
                ["think"]
                if any(
                    p in ("chain_of_thought", "cot") for p in config.reasoning_patterns
                )
                else ["plan"]
            ),
            "think": ["plan"],
            "plan": ["execute"],
            "execute": ["verify"],
            "verify": ["complete", "replan", "max_iter", "waiting_human",
                       "rag_remediate", "reflect"],
            "rag_remediate": ["plan"],
            "reflect": ["plan", "complete"],
        }
        return edges
