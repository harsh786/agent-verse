"""CodeStepNode — sandboxed Python/JavaScript execution."""
from __future__ import annotations

from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


class CodeStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.execution_env = services.get("execution_environment")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        code = str(self.ctx.resolve(self.step.code, state))
        inputs = self.ctx.resolve_dict(self.step.input, state)

        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
        elif self.execution_env is not None:
            result = await self.execution_env.run(
                runtime=self.step.runtime,
                code=code,
                inputs=inputs,
                timeout_seconds=30,
            )
            output = result.output if hasattr(result, "output") else result
        else:
            # Minimal safe fallback for tests (Python only, no network/fs)
            import ast
            try:
                tree = ast.parse(code, mode="exec")
                local_ns: dict[str, Any] = {"inputs": inputs, "__builtins__": {}}
                exec(compile(tree, "<code>", "exec"), {}, local_ns)
                output = local_ns.get("output", local_ns.get("result", {}))
            except Exception as e:
                raise RuntimeError(f"code step {self.step.id!r} failed: {e}") from e

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}
