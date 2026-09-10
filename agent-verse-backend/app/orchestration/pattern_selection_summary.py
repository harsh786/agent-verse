"""Pattern-selection summary — the always-on, human-readable record of which
agent pattern a goal was routed to and *why*.

The full :class:`~app.orchestration.runtime_profile.GoalRuntimeProfile` is only
assembled when the ``dynamic_orchestration`` master flag is on. Pattern
*selection*, however, is cheap (a fast heuristic classify + pure scoring) and
side-effect free, so it always runs: every goal records which pattern the ONE
:class:`~app.orchestration.pattern_selector.PatternSelector` chose, in plain
language, for observability and for the frontend selection surface.

This module owns no selection rules of its own — it composes the same
``GoalClassifier`` + ``PatternSelector`` + ``StrategyRegistry`` used by the
runtime-profile builder, so the summary can never diverge from what actually
runs. Adding a pattern = registering it; it then flows through here for free.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from app.orchestration.goal_classifier import GoalClassifier
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import KnowledgeState
from app.orchestration.strategy_registry import (
    StrategyCategory,
    StrategyRegistry,
    get_strategy_registry,
)

_PLAIN_NAMES: dict[str, str] = {
    "react": "ReAct",
    "plan_execute": "Plan-and-Execute",
    "chain_of_thought": "Chain of Thought",
    "few_shot_cot": "Few-shot Chain of Thought",
    "reflection": "Reflection",
    "reflexion": "Reflexion",
    "self_refine": "Self-Refine",
    "self_consistency": "Self-Consistency",
    "tree_of_thoughts": "Tree of Thoughts",
    "goal_tree": "Goal Tree",
    "supervisor": "Supervisor",
    "debate": "Debate",
    "consensus": "Consensus",
    "peer_review": "Peer Review",
    "single_agent": "Single Agent",
}


def humanize(pattern_id: str) -> str:
    """Best-effort plain-language name for a pattern id."""
    return _PLAIN_NAMES.get(pattern_id) or pattern_id.replace("_", " ").title()


def _available_agent_patterns(registry: StrategyRegistry) -> list[dict[str, Any]]:
    """The registry's agent-pattern catalog, so a picker is driven by ONE registry."""
    catalog: list[dict[str, Any]] = []
    for cap in sorted(
        registry.list_by_category(StrategyCategory.AGENT),
        key=lambda c: c.strategy_id,
    ):
        catalog.append(
            {
                "id": cap.strategy_id,
                "name": humanize(cap.strategy_id),
                "description": cap.description,
                "state": cap.state.value,
                "available": registry.is_available(cap.strategy_id),
                "cost_class": cap.cost_class,
                "latency_class": cap.latency_class,
            }
        )
    return catalog


def summarize_pattern_selection(
    goal: str,
    *,
    goal_id: str = "",
    tenant_id: str = "",
    agent_config: dict[str, Any] | None = None,
    kb_state: str = "unknown",
    registry: StrategyRegistry | None = None,
) -> dict[str, Any]:
    """Return a serializable, plain-language summary of the goal's pattern choice.

    ``agent_config`` may carry an explicit ``primary_strategy`` override — an
    explicit choice always wins over the auto-selection and is labelled as such.
    """
    reg = registry or get_strategy_registry()
    config = agent_config or {}

    classifier = GoalClassifier()
    props = classifier.classify_fast(goal)
    with suppress(ValueError):
        props.kb_state = KnowledgeState(kb_state)

    selector = PatternSelector(registry=reg)
    agent_cfg = selector.select_agent_patterns(props)

    override = config.get("primary_strategy")
    auto_primary = agent_cfg.reasoning[0] if agent_cfg.reasoning else "react"
    primary = str(override) if override else auto_primary
    source = "override" if override else "auto"

    # Whether the advanced/autonomous multi-agent tier may actually *run*
    # (default-off safety gate). Selection is still recorded either way.
    advanced_tier_enabled = False
    with suppress(Exception):
        from app.core.config import get_settings

        advanced_tier_enabled = bool(get_settings().agent_auto_multi_agent_enabled)

    rationale: list[dict[str, str]] = []
    for pattern in agent_cfg.reasoning:
        rationale.append(
            {
                "pattern": pattern,
                "name": humanize(pattern),
                "category": "reasoning",
                "why": agent_cfg.selection_reasons.get(pattern, "selected"),
            }
        )
    for pattern in agent_cfg.multi_agent:
        rationale.append(
            {
                "pattern": pattern,
                "name": humanize(pattern),
                "category": "multi_agent",
                "why": agent_cfg.selection_reasons.get(pattern, "selected"),
            }
        )

    return {
        "goal_id": goal_id,
        "tenant_id": tenant_id,
        "source": source,
        "override": str(override) if override else None,
        "primary_pattern": primary,
        "primary_pattern_name": humanize(primary),
        "reasoning_patterns": list(agent_cfg.reasoning),
        "multi_agent_patterns": list(agent_cfg.multi_agent),
        "safety_patterns": list(agent_cfg.safety),
        "autonomy_mode": agent_cfg.autonomy_mode,
        "max_iterations": agent_cfg.max_iterations,
        "advanced_tier_enabled": advanced_tier_enabled,
        "advanced_tier_gated": not advanced_tier_enabled,
        "goal_properties": {
            "complexity": props.complexity.value,
            "domain": props.domain.value,
            "risk": props.risk.value,
            "multi_step": bool(getattr(props, "multi_step", True)),
            "requires_code": bool(getattr(props, "requires_code", False)),
            "classifier_confidence": round(float(props.classifier_confidence), 3),
        },
        "rationale": rationale,
        "available_patterns": _available_agent_patterns(reg),
    }
