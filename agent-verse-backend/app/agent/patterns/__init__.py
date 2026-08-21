"""Agent pattern adapter registry."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState
from app.agent.patterns.consensus import ConsensusPattern
from app.agent.patterns.debate import DebatePattern
from app.agent.patterns.few_shot_cot import FewShotCoTAdapter, FewShotCoTRuntime
from app.agent.patterns.goal_tree import GoalTreePattern
from app.agent.patterns.graph_of_thoughts import GraphOfThoughtsAdapter, GraphOfThoughtsRuntime
from app.agent.patterns.lats import LATSAdapter, LATSRuntime
from app.agent.patterns.least_to_most import LeastToMostAdapter, LeastToMostRuntime
from app.agent.patterns.llm_compiler import LLMCompilerAdapter, LLMCompilerRuntime
from app.agent.patterns.loop_engineering import LoopEngineeringPattern
from app.agent.patterns.peer_review import PeerReviewPattern
from app.agent.patterns.plan_execute import PlanExecutePattern
from app.agent.patterns.react import ReActPattern
from app.agent.patterns.reflection import ReflectionPattern
from app.agent.patterns.reflexion import ReflexionPattern
from app.agent.patterns.rewoo import ReWOOAdapter, ReWOORuntime
from app.agent.patterns.self_consistency import SelfConsistencyPattern
from app.agent.patterns.self_refine import SelfRefinePattern
from app.agent.patterns.supervisor import SupervisorPattern
from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern

ALL_PATTERNS: list[AgentPattern] = [
    ReActPattern(),
    PlanExecutePattern(),
    ReflectionPattern(),
    ReflexionPattern(),
    SelfRefinePattern(),
    SelfConsistencyPattern(),
    TreeOfThoughtsPattern(),
    LoopEngineeringPattern(),
    SupervisorPattern(),
    DebatePattern(),
    GoalTreePattern(),
    ConsensusPattern(),
    PeerReviewPattern(),
]

__all__ = [
    "ALL_PATTERNS",
    "AgentPattern",
    "DebatePattern",
    "FewShotCoTAdapter",
    "FewShotCoTRuntime",
    "GoalTreePattern",
    "GraphOfThoughtsAdapter",
    "GraphOfThoughtsRuntime",
    "LATSAdapter",
    "LATSRuntime",
    "LLMCompilerAdapter",
    "LLMCompilerRuntime",
    "LeastToMostAdapter",
    "LeastToMostRuntime",
    "LoopEngineeringPattern",
    "PatternState",
    "PeerReviewPattern",
    "PlanExecutePattern",
    "ReActPattern",
    "ReWOOAdapter",
    "ReWOORuntime",
    "ReflectionPattern",
    "ReflexionPattern",
    "SelfConsistencyPattern",
    "SelfRefinePattern",
    "SupervisorPattern",
    "TreeOfThoughtsPattern",
]
