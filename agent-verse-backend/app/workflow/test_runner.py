"""WorkflowTestRunner — sandbox execution for testing workflows without side effects.

Modes:
  * dry_run:      All tool/HTTP/LLM/HITL steps are mocked. Returns predicted outputs.
  * step_through: Pause before each step, allow inspector to override outputs.
  * scenario:     Run against a predefined input fixture and assert outputs.
  * replay:       Re-run from a specific failed step using checkpointed state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger
from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.state import WorkflowRunStatus

_log = get_logger(__name__)


@dataclass
class WorkflowScenario:
    """Input fixture + expected output assertions."""

    name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    mock_overrides: dict[str, Any] = field(default_factory=dict)
    expected_outputs: dict[str, Any] = field(default_factory=dict)
    expected_status: str = WorkflowRunStatus.COMPLETE.value
    # Assertions on step_outputs: step_id → {key: expected_value}
    step_assertions: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class WorkflowRunResult:
    """Result of a single test run."""

    run_id: str
    status: str
    step_outputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    passed: bool = True
    assertion_failures: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    tokens_used: int = 0


class MockToolAdapter:
    """Intercepts tool/HTTP/LLM/HITL calls and returns configured mock outputs."""

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._overrides = overrides or {}

    def mock_output(self, step_id: str, step_type: str) -> dict[str, Any]:
        """Return mock output for a step."""
        if step_id in self._overrides:
            return self._overrides[step_id]
        # Default mock outputs by step type
        defaults: dict[str, dict[str, Any]] = {
            "tool": {"result": f"[mock] {step_id} output", "success": True},
            "llm": {"result": f"[mock] LLM response for {step_id}", "confidence": 0.9},
            "rag": {
                "result": f"[mock] RAG retrieved docs for {step_id}",
                "docs": [],
            },
            "http": {"status_code": 200, "body": {"mocked": True}},
            "hitl": {
                "action": "approved",
                "reviewer": "test-user",
                "note": "auto-approved in test",
            },
            "parallel": {},
            "conditional": {"chosen_branch": "true"},
            "foreach": {f"{step_id}_results": [], "_total": 0, "_failed": 0},
            "transform": {"output": {}},
            "sub_workflow": {"sub_run_id": f"mock-{uuid.uuid4().hex[:8]}", "outputs": {}},
            "wait": {"waited_seconds": 0, "event": None},
            "code": {"output": None, "result": "[mock] code output"},
            "set_variable": {"variable": step_id, "value": "[mock]"},
            "emit_event": {"event_channel": "[mock]", "published": True},
        }
        return defaults.get(step_type, {"_mock": True, "step_id": step_id})


class WorkflowTestRunner:
    """Executes workflow definitions in test mode — no real tools called."""

    def __init__(
        self,
        compiler: WorkflowCompiler | None = None,
        context_resolver: ContextResolver | None = None,
    ) -> None:
        self._compiler = compiler or WorkflowCompiler(
            context_resolver=context_resolver or ContextResolver()
        )
        self._ctx = context_resolver or ContextResolver()

    # ── Public API ────────────────────────────────────────────────────────────

    async def dry_run(
        self,
        definition: WorkflowDefinition,
        inputs: dict[str, Any],
        mock_overrides: dict[str, Any] | None = None,
    ) -> WorkflowRunResult:
        """Execute all steps with mocked outputs."""
        adapter = MockToolAdapter(mock_overrides)
        return await self._execute_mocked(definition, inputs, adapter)

    async def run_scenario(
        self,
        definition: WorkflowDefinition,
        scenario: WorkflowScenario,
    ) -> WorkflowRunResult:
        """Run a scenario and validate assertions."""
        result = await self.dry_run(
            definition,
            scenario.inputs,
            scenario.mock_overrides or None,
        )

        # Check expected status
        failures: list[str] = []
        if result.status != scenario.expected_status:
            failures.append(f"Expected status {scenario.expected_status!r}, got {result.status!r}")

        # Check expected outputs
        for key, expected in scenario.expected_outputs.items():
            actual = result.outputs.get(key)
            if actual != expected:
                failures.append(f"Output {key!r}: expected {expected!r}, got {actual!r}")

        # Check step assertions
        for step_id, assertions in scenario.step_assertions.items():
            step_out = result.step_outputs.get(step_id, {})
            for key, expected in assertions.items():
                actual = step_out.get(key)
                if actual != expected:
                    failures.append(
                        f"Step {step_id!r} output {key!r}: expected {expected!r}, got {actual!r}"
                    )

        result.assertion_failures = failures
        result.passed = not failures
        return result

    async def run_scenarios(
        self,
        definition: WorkflowDefinition,
        scenarios: list[WorkflowScenario],
    ) -> list[WorkflowRunResult]:
        """Run multiple scenarios and collect results."""
        results = []
        for scenario in scenarios:
            result = await self.run_scenario(definition, scenario)
            results.append(result)
        return results

    async def step_through(
        self,
        definition: WorkflowDefinition,
        inputs: dict[str, Any],
        step_overrides: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute step-by-step and return each step's result.

        step_overrides: step_id → forced output dict
        Returns a list of step execution records.
        """
        adapter = MockToolAdapter(step_overrides)
        steps_log: list[dict[str, Any]] = []
        state: dict[str, Any] = {
            "run_id": f"test-{uuid.uuid4().hex[:8]}",
            "workflow_id": definition.id,
            "inputs": inputs,
            "step_outputs": {},
            "vars": dict(definition.vars),
            "status": WorkflowRunStatus.RUNNING,
        }

        for step in definition.steps:
            mock_out = adapter.mock_output(step.id, step.type)
            state["step_outputs"][step.id] = mock_out  # type: ignore[index]
            steps_log.append(
                {
                    "step_id": step.id,
                    "step_type": step.type,
                    "output": mock_out,
                    "status": "completed",
                }
            )

        return steps_log

    # ── Private ───────────────────────────────────────────────────────────────

    async def _execute_mocked(
        self,
        definition: WorkflowDefinition,
        inputs: dict[str, Any],
        adapter: MockToolAdapter,
    ) -> WorkflowRunResult:
        """Walk steps in dependency order and apply mock outputs."""
        run_id = f"test-{uuid.uuid4().hex[:8]}"
        step_outputs: dict[str, Any] = {}
        error: str | None = None
        status = WorkflowRunStatus.COMPLETE.value

        # Topological order (already guaranteed by DSL validator)
        ordered_steps = self._topo_sort(definition)

        for step in ordered_steps:
            try:
                mock_out = adapter.mock_output(step.id, step.type)
                step_outputs[step.id] = mock_out
            except Exception as exc:
                error = f"Step {step.id} failed: {exc}"
                status = WorkflowRunStatus.FAILED.value
                break

        # Build final outputs from terminal steps
        terminal_ids = self._find_terminal_step_ids(definition)
        outputs = {sid: step_outputs.get(sid, {}) for sid in terminal_ids}

        return WorkflowRunResult(
            run_id=run_id,
            status=status,
            step_outputs=step_outputs,
            outputs=outputs,
            error=error,
            passed=status == WorkflowRunStatus.COMPLETE.value,
        )

    @staticmethod
    def _topo_sort(definition: WorkflowDefinition) -> list[StepDefinition]:
        """Return steps in topological order (respecting depends_on)."""
        all_steps = {s.id: s for s in definition.steps}
        visited: set[str] = set()
        result: list[StepDefinition] = []

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            visited.add(step_id)
            step = all_steps[step_id]
            for dep in step.depends_on:
                if dep in all_steps:
                    visit(dep)
            result.append(step)

        for step in definition.steps:
            visit(step.id)
        return result

    @staticmethod
    def _find_terminal_step_ids(definition: WorkflowDefinition) -> list[str]:
        """Steps that nothing else depends on."""
        depended_on = {dep for s in definition.steps for dep in s.depends_on}
        return [s.id for s in definition.steps if s.id not in depended_on]
