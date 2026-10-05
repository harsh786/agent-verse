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
                self.step.id,
                getattr(self.step, "runtime", "python") or "python",
                code,
                inputs,
                tenant_id=str(state.get("tenant_id") or ""),
                run_id=str(state.get("run_id") or ""),
            )

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}


_OUTPUT_MARKER = "__AGENTVERSE_STEP_OUTPUT__"


async def _run_in_code_sandbox(
    step_id: str,
    runtime: str,
    code: str,
    inputs: dict[str, Any],
    *,
    tenant_id: str,
    run_id: str,
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

    from app.tenancy.context import PlanTier, TenantContext
    from app.tools import code_execution
    from app.workflow.engine_audit import audit_tenant_id

    if not tenant_id:
        # Every execution is audited against a tenant; never run unattributed code.
        raise RuntimeError(f"code step {step_id!r}: no tenant in the workflow run state")
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
    ctx = code_execution.CodeExecutionContext(
        # Only the tenant id matters for the sandbox + audit row (no plan/roles used).
        # The run state carries the workflow tables' dashed UUID; the audit trail
        # keys tenants by the hex form they are created with (as engine_audit
        # does), so the row lands with the tenant's other audit rows (P4-2).
        tenant_ctx=TenantContext(
            tenant_id=audit_tenant_id(tenant_id), plan=PlanTier.FREE, api_key_id="workflow"
        ),
        source="workflow.code_step",
        ref_id=run_id,
        step_id=step_id,
    )
    try:
        async with code_execution.env_redis() as redis:
            res = await code_execution.execute_governed(program, lang, 30, ctx=ctx, redis=redis)
    except code_execution.CodeExecutionBusyError as exc:
        raise RuntimeError(f"code step {step_id!r}: {exc}") from exc
    except code_execution.AuditPersistenceError as exc:
        raise RuntimeError(f"code step {step_id!r}: execution could not be audited") from exc
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
