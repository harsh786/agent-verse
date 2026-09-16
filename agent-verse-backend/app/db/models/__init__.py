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
from app.db.models.civilization import (  # noqa: E402
    BlackboardEntry,
    BusMessage,
    Civilization,
    CivilizationAgent,
    CivilizationEvent,
    CivilizationLearning,
    SpawnRequest,
)
from app.db.models.coordination import COORDINATION_TABLES  # noqa: E402
from app.db.models.goal import Goal, GoalCheckpoint, GoalEvent, GoalStep  # noqa: E402
from app.db.models.governance import ApprovalRequest, AuditLog  # noqa: E402
from app.db.models.guardrail_rule import GuardrailRuleRow  # noqa: E402
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
    MemoryConflict,
)
from app.db.models.knowledge_graph import KnowledgeEdge, KnowledgeNode  # noqa: E402
from app.db.models.mcp import MCPCredential, MCPServer, OAuthToken  # noqa: E402
from app.db.models.memory import (  # noqa: E402
    CanonicalMemoryRecord,
    EpisodicMemory,
    MemoryFeedbackRow,
    ProceduralMemory,
)
from app.db.models.mfa import TenantMFA  # noqa: E402
from app.db.models.orchestration import StrategyCertificationEvidence  # noqa: E402
from app.db.models.raft import (  # noqa: E402
    RAFTConfirmationGrant,
    RAFTDataset,
    RAFTFineTuneJob,
)
from app.db.models.routing import RoutingDecisionRow, RoutingOutcomeRow  # noqa: E402
from app.db.models.scheduling import Policy, Schedule  # noqa: E402
from app.db.models.skill import Skill  # noqa: E402
from app.db.models.state_machine import (  # noqa: E402
    StateMachineDefinitionRow,
    StateMachineInstanceRow,
)
from app.db.models.template import GoalTemplate  # noqa: E402
from app.db.models.tenant import ApiKey, Tenant  # noqa: E402
from app.db.models.workflow import Workflow  # noqa: E402

__all__ = [  # noqa: RUF022
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
    "GuardrailRuleRow",
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
    "MemoryConflict",
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
    # workflows
    "Workflow",
    # templates
    "GoalTemplate",
    # state machines (durable registry)
    "StateMachineDefinitionRow",
    "StateMachineInstanceRow",
    # skills (Phase 6)
    "Skill",
    # mfa
    "TenantMFA",
    # knowledge graph
    "KnowledgeNode",
    "KnowledgeEdge",
    # episodic + procedural memory
    "EpisodicMemory",
    "ProceduralMemory",
    "CanonicalMemoryRecord",
    "MemoryFeedbackRow",
    # RAFT
    "RAFTDataset",
    "RAFTFineTuneJob",
    "RAFTConfirmationGrant",
    "StrategyCertificationEvidence",
    "COORDINATION_TABLES",
    "RoutingDecisionRow",
    "RoutingOutcomeRow",
]
