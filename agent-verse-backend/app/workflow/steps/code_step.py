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
            output = await _run_in_code_sandbox(
                self.step.id, getattr(self.step, "runtime", "python") or "python", code, inputs
            )

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}


_OUTPUT_MARKER = "__AGENTVERSE_STEP_OUTPUT__"


async def _run_in_code_sandbox(
    step_id: str, runtime: str, code: str, inputs: dict[str, Any]
) -> Any:
    """Run a code step in the Docker code sandbox (app.tools.code_interpreter).

    The fallback used to ``exec()`` the snippet inside the API/worker process
    with ``__builtins__ = {}`` as the only barrier — an escapable "sandbox"
    (e.g. via object subclasses), so anyone who could run a workflow could run
    code on the control plane with its secrets in reach. Without Docker this
    now fails the step, except for the explicit development-only opt-in
    (AGENTVERSE_ALLOW_SUBPROCESS_EXEC, never in production, scrubbed env).
    """
    import json

    from app.tools.code_interpreter import CodeInterpreter

    lang = "javascript" if runtime.lower() in {"javascript", "js", "node"} else "python"
    payload = json.dumps(json.dumps(inputs, default=str))
    if lang == "python":
        program = (
            "import json\n"
            f"inputs = json.loads({payload})\n"
            "output = None\nresult = None\n"
            f"{code}\n"
            f"print({_OUTPUT_MARKER!r} + json.dumps(output if output is not None else result, "
            "default=str))\n"
        )
    else:
        program = (
            f"const inputs = JSON.parse({payload});\n"
            "let output = null; let result = null;\n"
            f"{code}\n"
            f"console.log({_OUTPUT_MARKER!r} + JSON.stringify(output ?? result));\n"
        )
    try:
        res = await CodeInterpreter(default_timeout=30).execute(program, lang)
    except RuntimeError as exc:  # no sandbox in production
        raise RuntimeError(f"code step {step_id!r}: sandbox unavailable: {exc}") from exc
    if res.timed_out:
        raise RuntimeError(f"code step {step_id!r} timed out")
    if res.exit_code != 0:
        raise RuntimeError(f"code step {step_id!r} failed: {res.stderr.strip()[:500]}")
    for line in reversed(res.stdout.splitlines()):
        if line.startswith(_OUTPUT_MARKER):
            return json.loads(line[len(_OUTPUT_MARKER):] or "null")
    raise RuntimeError(f"code step {step_id!r} produced no output")
