"""Durable adapters for established multi-agent patterns."""

from app.coordination.patterns.debate_adapter import DurableDebateAdapter
from app.coordination.patterns.goal_tree_adapter import DurableGoalTreeAdapter
from app.coordination.patterns.supervisor_adapter import DurableSupervisorAdapter

__all__ = ["DurableDebateAdapter", "DurableGoalTreeAdapter", "DurableSupervisorAdapter"]
