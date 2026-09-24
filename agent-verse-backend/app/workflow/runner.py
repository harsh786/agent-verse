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

import contextlib
import json
import uuid
from typing import Any

import httpx

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.state import (
    WorkflowCancelled,
    WorkflowPaused,
    WorkflowRunStatus,
    WorkflowState,
)

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
        initial_state = self._build_initial_state(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            definition=definition,
            inputs=inputs,
            trigger_payload=trigger_payload,
            is_test_run=is_test_run,
            mock_overrides=mock_overrides,
            labels=labels,
            run_metadata=run_metadata,
        )

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

    async def resume(self, run_id: str, tenant_id: str) -> None:
        """Re-dispatch a paused run so it continues from where it stopped.

        The status was flipped back to RUNNING by the API (WorkflowService
        .resume_run). Re-executing reconstructs state from the run record and the
        compiler skips steps already persisted COMPLETE (see node_fn), so no
        completed work is redone. Uses Celery in production, inline otherwise.
        """
        workflow_id = (
            await self._run_store.get_workflow_id(run_id, tenant_id)
            if self._run_store is not None
            else ""
        )
        if not workflow_id:
            return
        if self._celery is None:
            await self.execute_fresh(run_id, workflow_id, tenant_id)
        else:
            from app.workflow.celery_tasks import execute_workflow_run

            execute_workflow_run.apply_async(
                args=[run_id, workflow_id, tenant_id],
                kwargs={},
                queue=f"workflows.{await self._get_plan_tier(tenant_id)}",
            )

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
            final_state = await compiled.ainvoke(initial_state, config)
            # Reach a terminal run-level status (COMPLETE), or persist the halt
            # (WAITING_HITL / PAUSED) the graph settled on — the graph nodes only
            # emit step_outputs, they never finalize the run row themselves.
            await self._finalize_status(run_id, initial_state["tenant_id"], final_state)
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

    def _build_initial_state(
        self,
        *,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        definition: WorkflowDefinition,
        inputs: dict[str, Any],
        trigger_payload: dict[str, Any] | None = None,
        is_test_run: bool = False,
        mock_overrides: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
        run_metadata: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Construct the initial LangGraph state for a run.

        Single source of truth so both the inline path (:meth:`run`) and the
        out-of-process Celery worker (:meth:`execute_fresh`) seed identical state.
        """
        state: WorkflowState = {
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
        return state

    async def execute_fresh(
        self,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        is_test_run: bool = False,
        mock_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Seed initial state from the run store and execute a fresh run.

        Called by the out-of-process Celery worker (``execute_workflow_run``).
        The Celery dispatch branch of :meth:`run` persists the run row but does
        **not** write the initial LangGraph checkpoint — and the worker runs on
        its own per-process checkpointer, so it cannot read anything the API
        process might have written. Reconstructing the initial state here from
        the persisted run record (inputs) plus the workflow definition lets the
        worker genuinely execute the run and persist real step results, instead
        of invoking an empty state.
        """
        definition = await self._load_definition(workflow_id, tenant_id)
        inputs: dict[str, Any] = {}
        labels: dict[str, str] | None = None
        if self._run_store is not None:
            record = await self._run_store.get(tenant_id, run_id)
            if record:
                inputs = record.get("inputs") or {}
                labels = record.get("labels") or None
        initial_state = self._build_initial_state(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            definition=definition,
            inputs=inputs,
            is_test_run=is_test_run,
            mock_overrides=mock_overrides,
            labels=labels,
        )
        compiled = self._compiler.compile(definition)
        config = {"configurable": {"thread_id": run_id}}
        # Mark RUNNING before executing so started_at is stamped at the real start
        # (update_status COALESCEs started_at on 'running') — otherwise the run
        # jumps straight to a terminal status and duration can't be computed.
        #
        # But check the CURRENT status first: this Celery task may have sat
        # queued for a while (busy tier queue) after ``run()`` created the row
        # as 'pending' and dispatched it. An operator can cancel or pause a
        # still-queued run in that window (WorkflowService.cancel_run/pause_run
        # both accept 'pending'). ``update_status`` has no WHERE-status guard —
        # it unconditionally overwrites whatever status is there — so blindly
        # setting RUNNING here would silently resurrect a CANCELLED/PAUSED run
        # back to RUNNING right before ``ainvoke`` executes every step for real
        # (including side-effecting ones), defeating the cancel/pause entirely.
        # The per-step cooperative check in compiler.py's node_fn can't save us
        # either: it reads status from the DB too, and by then we'd have
        # already overwritten the CANCELLED/PAUSED marker with RUNNING.
        if self._run_store is not None and not is_test_run:
            current_status: str | None = None
            with contextlib.suppress(Exception):
                current_status = await self._run_store.get_status(tenant_id, run_id)
            if current_status in (
                WorkflowRunStatus.CANCELLED.value,
                WorkflowRunStatus.PAUSED.value,
            ):
                _log.info(
                    "workflow_run_start_skipped_terminal_or_paused",
                    run_id=run_id,
                    status=current_status,
                )
                return
            with contextlib.suppress(Exception):
                await self._run_store.update_status(
                    run_id, WorkflowRunStatus.RUNNING, tenant_id=tenant_id
                )
        try:
            final_state = await compiled.ainvoke(initial_state, config)
        except WorkflowCancelled:
            # Operator stopped the run — it's already marked CANCELLED via the API;
            # confirm the terminal status and do NOT mark it failed.
            _log.info("workflow_run_cancelled", run_id=run_id)
            if self._run_store is not None:
                with contextlib.suppress(Exception):
                    await self._run_store.update_status(
                        run_id, WorkflowRunStatus.CANCELLED, tenant_id=tenant_id
                    )
            return
        except WorkflowPaused:
            # Operator paused the run — leave it PAUSED (set via the API) with its
            # completed steps persisted, so Resume can continue from here.
            _log.info("workflow_run_paused", run_id=run_id)
            return
        except Exception as exc:
            _log.error(
                "workflow_run_failed_worker", run_id=run_id, error=repr(exc), exc_info=True
            )
            if self._run_store is not None:
                await self._run_store.update_status(
                    run_id, WorkflowRunStatus.FAILED, tenant_id=tenant_id, error=str(exc)
                )
            return
        # Finalize the run-level status. The graph leaves a successful run at its
        # initial PENDING status (step nodes only emit step_outputs); a halting
        # step sets WAITING_HITL / PAUSED (or FAILED). Preserve those halts and
        # otherwise mark the run COMPLETE so it reaches a terminal state.
        await self._finalize_status(run_id, tenant_id, final_state)

    async def execute_resume_fresh(
        self,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        *,
        step_id: str,
        action: str,
        actor_id: str,
        note: str | None = None,
        form_data: dict[str, Any] | None = None,
    ) -> None:
        """Resume a HITL-suspended run in a *separate* process from the one that
        paused it (gap #2: cross-process workflow HITL).

        The run originally suspended in an out-of-process Celery worker whose
        per-process LangGraph checkpointer this (API- or worker-)process cannot
        read — so there is no shared checkpoint to ``aupdate_state`` + re-invoke.
        Instead, mirror :meth:`execute_fresh`: reconstruct the initial
        ``WorkflowState`` from the persisted run record, then seed the reviewer's
        decision onto the HITL fields so that when the graph re-runs, the HITL
        step short-circuits into ``_process_decision`` (it detects the resume via
        ``hitl_request_id == step.id``) instead of creating a *new* approval and
        re-suspending. The run then advances to a terminal state and is persisted.

        A distinct checkpointer ``thread_id`` (``<run_id>::resume``) is used so a
        stale END checkpoint from the fresh run (if this happens to be the same
        worker process) cannot interfere with the reconstructed invocation.
        """
        definition = await self._load_definition(workflow_id, tenant_id)
        inputs: dict[str, Any] = {}
        labels: dict[str, str] | None = None
        if self._run_store is not None:
            record = await self._run_store.get(tenant_id, run_id)
            if record:
                inputs = record.get("inputs") or {}
                labels = record.get("labels") or None
        initial_state = self._build_initial_state(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            definition=definition,
            inputs=inputs,
            labels=labels,
        )
        # Seed the reviewer's decision. HITLStepNode.execute() resumes (rather
        # than re-suspends) when ``hitl_request_id == step.id``.
        initial_state["hitl_request_id"] = step_id
        initial_state["hitl_action"] = action
        initial_state["hitl_note"] = note
        initial_state["hitl_reviewer"] = actor_id
        initial_state["hitl_form_data"] = form_data
        initial_state["status"] = WorkflowRunStatus.RUNNING

        compiled = self._compiler.compile(definition)
        config = {"configurable": {"thread_id": f"{run_id}::resume"}}
        try:
            final_state = await compiled.ainvoke(initial_state, config)
        except Exception as exc:
            _log.error(
                "workflow_resume_failed_worker", run_id=run_id, error=repr(exc), exc_info=True
            )
            if self._run_store is not None:
                await self._run_store.update_status(
                    run_id, WorkflowRunStatus.FAILED, tenant_id=tenant_id, error=str(exc)
                )
            return
        await self._finalize_status(run_id, tenant_id, final_state)

    async def _finalize_status(
        self, run_id: str, tenant_id: str, final_state: Any
    ) -> None:
        if self._run_store is None:
            return
        raw_status = (final_state or {}).get("status") if isinstance(final_state, dict) else None
        halted = {
            WorkflowRunStatus.WAITING_HITL,
            WorkflowRunStatus.PAUSED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }
        status = raw_status if raw_status in halted else WorkflowRunStatus.COMPLETE
        outputs = (
            final_state.get("outputs") if isinstance(final_state, dict) else None
        ) or None
        _fs = final_state if isinstance(final_state, dict) else {}
        await self._run_store.update_status(
            run_id,
            status,
            tenant_id=tenant_id,
            error=_fs.get("error"),
            outputs=outputs,
            # Persist the run's accumulated telemetry so the run row (and UI) show
            # real cost/tokens/duration instead of 0 — the graph accumulates these
            # in state but never writes them to the run row itself.
            cost_usd=_fs.get("cost_usd"),
            tokens_used=_fs.get("tokens_used"),
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
        """Resume a WAITING_HITL run after a reviewer decision.

        Two paths, chosen by whether Celery is wired:

        * **Celery (cross-process, gap #2):** the run suspended in an
          out-of-process worker whose per-process checkpointer this process
          cannot read, so ``aupdate_state`` + re-invoke against a local
          checkpoint would apply the decision to the wrong (empty) checkpoint.
          Instead dispatch the decision itself to a worker, which reconstructs
          the run from the persisted run record and advances it to terminal
          (:meth:`execute_resume_fresh`). No shared checkpointer required.
        * **In-process (``_celery`` is ``None``, e.g. the in-process e2e):** the
          suspended checkpoint lives in this process, so update it and re-invoke
          directly — the original WS-3/WS-4 behaviour, preserved unchanged.
        """
        workflow_id = (
            await self._run_store.get_workflow_id(run_id, tenant_id)
            if self._run_store
            else ""
        )

        if self._celery:
            from app.workflow.celery_tasks import execute_workflow_run

            execute_workflow_run.apply_async(
                args=[run_id, workflow_id, tenant_id],
                kwargs={
                    "resume": True,
                    "hitl_decision": {
                        "step_id": step_id,
                        "action": action,
                        "actor_id": actor_id,
                        "note": note,
                        "form_data": form_data,
                    },
                },
                queue=f"workflows.{await self._get_plan_tier(tenant_id)}",
            )
            return

        # In-process resume: the checkpoint is local to this process.
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
        current = await compiled.aget_state(config)
        if hasattr(current, "values"):
            state = dict(current.values)
            state.update(state_update)
            final_state = await compiled.ainvoke(state, config)
            # A resumed in-process run must also reach a terminal run-level
            # status (the graph itself only emits step_outputs).
            await self._finalize_status(run_id, tenant_id, final_state)

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
