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
        config: "PatternConfig",
        *,
        planner: "LLMProvider",
        executor: "LLMProvider",
        verifier: "LLMProvider",
        **kwargs: Any,
    ) -> Any:
        from app.agent.graph import AgentGraph

        graph = AgentGraph(
            planner=planner,
            executor=executor,
            verifier=verifier,
            max_iterations=config.max_iterations,
            **kwargs,
        )
        graph._pattern_config = config  # type: ignore[attr-defined]
        return graph

    def get_active_nodes(self, config: "PatternConfig") -> list[str]:
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

    def _wire_edges(self, config: "PatternConfig") -> dict[str, list[str]]:
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
