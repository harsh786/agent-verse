"""PatternAssembler — assembles PatternConfig from GoalProperties + agent config.

CRITICAL rules: safety_patterns can only ADD, never remove.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel


@dataclass
class Rule:
    condition: Callable[[GoalProperties], bool]
    add_reasoning: list[str] | None = None
    add_rag: list[str] | None = None
    add_multi_agent: list[str] | None = None
    add_safety: list[str] | None = None
    config_overrides: dict[str, Any] | None = None
    reason_key: str = ""
    reason_value: str = ""
    priority: str = "MEDIUM"

    def __post_init__(self) -> None:
        for attr in ("add_reasoning", "add_rag", "add_multi_agent", "add_safety"):
            if getattr(self, attr) is None:
                setattr(self, attr, [])
        if self.config_overrides is None:
            self.config_overrides = {}


_RULES: list[Rule] = [
    # CRITICAL: inviolable safety rules
    Rule(
        condition=lambda p: p.risk == RiskLevel.CRITICAL,
        add_safety=["hitl", "rollback", "guardrails", "consensus_verification"],
        config_overrides={"autonomy_mode": "supervised", "persistence_mode": False},
        reason_key="hitl",
        reason_value="risk=critical — HITL inviolable",
        priority="CRITICAL",
    ),
    Rule(
        condition=lambda p: p.risk == RiskLevel.HIGH,
        add_safety=["hitl", "rollback", "guardrails"],
        config_overrides={"autonomy_mode": "supervised"},
        reason_key="hitl",
        reason_value="risk=high — supervised mode",
        priority="CRITICAL",
    ),
    Rule(
        condition=lambda p: p.reversibility == "irreversible",
        add_safety=["rollback"],
        reason_key="rollback",
        reason_value="irreversible action — rollback required",
        priority="CRITICAL",
    ),
    # HIGH: quality signals
    Rule(
        condition=lambda p: p.complexity == Complexity.EXPERT,
        add_reasoning=["chain_of_thought", "reflection", "self_refine"],
        add_multi_agent=["goal_tree"],
        config_overrides={"max_iterations": 50, "persistence_mode": True, "max_persistence_attempts": 5},
        reason_key="chain_of_thought",
        reason_value="complexity=expert",
        priority="HIGH",
    ),
    Rule(
        condition=lambda p: p.complexity == Complexity.COMPLEX,
        add_reasoning=["chain_of_thought", "reflection"],
        config_overrides={"max_iterations": 25},
        reason_key="chain_of_thought",
        reason_value="complexity=complex",
        priority="HIGH",
    ),
    Rule(
        condition=lambda p: p.multi_step and p.complexity == Complexity.EXPERT,
        add_multi_agent=["goal_tree"],
        reason_key="goal_tree",
        reason_value="expert+multi_step → parallel sub-goals",
        priority="HIGH",
    ),
    # MEDIUM: optimization signals
    Rule(
        condition=lambda p: p.requires_web or p.time_sensitivity == "realtime",
        config_overrides={"web_auto_activate": True},
        add_rag=["web_augmented_rag"],
        reason_key="web_auto_activate",
        reason_value="requires_web=true",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: p.domain == Domain.TECHNICAL and p.complexity != Complexity.SIMPLE,
        add_reasoning=["reflection"],
        reason_key="reflection",
        reason_value="technical domain — reflection improves quality",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: p.complexity in (Complexity.COMPLEX, Complexity.EXPERT),
        add_rag=["agentic_rag"],
        reason_key="agentic_rag",
        reason_value="complex goal — agent-owned retrieval",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: p.is_generative or p.domain == Domain.CREATIVE,
        add_reasoning=["self_refine"],
        reason_key="self_refine",
        reason_value="generative/creative task",
        priority="MEDIUM",
    ),
    Rule(
        condition=lambda p: (
            p.complexity == Complexity.EXPERT
            and p.domain == Domain.ANALYTICAL
            and p.multi_step
        ),
        add_multi_agent=["supervisor"],
        reason_key="supervisor",
        reason_value="expert analytical multi-step → supervisor",
        priority="MEDIUM",
    ),
    # LOW: defaults
    Rule(
        condition=lambda p: True,
        add_reasoning=["react"],
        add_safety=["guardrails"],
        reason_key="react",
        reason_value="default reasoning loop",
        priority="LOW",
    ),
]


class PatternAssembler:
    """Assembles PatternConfig by applying ordered rules to GoalProperties."""

    def assemble(self, props: GoalProperties, agent_config: dict[str, Any]) -> PatternConfig:
        t0 = time.perf_counter()
        reasoning: list[str] = []
        rag: list[str] = ["hybrid_rag"]
        multi_agent: list[str] = ["single_agent"]
        safety: list[str] = []
        config_overrides: dict[str, Any] = {}
        reasons: dict[str, str] = {}
        critical_safety: set[str] = set()

        for rule in _RULES:
            if not rule.condition(props):
                continue
            for p in rule.add_reasoning:
                if p not in reasoning:
                    reasoning.append(p)
                    reasons[p] = rule.reason_value
            for p in rule.add_rag:
                if p not in rag:
                    rag.append(p)
            for p in rule.add_multi_agent:
                if p == "goal_tree" and "single_agent" in multi_agent:
                    multi_agent.remove("single_agent")
                if p not in multi_agent:
                    multi_agent.append(p)
                reasons[p] = rule.reason_value
            for p in rule.add_safety:
                if p not in safety:
                    safety.append(p)
                    if rule.priority == "CRITICAL":
                        critical_safety.add(p)
                    reasons[p] = rule.reason_value
            config_overrides.update(rule.config_overrides)

        # Agent config additions (non-safety only — CRITICAL safety cannot be overridden)
        if agent_config.get("enable_cot") and "chain_of_thought" not in reasoning:
            reasoning.append("chain_of_thought")
            reasons["chain_of_thought"] = "agent_config.enable_cot=true"
        if agent_config.get("enable_reflection") and "reflection" not in reasoning:
            reasoning.append("reflection")
        if agent_config.get("enable_goal_tree") and "goal_tree" not in multi_agent:
            multi_agent.append("goal_tree")
        # NOTE: force_no_hitl is intentionally IGNORED — CRITICAL rules win

        if "react" not in reasoning:
            reasoning.insert(0, "react")
        elif reasoning[0] != "react":
            reasoning.remove("react")
            reasoning.insert(0, "react")

        latency_ms = (time.perf_counter() - t0) * 1000
        return PatternConfig(
            reasoning_patterns=reasoning,
            rag_patterns=rag,
            multi_agent_patterns=multi_agent,
            safety_patterns=safety,
            model_planner=config_overrides.get("model_planner", "gpt-5.2"),
            model_executor=config_overrides.get("model_executor", "gpt-5.2"),
            model_verifier=config_overrides.get("model_verifier", "gpt-5.2"),
            model_classifier="gpt-4o-mini",
            max_iterations=config_overrides.get("max_iterations", 15),
            max_refine_iterations=config_overrides.get("max_refine_iterations", 2),
            persistence_mode=config_overrides.get("persistence_mode", False),
            max_persistence_attempts=config_overrides.get("max_persistence_attempts", 3),
            autonomy_mode=config_overrides.get("autonomy_mode", "bounded-autonomous"),
            web_auto_activate=config_overrides.get("web_auto_activate", False),
            goal_properties=props,
            selection_reason=reasons,
            assembly_latency_ms=latency_ms,
        )


# Module-level singleton
pattern_assembler = PatternAssembler()
