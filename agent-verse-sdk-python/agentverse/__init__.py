"""AgentVerse Python SDK — public surface area."""

from agentverse.client import AgentVerseClient
from agentverse.exceptions import AgentVerseError, AuthError, GoalFailedError, GoalTimeoutError
from agentverse.models import (
    Agent,
    Connector,
    CoordinationLayerPage,
    CoordinationEvent,
    CoordinationMessagePage,
    Goal,
    GoalEvent,
    GoalStatus,
)
from agentverse.workflows import WorkflowClient, WorkflowRun, WorkflowDefinition

__all__ = [
    # Core client
    "AgentVerseClient",
    # Workflow Automation Engine
    "WorkflowClient",
    "WorkflowRun",
    "WorkflowDefinition",
    # Goals / Agents
    "Goal",
    "GoalEvent",
    "GoalStatus",
    "Agent",
    "Connector",
    "CoordinationLayerPage",
    "CoordinationEvent",
    "CoordinationMessagePage",
    # Exceptions
    "AgentVerseError",
    "AuthError",
    "GoalFailedError",
    "GoalTimeoutError",
]
__version__ = "0.1.0"
