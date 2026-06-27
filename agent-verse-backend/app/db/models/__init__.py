"""SQLAlchemy declarative base shared across all models.

All domain models are re-exported here so consumers can do:
    from app.db.models import Agent, Goal, Tenant, ...
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Re-export all ORM models.  The imports must come AFTER Base is defined so that
# each model file can successfully import Base from this (partially-loaded) module.
from app.db.models.agent import Agent, AgentPermission  # noqa: E402
from app.db.models.goal import Goal, GoalCheckpoint, GoalEvent, GoalStep  # noqa: E402
from app.db.models.governance import ApprovalRequest, AuditLog  # noqa: E402
from app.db.models.intelligence import (  # noqa: E402
    AgentTemplate,
    CollabOperation,
    CollabSession,
    CostLedger,
    DecisionTrace,
    Evaluation,
)
from app.db.models.knowledge import (  # noqa: E402
    Document,
    ExecutionMemory,
    KnowledgeCollection,
    LongTermMemory,
)
from app.db.models.mcp import MCPCredential, MCPServer, OAuthToken  # noqa: E402
from app.db.models.scheduling import Policy, Schedule  # noqa: E402
from app.db.models.tenant import ApiKey, Tenant  # noqa: E402
from app.db.models.civilization import (  # noqa: E402
    Civilization,
    CivilizationAgent,
    SpawnRequest,
    BlackboardEntry,
    BusMessage,
    CivilizationLearning,
    CivilizationEvent,
)

__all__ = [
    "Base",
    # tenancy
    "Tenant",
    "ApiKey",
    # agent
    "Agent",
    "AgentPermission",
    # goals
    "Goal",
    "GoalStep",
    "GoalEvent",
    "GoalCheckpoint",
    # governance
    "AuditLog",
    "ApprovalRequest",
    # mcp
    "MCPServer",
    "MCPCredential",
    "OAuthToken",
    # scheduling
    "Policy",
    "Schedule",
    # knowledge
    "KnowledgeCollection",
    "Document",
    "ExecutionMemory",
    "LongTermMemory",
    # intelligence
    "DecisionTrace",
    "Evaluation",
    "CostLedger",
    "CollabSession",
    "CollabOperation",
    "AgentTemplate",
    # civilization
    "Civilization",
    "CivilizationAgent",
    "SpawnRequest",
    "BlackboardEntry",
    "BusMessage",
    "CivilizationLearning",
    "CivilizationEvent",
]
