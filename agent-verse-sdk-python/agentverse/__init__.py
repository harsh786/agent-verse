"""AgentVerse Python SDK — public surface area."""

from agentverse.client import AgentVerseClient
from agentverse.exceptions import AgentVerseError, AuthError, GoalFailedError, GoalTimeoutError
from agentverse.models import Agent, Connector, Goal, GoalEvent, GoalStatus

__all__ = [
    "AgentVerseClient",
    "Goal",
    "GoalEvent",
    "GoalStatus",
    "Agent",
    "Connector",
    "AgentVerseError",
    "AuthError",
    "GoalFailedError",
    "GoalTimeoutError",
]
__version__ = "0.1.0"
