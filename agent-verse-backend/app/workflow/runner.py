"""WorkflowRunner — orchestrates workflow run lifecycle.

Responsibilities:
  1. Validate inputs against workflow input schema
  2. Apply trigger_transform to reshape raw payload → inputs
  3. Create workflow_runs DB record
  4. Dispatch to Celery (per-plan queue)
  5. OTEL trace wrapping
  6. POST callback URL on completion
  7. Operator pause check before each step
  8. Enforce max 1MB trigger payload
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.state import WorkflowRunStatus, WorkflowState

_log = get_logger(__name__)

MAX_TRIGGER_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB


class WorkflowValidationError(ValueError):
    pass


class WorkflowRunner:
    """Entry point for triggering and managing workflow runs."""

    def __init__(
        self,
        compiler: Any,
        run_store: Any | None = None,
        celery_app: Any | None = None,
        context_resolver: ContextResolver | None = None,
        **services: Any,
    ) -> None:
        self._compiler = compiler
        self._run_store = run_store
        self._celery = celery_app
        self._ctx = context_resolver or ContextResolver()
        self._services = services

    async def trigger(
        self,
        *,
        workflow_id: str,
        tenant_id: str,
        inputs: dict[str, Any],
        idempotency_key: str | None = None,
        dry_run: bool = False,
        callback_url: str | None = None,
    ) -> dict[str, Any]:
        """Router-facing entrypoint (WT-7/P0-6).

        Maps the ``POST /workflows/{id}/trigger`` contract onto :meth:`run` and
        returns a RunResponse-shaped dict. Previously the router called this
        method, which did not exist -> AttributeError -> HTTP 500.
        """
        run_id = await self.run(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=inputs,
            is_test_run=dry_run,
            run_metadata={"idempotency_key": idempotency_key, "callback_url": callback_url},
        )
        if self._run_store is not None:
            rec = await self._run_store.get(tenant_id, run_id)
            if rec:
                return rec
        return {"run_id": run_id, "workflow_id": workflow_id, "status": "pending"}

    async def run(
        self,
        workflow_id: str,
        tenant_id: str,
        inputs: dict[str, Any],
        trigger_type: str = "api",
        trigger_payload: dict[str, Any] | None = None,
        is_test_run: bool = False,
        mock_overrides: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
        run_metadata: dict[str, Any] | None = None,
        wait_for_completion: bool = False,
    ) -> str:
        """Trigger a new workflow run. Returns run_id immediately."""

        # 1. Payload size guard
        payload_bytes = len(json.dumps(inputs, default=str).encode())
        if payload_bytes > MAX_TRIGGER_PAYLOAD_BYTES:
            raise WorkflowValidationError(
                f"Trigger payload too large: {payload_bytes} bytes > 1MB limit"
            )

        # 2. Load definition (from store or in-memory for tests)
        definition = await self._load_definition(workflow_id, tenant_id)

        # 3. Apply trigger_transform
        if definition.trigger_transform and trigger_payload:
            base_state: dict[str, Any] = {"raw_trigger": trigger_payload}
            transformed = self._ctx.resolve_dict(definition.trigger_transform, base_state)
            inputs = {**inputs, **transformed}

        # 4. Validate inputs
        self._validate_inputs(definition, inputs)

        # 5. Build initial state
        run_id = str(uuid.uuid4())
        initial_state: WorkflowState = {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "tenant_id": tenant_id,
            "workflow_name": definition.name,
            "inputs": inputs,
            "raw_trigger": trigger_payload or {},
            "step_outputs": {},
            "outputs": {},
            "vars": dict(definition.vars),
            "status": WorkflowRunStatus.PENDING,
            "current_step_id": None,
            "completed_branch": None,
            "error": None,
            "error_step_id": None,
            "hitl_request_id": None,
            "hitl_action": None,
            "hitl_note": None,
            "hitl_reviewer": None,
            "hitl_form_data": None,
            "foreach_progress": {},
            "cost_usd": 0.0,
            "tokens_used": 0,
            "step_timings": {},
            "paused_by": None,
            "paused_at": None,
            "pause_reason": None,
            "is_test_run": is_test_run,
            "mock_overrides": mock_overrides or {},
            "labels": {**definition.run_labels, **(labels or {})},
            "run_metadata": run_metadata or {},
            "vault_refs_used": set(),
            "_env": definition.env,
        }

        # 6. Persist run record
        if self._run_store is not None:
            await self._run_store.create(
                run_id=run_id,
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                trigger_type=trigger_type,
                trigger_payload=trigger_payload,
                inputs=inputs,
                labels=initial_state["labels"],
                is_test_run=is_test_run,
            )

        # 7. Execute (inline for tests, Celery for production)
        if is_test_run or wait_for_completion or self._celery is None:
            await self._execute_inline(run_id, definition, initial_state)
        else:
            plan_tier = await self._get_plan_tier(tenant_id)
            from app.workflow.celery_tasks import execute_workflow_run

            execute_workflow_run.apply_async(
                args=[run_id, workflow_id, tenant_id],
                kwargs={"is_test_run": is_test_run, "mock_overrides": mock_overrides or {}},
                queue=f"workflows.{plan_tier}",
            )

        return run_id

    async def _execute_inline(
        self,
        run_id: str,
        definition: WorkflowDefinition,
        initial_state: WorkflowState,
    ) -> None:
        """Execute synchronously (tests / wait_for_completion=True)."""
        compiled = self._compiler.compile(definition)
        config = {"configurable": {"thread_id": run_id}}
        try:
            await compiled.ainvoke(initial_state, config)
        except Exception as exc:
            _log.error(
                "workflow_run_failed_inline", run_id=run_id, error=repr(exc), exc_info=True
            )
            if self._run_store:
                # tenant_id is keyword-only-required (RLS-scoped); read it from
                # the run state so the failure update targets the right tenant.
                await self._run_store.update_status(
                    run_id,
                    WorkflowRunStatus.FAILED,
                    tenant_id=initial_state["tenant_id"],
                    error=str(exc),
                )

    async def resume_from_hitl(
        self,
        run_id: str,
        step_id: str,
        action: str,
        actor_id: str,
        note: str | None,
        form_data: dict[str, Any] | None,
        tenant_id: str,
    ) -> None:
        """Resume a WAITING_HITL run after reviewer decision."""
        # Load state from checkpointer
        workflow_id = await self._run_store.get_workflow_id(run_id) if self._run_store else ""
        definition = await self._load_definition(workflow_id, tenant_id)
        compiled = self._compiler.compile(definition)

        config = {"configurable": {"thread_id": run_id}}
        state_update: dict[str, Any] = {
            "status": WorkflowRunStatus.RUNNING,
            # WS-3 fix: must equal step_id, not None. HITLStepNode.execute()
            # detects "we are resuming" via
            # ``state.get("hitl_request_id") == self.step.id`` — clearing it to
            # None here made that comparison always false, so a re-invoked run
            # re-entered the hitl step as if it were a brand new suspend
            # instead of processing the reviewer's decision.
            "hitl_request_id": step_id,
            "hitl_action": action,
            "hitl_note": note,
            "hitl_reviewer": actor_id,
            "hitl_form_data": form_data,
        }
        await compiled.aupdate_state(config, state_update)

        # Re-dispatch to continue execution
        if self._celery:
            from app.workflow.celery_tasks import execute_workflow_run

            execute_workflow_run.apply_async(
                args=[run_id, workflow_id, tenant_id],
                kwargs={"resume": True},
                queue=f"workflows.{await self._get_plan_tier(tenant_id)}",
            )
        else:
            current = await compiled.aget_state(config)
            if hasattr(current, "values"):
                state = dict(current.values)
                state.update(state_update)
                await compiled.ainvoke(state, config)

    async def _load_definition(self, workflow_id: str, tenant_id: str) -> WorkflowDefinition:
        if self._run_store is not None:
            data = await self._run_store.get_definition(workflow_id, tenant_id)
            definition = WorkflowDefinition.from_json(data)
            # API-created DSL carries no ``id``; stamp the workflow id so the
            # compiler's per-(id, version) graph cache doesn't collide across
            # distinct workflows (each would otherwise share key ":<version>").
            if not definition.id:
                definition.id = workflow_id
            return definition
        # Fallback for tests — return minimal definition
        return WorkflowDefinition(name="test", id=workflow_id)

    @staticmethod
    def _validate_inputs(definition: WorkflowDefinition, inputs: dict[str, Any]) -> None:
        for name, input_def in definition.inputs.items():
            if input_def.required and name not in inputs and input_def.default is None:
                raise WorkflowValidationError(f"Required input {name!r} is missing")
            if name in inputs and input_def.enum and inputs[name] not in input_def.enum:
                raise WorkflowValidationError(
                    f"Input {name!r} value {inputs[name]!r} not in enum {input_def.enum}"
                )

    async def _get_plan_tier(self, tenant_id: str) -> str:
        """Get plan tier for Celery queue routing."""
        tenant_service = self._services.get("tenant_service")
        if tenant_service is None:
            return "free"
        try:
            tenant = await tenant_service.get(tenant_id)
            return getattr(tenant, "plan", "free") or "free"
        except Exception:
            return "free"

    async def send_callback(
        self,
        definition: WorkflowDefinition,
        state: WorkflowState,
    ) -> None:
        """POST final outputs to callback URL if configured."""
        if not definition.callback or not definition.callback.url:
            return

        cb_url = self._ctx.resolve(definition.callback.url, state)
        payload = {
            "run_id": state.get("run_id"),
            "status": str(state.get("status", "")),
            "outputs": state.get("outputs", {}),
            "labels": state.get("labels", {}),
            "cost_usd": state.get("cost_usd", 0.0),
        }

        if state.get("status") == WorkflowRunStatus.FAILED and not definition.callback.on_failure:
            return

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(str(cb_url), json=payload)
        except Exception as exc:
            _log.warning("callback_failed", url=cb_url, error=str(exc))
