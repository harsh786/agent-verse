"""StepTypeRegistry — public plugin extension point for the workflow engine.

All 14 built-in step types are registered at startup.
Third-party code can register custom step types:

    from app.workflow.registry import StepTypeRegistry, StepTypeMeta

    StepTypeRegistry.register(
        step_type="salesforce.query",
        node_class=SalesforceQueryNode,
        meta=StepTypeMeta(
            step_type="salesforce.query",
            display_name="Salesforce SOQL Query",
            category="CRM",
            icon="salesforce",
            input_schema={...},
            output_schema={...},
            description="Run a SOQL query against Salesforce",
        ),
    )

The Visual Builder's tool palette and the YAML validator both read from
this registry. Custom step types appear automatically in the builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.workflow.steps.base import BaseStepNode


class UnknownStepTypeError(KeyError):
    pass


@dataclass
class StepTypeMeta:
    step_type: str
    display_name: str
    category: str
    icon: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    description: str
    is_built_in: bool = True
    requires_connectors: list[str] = field(default_factory=list)
    color: str = "#64748B"
    node_shape: str = "rect"  # rect | diamond | pill | fork | clock


class StepTypeRegistry:
    """Singleton registry of all available workflow step types."""

    _registry: dict[str, type[BaseStepNode]] = {}  # noqa: RUF012
    _metadata: dict[str, StepTypeMeta] = {}  # noqa: RUF012

    @classmethod
    def register(
        cls,
        step_type: str,
        node_class: type[BaseStepNode],
        meta: StepTypeMeta,
    ) -> None:
        """Register a step type. Safe to call multiple times (idempotent)."""
        cls._registry[step_type] = node_class
        cls._metadata[step_type] = meta

    @classmethod
    def get(cls, step_type: str) -> type[BaseStepNode]:
        """Get a step node class by type name."""
        if step_type not in cls._registry:
            raise UnknownStepTypeError(
                f"Unknown step type: {step_type!r}. Available: {sorted(cls._registry.keys())}"
            )
        return cls._registry[step_type]

    @classmethod
    def get_meta(cls, step_type: str) -> StepTypeMeta:
        if step_type not in cls._metadata:
            raise UnknownStepTypeError(f"No metadata for step type: {step_type!r}")
        return cls._metadata[step_type]

    @classmethod
    def list_all(cls) -> list[StepTypeMeta]:
        """List all registered step types (drives Visual Builder palette)."""
        return sorted(cls._metadata.values(), key=lambda m: (m.category, m.display_name))

    @classmethod
    def is_registered(cls, step_type: str) -> bool:
        return step_type in cls._registry

    @classmethod
    def clear(cls) -> None:
        """Test helper — reset registry to empty state."""
        cls._registry.clear()
        cls._metadata.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Register all 14 built-in step types at module load time
# ─────────────────────────────────────────────────────────────────────────────


def _register_built_ins() -> None:
    """Import and register all built-in step nodes."""
    # Deferred imports to avoid circular deps at module load
    from app.workflow.steps.code_step import CodeStepNode
    from app.workflow.steps.conditional_step import ConditionalStepNode
    from app.workflow.steps.emit_event_step import EmitEventStepNode
    from app.workflow.steps.foreach_step import ForeachStepNode
    from app.workflow.steps.hitl_step import HITLStepNode
    from app.workflow.steps.http_step import HTTPStepNode
    from app.workflow.steps.llm_step import LLMStepNode
    from app.workflow.steps.parallel_step import ParallelStepNode
    from app.workflow.steps.rag_step import RAGStepNode
    from app.workflow.steps.set_variable_step import SetVariableStepNode
    from app.workflow.steps.sub_workflow_step import SubWorkflowStepNode
    from app.workflow.steps.tool_step import ToolStepNode
    from app.workflow.steps.transform_step import TransformStepNode
    from app.workflow.steps.wait_step import WaitStepNode

    _builtins = [
        (
            "tool",
            ToolStepNode,
            StepTypeMeta(
                "tool",
                "MCP Tool",
                "Tools",
                "tool",
                {},
                {},
                "Call any registered MCP tool",
                color="#DBEAFE",
            ),
        ),
        (
            "llm",
            LLMStepNode,
            StepTypeMeta(
                "llm",
                "LLM Prompt",
                "AI",
                "brain",
                {},
                {},
                "LLM completion with optional RAG",
                color="#F3E8FF",
            ),
        ),
        (
            "rag",
            RAGStepNode,
            StepTypeMeta(
                "rag",
                "RAG Retrieval",
                "AI",
                "book",
                {},
                {},
                "Knowledge base retrieval + LLM",
                color="#FFF7ED",
            ),
        ),
        (
            "http",
            HTTPStepNode,
            StepTypeMeta(
                "http",
                "HTTP Request",
                "Network",
                "globe",
                {},
                {},
                "Authenticated HTTP call to external API",
                color="#E0E7FF",
            ),
        ),
        (
            "hitl",
            HITLStepNode,
            StepTypeMeta(
                "hitl",
                "Human Review",
                "Control",
                "user",
                {},
                {},
                "Human-in-the-loop approval gate",
                color="#FEE2E2",
                node_shape="rect",
            ),
        ),
        (
            "parallel",
            ParallelStepNode,
            StepTypeMeta(
                "parallel",
                "Parallel",
                "Flow",
                "fork",
                {},
                {},
                "Run branches concurrently",
                color="#CCFBF1",
                node_shape="fork",
            ),
        ),
        (
            "conditional",
            ConditionalStepNode,
            StepTypeMeta(
                "conditional",
                "Condition",
                "Flow",
                "diamond",
                {},
                {},
                "Route based on expression",
                color="#FEF9C3",
                node_shape="diamond",
            ),
        ),
        (
            "foreach",
            ForeachStepNode,
            StepTypeMeta(
                "foreach",
                "For Each",
                "Flow",
                "repeat",
                {},
                {},
                "Iterate over a list",
                color="#FCE7F3",
            ),
        ),
        (
            "transform",
            TransformStepNode,
            StepTypeMeta(
                "transform",
                "Transform",
                "Data",
                "shuffle",
                {},
                {},
                "Pure data mapping / reshape",
                color="#F0FDF4",
            ),
        ),
        (
            "sub_workflow",
            SubWorkflowStepNode,
            StepTypeMeta(
                "sub_workflow",
                "Sub-Workflow",
                "Flow",
                "nested",
                {},
                {},
                "Call another workflow as a step",
                color="#F8FAFC",
            ),
        ),
        (
            "wait",
            WaitStepNode,
            StepTypeMeta(
                "wait",
                "Wait",
                "Control",
                "clock",
                {},
                {},
                "Pause for duration or event",
                color="#F1F5F9",
                node_shape="clock",
            ),
        ),
        (
            "code",
            CodeStepNode,
            StepTypeMeta(
                "code",
                "Code",
                "Dev",
                "code",
                {},
                {},
                "Run sandboxed Python or JavaScript",
                color="#FDF4FF",
            ),
        ),
        (
            "set_variable",
            SetVariableStepNode,
            StepTypeMeta(
                "set_variable",
                "Set Variable",
                "Data",
                "variable",
                {},
                {},
                "Write a mutable workflow variable",
                color="#FFFBEB",
            ),
        ),
        (
            "emit_event",
            EmitEventStepNode,
            StepTypeMeta(
                "emit_event",
                "Emit Event",
                "Integration",
                "broadcast",
                {},
                {},
                "Publish a Redis event to other workflows",
                color="#F0F9FF",
            ),
        ),
    ]

    for step_type, node_class, meta in _builtins:
        StepTypeRegistry.register(step_type, node_class, meta)  # type: ignore[arg-type]


# Trigger registration when registry.py is first imported
_register_built_ins()
