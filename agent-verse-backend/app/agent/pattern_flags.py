"""Per-agent reasoning-pattern flags (CoT, reflection, goal tree, debate, ...).

An agent opts into optional graph nodes with these booleans. They are stored on
the agent (``agents.pattern_flags``), snapshotted onto each goal at submission
(``execution_context["agent_pattern_flags"]``) so a Celery worker on another
replica compiles the same graph, and passed to ``AgentGraph`` as keyword args.
"""

from __future__ import annotations

from typing import Any

AGENT_PATTERN_FLAG_KEYS: tuple[str, ...] = (
    "enable_cot",
    "enable_reflection",
    "enable_goal_tree",
    "enable_self_refine",
    "enable_self_consistency",
    "enable_tree_of_thoughts",
    "enable_peer_review",
    "enable_supervisor",
    "enable_debate",
)


def normalize_pattern_flags(raw: Any) -> dict[str, bool]:
    """Every known flag as a bool (unknown keys dropped, missing ones False)."""
    src = raw if isinstance(raw, dict) else {}
    return {k: bool(src.get(k, False)) for k in AGENT_PATTERN_FLAG_KEYS}


def pattern_flags_from_record(record: Any) -> dict[str, bool]:
    """The flags an agent record actually carries (only keys that are present).

    Reads the persisted ``pattern_flags`` mapping when there is one, and the flat
    ``enable_*`` keys (which win, being the most recently merged view) otherwise.
    """
    if not isinstance(record, dict):
        return {}
    flags: dict[str, bool] = {}
    nested = record.get("pattern_flags")
    if isinstance(nested, dict):
        flags.update({k: bool(nested[k]) for k in AGENT_PATTERN_FLAG_KEYS if k in nested})
    flags.update({k: bool(record[k]) for k in AGENT_PATTERN_FLAG_KEYS if k in record})
    return flags
