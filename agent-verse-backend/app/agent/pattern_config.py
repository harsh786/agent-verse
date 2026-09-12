"""PatternConfig and GoalProperties — exact doc-4 dataclass contracts.

These live in app/agent/ (not app/orchestration/) because they directly
drive the LangGraph DynamicGraphAssembler and are agent-execution contracts.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Complexity(enum.StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"


class RiskLevel(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(enum.StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    CONVERSATIONAL = "conversational"


@dataclass
class GoalProperties:
    """Classified properties of a goal — drives PatternAssembler decisions."""

    complexity: Complexity = Complexity.MEDIUM
    domain: Domain = Domain.TECHNICAL
    risk: RiskLevel = RiskLevel.LOW
    time_sensitivity: str = "normal"  # realtime | normal | batch
    knowledge_requirement: str = "kb_only"  # none | kb_only | web_required | expert_domain
    reversibility: str = "reversible"  # reversible | irreversible
    multi_step: bool = True
    is_generative: bool = False
    requires_web: bool = False
    estimated_steps: int = 3
    confidence: float = 0.8  # classifier confidence


@dataclass
class PatternConfig:
    """Complete pattern configuration for one goal execution.

    CRITICAL rule: safety_patterns can only be ADDED by the assembler,
    never removed. Optimization rules (reasoning/rag/multi_agent) can
    be added or removed.
    """

    reasoning_patterns: list[str] = field(default_factory=lambda: ["reflection"])
    rag_patterns: list[str] = field(default_factory=lambda: ["hybrid_rag"])
    multi_agent_patterns: list[str] = field(default_factory=lambda: ["single_agent"])
    safety_patterns: list[str] = field(default_factory=lambda: ["guardrails"])
    model_planner: str = "gpt-5.2"
    model_executor: str = "gpt-5.2"
    model_verifier: str = "gpt-5.2"
    model_classifier: str = "gpt-4o-mini"
    max_iterations: int = 6
    max_refine_iterations: int = 2
    persistence_mode: bool = False
    max_persistence_attempts: int = 3
    autonomy_mode: str = "bounded-autonomous"
    web_auto_activate: bool = False
    goal_properties: GoalProperties | None = None
    selection_reason: dict[str, str] = field(default_factory=dict)
    assembly_latency_ms: float = 0.0

    def to_sse_event(self, goal_id: str) -> dict[str, Any]:
        """Emit pattern_assembled SSE event (exact doc-4 shape)."""
        props = self.goal_properties
        return {
            "type": "pattern_assembled",
            "goal_id": goal_id,
            "complexity": props.complexity.value if props else "unknown",
            "risk": props.risk.value if props else "unknown",
            "patterns_active": {
                "reasoning": self.reasoning_patterns,
                "rag": self.rag_patterns,
                "multi_agent": self.multi_agent_patterns,
                "safety": self.safety_patterns,
            },
            "models": {
                "planner": self.model_planner,
                "executor": self.model_executor,
                "verifier": self.model_verifier,
            },
            "selection_reasons": self.selection_reason,
            "assembly_latency_ms": self.assembly_latency_ms,
        }
