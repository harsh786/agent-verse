"""Validated state contracts shared by bounded local-reasoning adapters."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class ReasoningContractError(ValueError):
    """Raised before execution when a reasoning graph is not safe to run."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ReasoningPhase(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    EXECUTING = "executing"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_EXCEEDED = "limit_exceeded"


class ReasoningExample(_FrozenModel):
    example_id: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    safe_rationale: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    provenance_ref: str = Field(min_length=1)
    trust_label: Literal["curated", "tenant_verified"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ThoughtNodeState(_FrozenModel):
    node_id: str = Field(min_length=1)
    content_ref: str = Field(min_length=1)
    safe_summary: str = Field(min_length=1, max_length=500)
    score: float = Field(ge=0.0, le=1.0)
    depth: int = Field(ge=0)
    status: Literal["candidate", "selected", "pruned", "merged", "terminal"]
    parent_ids: tuple[str, ...]

    @field_validator("parent_ids")
    @classmethod
    def require_at_most_two_parents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 2:
            raise ValueError("thought nodes support at most two parents")
        if len(value) != len(set(value)):
            raise ValueError("thought parent IDs must be unique")
        return value


class ThoughtEdgeState(_FrozenModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relation: Literal["expands", "refines", "combines", "contradicts"]


class SubproblemState(_FrozenModel):
    subproblem_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    depends_on: tuple[str, ...]
    status: Literal["pending", "ready", "running", "complete", "failed"]
    answer_ref: str | None


class ToolPlanStep(_FrozenModel):
    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    depends_on: tuple[str, ...]
    output_variable: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)


class SearchNodeState(_FrozenModel):
    node_id: str = Field(min_length=1)
    parent_id: str | None
    action_ref: str = Field(min_length=1)
    observation_ref: str | None
    visits: int = Field(ge=0)
    value_sum: float
    depth: int = Field(ge=0)
    is_terminal: bool


class CompiledTask(_FrozenModel):
    task_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    depends_on: tuple[str, ...]
    output_schema: dict[str, JsonValue]
    status: Literal["pending", "ready", "running", "complete", "failed", "cancelled"]


class LocalReasoningResult(_FrozenModel):
    phase: ReasoningPhase
    answer: str | None = None
    terminal_reason: str | None = None
    checkpoint_cursor: str | None = None
    call_count: int = Field(default=0, ge=0)
    node_count: int = Field(default=0, ge=0)
    safe_evidence: dict[str, JsonValue] = Field(default_factory=dict)


def topological_order[T](
    items: Iterable[T],
    *,
    id_of: Callable[[T], str],
    dependencies_of: Callable[[T], tuple[str, ...]],
) -> tuple[T, ...]:
    ordered_items = tuple(items)
    by_id: dict[str, T] = {}
    ordinal: dict[str, int] = {}
    for index, item in enumerate(ordered_items):
        item_id = id_of(item)
        if item_id in by_id:
            raise ReasoningContractError(f"duplicate ID: {item_id}")
        by_id[item_id] = item
        ordinal[item_id] = index

    incoming: dict[str, int] = dict.fromkeys(by_id, 0)
    children: dict[str, list[str]] = defaultdict(list)
    level: dict[str, int] = dict.fromkeys(by_id, 0)
    for item_id, item in by_id.items():
        dependencies = dependencies_of(item)
        if len(dependencies) != len(set(dependencies)):
            raise ReasoningContractError(f"duplicate dependency for {item_id}")
        for dependency in dependencies:
            if dependency == item_id:
                raise ReasoningContractError(f"self dependency: {item_id}")
            if dependency not in by_id:
                raise ReasoningContractError(f"unknown dependency: {dependency}")
            incoming[item_id] += 1
            children[dependency].append(item_id)

    ready = sorted(
        (item_id for item_id, count in incoming.items() if count == 0),
        key=lambda item_id: (ordinal[item_id], item_id),
    )
    result: list[T] = []
    while ready:
        item_id = ready.pop(0)
        result.append(by_id[item_id])
        for child in sorted(children[item_id], key=lambda value: (ordinal[value], value)):
            incoming[child] -= 1
            level[child] = max(level[child], level[item_id] + 1)
            if incoming[child] == 0:
                ready.append(child)
        ready.sort(key=lambda value: (level[value], ordinal[value], value))
    if len(result) != len(by_id):
        raise ReasoningContractError("dependency graph contains a cycle")
    return tuple(result)


def validate_subproblems(items: Iterable[SubproblemState]) -> tuple[SubproblemState, ...]:
    ordered = topological_order(
        items,
        id_of=lambda item: item.subproblem_id,
        dependencies_of=lambda item: item.depends_on,
    )
    if not ordered:
        raise ReasoningContractError("at least one subproblem is required")
    if len(ordered) > 8:
        raise ReasoningContractError("subproblem limit exceeded")
    return ordered


def validate_tool_plan(items: Iterable[ToolPlanStep]) -> tuple[ToolPlanStep, ...]:
    ordered = topological_order(
        items,
        id_of=lambda item: item.step_id,
        dependencies_of=lambda item: item.depends_on,
    )
    variables = [item.output_variable for item in ordered]
    if len(variables) != len(set(variables)):
        raise ReasoningContractError("duplicate output variable")
    return ordered


def validate_compiled_tasks(items: Iterable[CompiledTask]) -> tuple[CompiledTask, ...]:
    return topological_order(
        items,
        id_of=lambda item: item.task_id,
        dependencies_of=lambda item: item.depends_on,
    )


def validate_thought_graph(
    nodes: Iterable[ThoughtNodeState],
    edges: Iterable[ThoughtEdgeState],
) -> tuple[ThoughtNodeState, ...]:
    node_items = tuple(nodes)
    edge_items = tuple(edges)
    by_id = {node.node_id: node for node in node_items}
    if len(by_id) != len(node_items):
        raise ReasoningContractError("duplicate thought node ID")
    if len(node_items) > 24 or len(edge_items) > 48:
        raise ReasoningContractError("thought graph limit exceeded")
    if any(node.depth > 4 for node in node_items):
        raise ReasoningContractError("thought depth limit exceeded")
    dependencies: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    seen_edges: set[tuple[str, str]] = set()
    for edge in edge_items:
        if edge.source_id not in by_id or edge.target_id not in by_id:
            raise ReasoningContractError("thought edge references unknown node")
        if edge.source_id == edge.target_id:
            raise ReasoningContractError("thought self-edge")
        identity = (edge.source_id, edge.target_id)
        if identity in seen_edges:
            raise ReasoningContractError("duplicate thought edge")
        seen_edges.add(identity)
        dependencies[edge.target_id].append(edge.source_id)
    topological_order(
        node_items,
        id_of=lambda node: node.node_id,
        dependencies_of=lambda node: tuple(dependencies[node.node_id]),
    )
    return tuple(sorted(node_items, key=lambda node: (-node.score, node.depth, node.node_id)))


def canonical_json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "CompiledTask",
    "JsonValue",
    "LocalReasoningResult",
    "ReasoningContractError",
    "ReasoningExample",
    "ReasoningPhase",
    "SearchNodeState",
    "SubproblemState",
    "ThoughtEdgeState",
    "ThoughtNodeState",
    "ToolPlanStep",
    "canonical_json",
    "topological_order",
    "validate_compiled_tasks",
    "validate_subproblems",
    "validate_thought_graph",
    "validate_tool_plan",
]
