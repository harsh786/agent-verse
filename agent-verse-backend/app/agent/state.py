"""Agent execution state — the typed graph state for LangGraph.

All fields are designed to be serializable for checkpointing.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.tenancy.context import TenantContext


class GoalStatus(enum.StrEnum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    WAITING_HUMAN = "waiting_human"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    """Result of executing a single planned step."""

    step_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class SubGoal:
    """A decomposed sub-goal produced by the goal-tree planner."""

    sub_goal_id: str
    description: str
    parent_goal_id: str
    depends_on: list[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.PLANNING
    result: str = ""
    error: str = ""


@dataclass
class AgentState:
    """Full runtime state of one agent execution, serializable for checkpointing."""

    goal: str
    tenant_ctx: TenantContext

    # Execution bookkeeping
    goal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: GoalStatus = GoalStatus.PLANNING
    iterations: int = 0
    steps: list[StepResult] = field(default_factory=list)

    # Plan produced by the planner LLM
    plan: list[str] = field(default_factory=list)

    # Accumulated context (RAG results, tool outputs, etc.)
    context: dict[str, Any] = field(default_factory=dict)

    # Verifier feedback on the last execution
    verification_feedback: str = ""
    verification_success: bool = False

    # Error information if failed
    error_message: str = ""

    # Sub-goals produced by goal-tree decomposition (empty when not decomposed)
    sub_goals: list[SubGoal] = field(default_factory=list)

    # SSE event stream (not checkpointed — ephemeral)
    events: list[dict[str, Any]] = field(default_factory=list)
