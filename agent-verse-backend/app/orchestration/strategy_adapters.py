"""Trusted compatibility adapters for existing strategy execution runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.rag.contracts import RAGRuntimeAdapter, RAGStrategy


class ExecutionTier(StrEnum):
    LOCAL = "local"
    RAG = "rag"
    WORKFLOW = "workflow"
    SANDBOX = "sandbox"
    DISTRIBUTED = "distributed"
    CROSS_CUTTING = "cross_cutting"


class StrategyAdapter(Protocol):
    """Minimal factory contract consumed by the strategy runner."""

    @property
    def strategy_id(self) -> str: ...

    @property
    def execution_tier(self) -> ExecutionTier: ...

    def create_runtime(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class AgentGraphCompatibilityAdapter:
    strategy_id: str
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> Any:
        from app.agent.graph import AgentGraph

        return AgentGraph(**kwargs)


@dataclass(frozen=True, slots=True)
class RAGCompatibilityAdapter:
    strategy_id: str
    runtime_adapter: type[RAGRuntimeAdapter]
    execution_tier: ExecutionTier = ExecutionTier.RAG

    def create_runtime(self, **kwargs: Any) -> RAGRuntimeAdapter:
        runtime = self.runtime_adapter(**kwargs)
        if runtime.strategy.value != self.strategy_id:
            raise TypeError(f"RAG adapter strategy mismatch: {self.strategy_id}")
        return runtime


@dataclass(frozen=True, slots=True)
class WorkflowCompatibilityAdapter:
    strategy_id: str = "workflow_dag"
    execution_tier: ExecutionTier = ExecutionTier.WORKFLOW
    supported_apis: tuple[str, ...] = ("execute", "run")

    def create_runtime(self, **kwargs: Any) -> Any:
        from app.agent.workflow_executor import WorkflowExecutor

        return WorkflowExecutor(**kwargs)


AdapterFactory = Callable[[], StrategyAdapter]


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    """Registry-owned factory descriptor; never constructed from request data."""

    descriptor_id: str
    execution_tier: ExecutionTier
    factory: AdapterFactory

    @property
    def is_executable(self) -> bool:
        if not callable(self.factory):
            return False
        try:
            adapter = self.factory()
        except Exception:
            return False
        return adapter.execution_tier is self.execution_tier and callable(
            getattr(adapter, "create_runtime", None)
        )

    def create_adapter(self) -> StrategyAdapter:
        if not callable(self.factory):
            raise TypeError(f"Adapter factory is not callable: {self.descriptor_id}")
        adapter = self.factory()
        if adapter.execution_tier is not self.execution_tier:
            raise TypeError(f"Adapter tier mismatch: {self.descriptor_id}")
        if not callable(adapter.create_runtime):
            raise TypeError(f"Adapter is not executable: {self.descriptor_id}")
        return adapter


def local_agent_graph_descriptor(strategy_id: str) -> AdapterDescriptor:
    return AdapterDescriptor(
        descriptor_id=f"local.agent_graph.{strategy_id}",
        execution_tier=ExecutionTier.LOCAL,
        factory=lambda: AgentGraphCompatibilityAdapter(strategy_id),
    )


def core_execution_descriptor(strategy_id: str) -> AdapterDescriptor:
    if strategy_id == "react":
        from app.agent.patterns.core_execution import ReActStrategyAdapter

        factory: AdapterFactory = ReActStrategyAdapter
    elif strategy_id == "plan_execute":
        from app.agent.patterns.core_execution import PlanExecuteStrategyAdapter

        factory = PlanExecuteStrategyAdapter
    else:
        raise ValueError(f"unknown core strategy: {strategy_id}")
    return AdapterDescriptor(
        descriptor_id=f"local.core_execution.{strategy_id}",
        execution_tier=ExecutionTier.LOCAL,
        factory=factory,
    )


def local_reasoning_descriptor(strategy_id: str) -> AdapterDescriptor:
    from app.agent.patterns.constitutional_ai import ConstitutionalAIAdapter
    from app.agent.patterns.few_shot_cot import FewShotCoTAdapter
    from app.agent.patterns.graph_of_thoughts import GraphOfThoughtsAdapter
    from app.agent.patterns.lats import LATSAdapter
    from app.agent.patterns.least_to_most import LeastToMostAdapter
    from app.agent.patterns.llm_compiler import LLMCompilerAdapter
    from app.agent.patterns.rewoo import ReWOOAdapter

    factories: dict[str, AdapterFactory] = {
        "constitutional_ai": ConstitutionalAIAdapter,
        "few_shot_cot": FewShotCoTAdapter,
        "graph_of_thoughts": GraphOfThoughtsAdapter,
        "least_to_most": LeastToMostAdapter,
        "rewoo": ReWOOAdapter,
        "lats": LATSAdapter,
        "llm_compiler": LLMCompilerAdapter,
    }
    try:
        factory = factories[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown local reasoning strategy: {strategy_id}") from exc
    return AdapterDescriptor(
        descriptor_id=f"local.reasoning.{strategy_id}",
        execution_tier=ExecutionTier.LOCAL,
        factory=factory,
    )


def code_reasoning_descriptor(strategy_id: str) -> AdapterDescriptor:
    from app.agent.patterns.codeact import CodeActAdapter
    from app.agent.patterns.program_of_thought import ProgramOfThoughtAdapter

    factories: dict[str, AdapterFactory] = {
        "codeact": CodeActAdapter,
        "program_of_thought": ProgramOfThoughtAdapter,
    }
    try:
        factory = factories[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown code reasoning strategy: {strategy_id}") from exc
    return AdapterDescriptor(
        descriptor_id=f"sandbox.code_reasoning.{strategy_id}",
        execution_tier=ExecutionTier.SANDBOX,
        factory=factory,
    )


def durable_coordination_descriptor(strategy_id: str) -> AdapterDescriptor:
    from app.agent.patterns.autogpt import AutoGPTAdapter
    from app.agent.patterns.babyagi import BabyAGIAdapter
    from app.agent.patterns.voyager import VoyagerAdapter
    from app.coordination.auction.adapter import MarketAuctionAdapter
    from app.coordination.camel.adapter import CamelAdapter
    from app.coordination.generative.adapter import GenerativeAgentsAdapter
    from app.coordination.group_chat.adapter import GroupChatAdapter
    from app.coordination.magentic.adapter import MagenticAdapter
    from app.coordination.moa.adapter import MoAAdapter
    from app.coordination.patterns.consensus_adapter import DurableConsensusAdapter
    from app.coordination.patterns.debate_adapter import DurableDebateAdapter
    from app.coordination.patterns.goal_tree_adapter import DurableGoalTreeAdapter
    from app.coordination.patterns.supervisor_adapter import DurableSupervisorAdapter
    from app.coordination.swarm.adapter import DecentralizedSwarmAdapter

    factories: dict[str, AdapterFactory] = {
        "autogpt": AutoGPTAdapter,
        "babyagi": BabyAGIAdapter,
        "voyager": VoyagerAdapter,
        "debate": DurableDebateAdapter,
        "camel": CamelAdapter,
        "consensus": lambda: DurableConsensusAdapter(strategy_id="consensus"),
        "consensus_verification": lambda: DurableConsensusAdapter(
            strategy_id="consensus_verification"
        ),
        "goal_tree": DurableGoalTreeAdapter,
        "group_chat": GroupChatAdapter,
        "magentic": MagenticAdapter,
        "mixture_of_agents": MoAAdapter,
        "generative_agents": GenerativeAgentsAdapter,
        "decentralized_swarm": DecentralizedSwarmAdapter,
        "market_auction": MarketAuctionAdapter,
        "supervisor": DurableSupervisorAdapter,
    }
    try:
        factory = factories[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown durable coordination strategy: {strategy_id}") from exc
    return AdapterDescriptor(
        descriptor_id=f"distributed.coordination.{strategy_id}",
        execution_tier=ExecutionTier.DISTRIBUTED,
        factory=factory,
    )


def rag_adapter_descriptor(
    strategy: RAGStrategy,
    runtime_adapter: type[RAGRuntimeAdapter],
) -> AdapterDescriptor:
    return AdapterDescriptor(
        descriptor_id=f"rag.{strategy.value}",
        execution_tier=ExecutionTier.RAG,
        factory=lambda: RAGCompatibilityAdapter(strategy.value, runtime_adapter),
    )


def workflow_adapter_descriptor() -> AdapterDescriptor:
    return AdapterDescriptor(
        descriptor_id="workflow.static_and_dag",
        execution_tier=ExecutionTier.WORKFLOW,
        factory=WorkflowCompatibilityAdapter,
    )


__all__ = [
    "AdapterDescriptor",
    "AgentGraphCompatibilityAdapter",
    "ExecutionTier",
    "RAGCompatibilityAdapter",
    "StrategyAdapter",
    "WorkflowCompatibilityAdapter",
    "code_reasoning_descriptor",
    "core_execution_descriptor",
    "durable_coordination_descriptor",
    "local_agent_graph_descriptor",
    "local_reasoning_descriptor",
    "rag_adapter_descriptor",
    "workflow_adapter_descriptor",
]
