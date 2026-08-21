"""WorkflowCompiler — compiles WorkflowDefinition → LangGraph StateGraph.

One compiled graph is cached per (workflow_id, version) to avoid
re-compiling on every run. Cache is invalidated on re-publish.

The compiler:
  1. Registers each step as a LangGraph node
  2. Wires edges based on depends_on and step type
  3. Handles parallel fan-out/fan-in
  4. Sets up conditional routing
  5. Compiles with the shared Redis checkpointer
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.registry import StepTypeRegistry
from app.workflow.state import WorkflowRunStatus, WorkflowState

_log = get_logger(__name__)


class CompiledWorkflow:
    """Wrapper around a compiled LangGraph graph."""

    def __init__(self, graph: Any, definition: WorkflowDefinition) -> None:
        self.graph = graph
        self.definition = definition

    async def ainvoke(
        self, state: WorkflowState, config: dict[str, Any] | None = None
    ) -> WorkflowState:
        result: Any = await self.graph.ainvoke(state, config or {})  # type: ignore[no-any-return]
        return result  # type: ignore[return-value]

    async def aupdate_state(self, config: dict[str, Any], state_update: dict[str, Any]) -> None:
        await self.graph.aupdate_state(config, state_update)

    async def aget_state(self, config: dict[str, Any]) -> Any:
        return await self.graph.aget_state(config)


class WorkflowCompiler:
    """Compiles workflow DSL definitions into executable LangGraph graphs."""

    def __init__(
        self,
        context_resolver: ContextResolver,
        checkpointer: Any = None,
        **services: Any,
    ) -> None:
        self._ctx = context_resolver
        self._checkpointer = checkpointer or MemorySaver()
        self._services = services
        # Cache: (workflow_id, version) → CompiledWorkflow
        self._cache: dict[str, CompiledWorkflow] = {}

    def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """Compile a WorkflowDefinition to a runnable graph. Uses cache."""
        cache_key = f"{definition.id}:{definition.version}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        compiled = self._compile_uncached(definition)
        self._cache[cache_key] = compiled
        return compiled

    def invalidate(self, workflow_id: str) -> None:
        """Remove all cached versions for a workflow (called on re-publish)."""
        keys_to_del = [k for k in self._cache if k.startswith(f"{workflow_id}:")]
        for k in keys_to_del:
            del self._cache[k]

    def _compile_uncached(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        graph = StateGraph(WorkflowState)

        # 1. Register each step as a graph node
        for step in definition.steps:
            node_fn = self._build_node_fn(step)
            graph.add_node(step.id, node_fn)

        # 2. Find entry steps (no depends_on or all deps are parallel branches)
        entry_steps = [s for s in definition.steps if not s.depends_on]
        terminal_steps = self._find_terminal_steps(definition)

        for s in entry_steps:
            graph.add_edge(START, s.id)

        # 3. Wire edges
        for step in definition.steps:
            if step.type == "conditional":
                # Fan-out: one conditional edge per branch
                branch_map = {b.next: b.next for b in step.branches if b.next}

                def make_router(s: Any = step) -> Any:
                    async def router(state: WorkflowState) -> str:
                        out = (state.get("step_outputs") or {}).get(s.id, {})
                        return str(out.get("chosen_branch", ""))

                    return router

                graph.add_conditional_edges(step.id, make_router(), branch_map)  # type: ignore[arg-type]

            elif step.type == "hitl":
                # After HITL step: route based on _hitl_next_step
                action_targets = {a.next: a.next for a in step.actions if a.next}
                if action_targets:

                    def make_hitl_router(s: Any = step) -> Any:
                        async def router(state: WorkflowState) -> str:
                            if state.get("status") == WorkflowRunStatus.WAITING_HITL:
                                return "__end__"
                            out = (state.get("step_outputs") or {}).get(s.id, {})
                            return str(out.get("action", ""))

                        return router

                    # Add END as a valid target for the waiting state
                    action_targets["__end__"] = END
                    graph.add_conditional_edges(step.id, make_hitl_router(), action_targets)  # type: ignore[arg-type]
                else:
                    # No explicit actions — just route to downstream steps
                    downstream = self._find_downstream(step.id, definition)
                    for ds in downstream:
                        graph.add_edge(step.id, ds)
            else:
                # Standard edges: step → all steps that depend on it
                downstream = self._find_downstream(step.id, definition)
                for ds in downstream:
                    graph.add_edge(step.id, ds)

        for terminal_id in terminal_steps:
            graph.add_edge(terminal_id, END)

        compiled_graph = graph.compile(checkpointer=self._checkpointer)
        _log.info(
            "workflow_compiled",
            workflow_id=definition.id,
            version=definition.version,
            steps=len(definition.steps),
        )
        return CompiledWorkflow(compiled_graph, definition)

    def _build_node_fn(self, step: Any) -> Any:
        """Build the async node function for a step."""
        node_class = StepTypeRegistry.get(step.type)
        node = node_class(step, self._ctx, **self._services)

        async def node_fn(state: WorkflowState) -> dict[str, Any]:
            # Check operator pause before each step
            if state.get("paused_by"):
                return {"status": WorkflowRunStatus.PAUSED}
            return await node.execute(state)  # type: ignore[arg-type]

        node_fn.__name__ = f"step_{step.id}"
        return node_fn

    @staticmethod
    def _find_downstream(step_id: str, definition: WorkflowDefinition) -> list[str]:
        """Find all steps that directly depend on step_id."""
        return [
            s.id
            for s in definition.steps
            if (step_id in s.depends_on and not s.depends_on_any)
            or (s.depends_on_any and step_id in s.depends_on)
        ]

    @staticmethod
    def _find_terminal_steps(definition: WorkflowDefinition) -> list[str]:
        """Steps that nothing else depends on are terminal → connect to END."""
        all_ids = {s.id for s in definition.steps}
        depended_on = {dep for s in definition.steps for dep in s.depends_on}
        # Steps that are conditional/hitl targets are also "depended on"
        branch_targets = set()
        for s in definition.steps:
            for b in s.branches:
                branch_targets.add(b.next)
            for a in s.actions:
                if a.next:
                    branch_targets.add(a.next)
        depended_on.update(branch_targets)
        return list(all_ids - depended_on)
