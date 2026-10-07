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
import copy
import json
import uuid
from typing import Any

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


class WorkflowEngineUnavailableError(RuntimeError):
    """The durable run path (persistent run store) is not wired, so a request
    that needs it (idempotent trigger, retry, legacy /run) cannot be honoured.
    Routers map this to HTTP 503 rather than pretending to run anything."""


# Run statuses after which a completion callback is delivered.
_CALLBACK_STATUSES = {
    WorkflowRunStatus.COMPLETE,
    WorkflowRunStatus.FAILED,
    WorkflowRunStatus.CANCELLED,
    WorkflowRunStatus.TIMED_OUT,
}


_PLAN_QUEUES = frozenset({"free", "starter", "professional", "enterprise"})


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

        ``idempotency_key`` is now enforced (it used to be dropped into run
        metadata that was never even persisted): a repeat trigger with the same
        key for the same tenant+workflow returns the ORIGINAL run.
        ``callback_url`` is validated here and POSTed on the terminal status.
        """
        metadata: dict[str, Any] = {}
        if callback_url:
            metadata["callback_url"] = callback_url
        run_id = await self.run(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=inputs,
            is_test_run=dry_run,
            run_metadata=metadata or None,
            idempotency_key=idempotency_key,
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
        idempotency_key: str | None = None,
        seed_from_run_id: str | None = None,
    ) -> str:
        """Trigger a new workflow run. Returns run_id immediately.

        ``idempotency_key``: with a persistent run store, a second call with the
        same (tenant, workflow, key) returns the existing run's id and does NOT
        start another execution (the DB unique index arbitrates, so this holds
        across replicas).

        ``seed_from_run_id``: copy that run's COMPLETE step results into the new
        run before it is dispatched, so the engine skips them (used by retry).
        """
        if idempotency_key and self._run_store is None:
            # Without a persistent store there is nothing to dedupe against;
            # silently ignoring the key would start duplicate runs.
            raise WorkflowEngineUnavailableError(
                "idempotency_key requires the persistent workflow run store"
            )
        callback_url = (run_metadata or {}).get("callback_url")
        if callback_url:
            from app.workflow.callbacks import validate_callback_url

            try:
                validate_callback_url(str(callback_url))
            except ValueError as exc:  # SSRFError subclasses ValueError
                raise WorkflowValidationError(f"callback_url rejected: {exc}") from exc

        # 1. Payload size guard
        payload_bytes = len(json.dumps(inputs, default=str).encode())
        if payload_bytes > MAX_TRIGGER_PAYLOAD_BYTES:
            raise WorkflowValidationError(
                f"Trigger payload too large: {payload_bytes} bytes > 1MB limit"
            )

        # 2. Load definition (from store or in-memory for tests). A published
        # workflow runs its recorded version snapshot and the run pins it (WF-30).
        definition, pinned_version = await self._load_live_definition(workflow_id, tenant_id)
        if pinned_version is not None:
            run_metadata = {**(run_metadata or {}), "definition_version": pinned_version}

        # 3. Apply trigger_transform
        if definition.trigger_transform and trigger_payload:
            base_state: dict[str, Any] = {"raw_trigger": trigger_payload}
            transformed = self._ctx.resolve_dict(definition.trigger_transform, base_state)
            inputs = {**inputs, **transformed}

        # 4. Apply declared defaults, then validate (required / enum)
        inputs = self.apply_input_defaults(definition, inputs)
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
            extra: dict[str, Any] = {}
            # Only pass the newer kwargs when set, so minimal stores that predate
            # them keep working.
            if run_metadata:
                extra["run_metadata"] = run_metadata
            if idempotency_key:
                extra["idempotency_key"] = idempotency_key
            owner = await self._run_store.create(
                run_id=run_id,
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                trigger_type=trigger_type,
                trigger_payload=trigger_payload,
                inputs=inputs,
                labels=initial_state["labels"],
                is_test_run=is_test_run,
                **extra,
            )
            if idempotency_key and owner and str(owner) != run_id:
                # Duplicate trigger: the key already belongs to an earlier run.
                # Return it and do NOT execute/dispatch a second time.
                _log.info(
                    "workflow_run_idempotent_replay",
                    workflow_id=workflow_id,
                    existing_run_id=str(owner),
                )
                return str(owner)
            if seed_from_run_id and hasattr(self._run_store, "copy_completed_step_results"):
                # Must happen BEFORE dispatch so the worker sees the seeded steps.
                copied = await self._run_store.copy_completed_step_results(
                    tenant_id, seed_from_run_id, run_id
                )
                _log.info(
                    "workflow_retry_seeded", run_id=run_id, source=seed_from_run_id, steps=copied
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

    async def record_rejected_run(
        self,
        *,
        workflow_id: str,
        tenant_id: str,
        trigger_type: str,
        trigger_payload: dict[str, Any] | None,
        error: str,
    ) -> str | None:
        """Persist a FAILED run for a trigger whose inputs were refused.

        An API / webhook caller gets the refusal as a 422; a schedule has no
        caller, so without this the occurrence vanished into a log line. Returns
        the run id, or None without a persistent store.
        """
        if self._run_store is None:
            return None
        run_id = str(uuid.uuid4())
        await self._run_store.create(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            trigger_type=trigger_type,
            trigger_payload=trigger_payload,
            inputs={},
        )
        await self._run_store.update_status(
            run_id, WorkflowRunStatus.FAILED, tenant_id=tenant_id, error=error
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
            await self._finalize_status(
                run_id, initial_state["tenant_id"], final_state, definition=definition
            )
        except Exception as exc:
            _log.error(
                "workflow_run_failed_inline", run_id=run_id, error=repr(exc), exc_info=True
            )
            # tenant_id is keyword-only-required (RLS-scoped); read it from the
            # run state so the failure update targets the right tenant.
            await self._fail_run(
                run_id, initial_state["tenant_id"], exc, initial_state, definition
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
        definition = await self._load_run_definition(run_id, workflow_id, tenant_id)
        inputs: dict[str, Any] = {}
        labels: dict[str, str] | None = None
        run_metadata: dict[str, Any] | None = None
        if self._run_store is not None:
            record = await self._run_store.get(tenant_id, run_id)
            if record:
                inputs = record.get("inputs") or {}
                labels = record.get("labels") or None
                # The worker previously rebuilt state WITHOUT run_metadata, so a
                # trigger's callback_url never reached the process that finishes
                # the run.
                run_metadata = record.get("run_metadata") or None
        # A run persisted before defaults were merged at trigger time still
        # gets them here (idempotent for runs persisted since).
        inputs = self.apply_input_defaults(definition, inputs)
        initial_state = self._build_initial_state(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            definition=definition,
            inputs=inputs,
            run_metadata=run_metadata,
            is_test_run=is_test_run,
            mock_overrides=mock_overrides,
            labels=labels,
        )
        compiled = self._compiler.compile(definition)
        config = {"configurable": {"thread_id": run_id}}
        # The state above is rebuilt from Postgres; a checkpoint this worker
        # process kept from an earlier attempt of the run would be MERGED into it
        # by the reducers (cost/tokens added twice, stale status).
        await self._drop_checkpoint(compiled, run_id)
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
            # Also a crash-redelivered task (acks_late) for a run that already
            # finished or suspended at an approval: re-invoking it would re-run
            # the approval step (a duplicate approval) or replay finished work
            # and fire the completion callback twice. The HITL resume uses
            # execute_resume_fresh, and a timer wake re-queues as 'pending'.
            if current_status in (
                WorkflowRunStatus.CANCELLED.value,
                WorkflowRunStatus.PAUSED.value,
                WorkflowRunStatus.COMPLETE.value,
                WorkflowRunStatus.FAILED.value,
                WorkflowRunStatus.TIMED_OUT.value,
                WorkflowRunStatus.WAITING_HITL.value,
            ):
                _log.info(
                    "workflow_run_start_skipped_terminal_or_paused",
                    run_id=run_id,
                    status=current_status,
                )
                return
            started = True
            with contextlib.suppress(Exception):
                # Compare-and-set: a cancel / pause landing between the read
                # above and this write wins.
                started = await self._run_store.update_status(
                    run_id,
                    WorkflowRunStatus.RUNNING,
                    tenant_id=tenant_id,
                    only_from=(WorkflowRunStatus.PENDING.value, WorkflowRunStatus.RUNNING.value),
                )
            if not started:
                _log.info("workflow_run_start_skipped_status_changed", run_id=run_id)
                return
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
            self._fire_callback(WorkflowRunStatus.CANCELLED, initial_state, definition)
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
            await self._fail_run(run_id, tenant_id, exc, initial_state, definition)
            return
        # Finalize the run-level status. The graph leaves a successful run at its
        # initial PENDING status (step nodes only emit step_outputs); a halting
        # step sets WAITING_HITL / PAUSED (or FAILED). Preserve those halts and
        # otherwise mark the run COMPLETE so it reaches a terminal state.
        await self._finalize_status(run_id, tenant_id, final_state, definition=definition)

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
        definition = await self._load_run_definition(run_id, workflow_id, tenant_id)
        inputs: dict[str, Any] = {}
        labels: dict[str, str] | None = None
        run_metadata: dict[str, Any] | None = None
        if self._run_store is not None:
            record = await self._run_store.get(tenant_id, run_id)
            if record:
                inputs = record.get("inputs") or {}
                labels = record.get("labels") or None
                # The worker previously rebuilt state WITHOUT run_metadata, so a
                # trigger's callback_url never reached the process that finishes
                # the run.
                run_metadata = record.get("run_metadata") or None
        # A run persisted before defaults were merged at trigger time still
        # gets them here (idempotent for runs persisted since).
        inputs = self.apply_input_defaults(definition, inputs)
        initial_state = self._build_initial_state(
            run_id=run_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            definition=definition,
            inputs=inputs,
            run_metadata=run_metadata,
            labels=labels,
        )
        # A run the HITL SLA sweep paused at THIS approval step (timeout_action
        # "pause") is resumed by a decision on that still-pending approval.
        sla_paused_here = (
            str(((run_metadata or {}).get("hitl_timeout_pause") or {}).get("step_id") or "")
            == step_id
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
        # Compare-and-set the run back to RUNNING. A run cancelled (or paused /
        # finished) after the decision was recorded must not be resumed: the
        # resume used to run anyway and turn WorkflowCancelled into FAILED.
        # ('running'/'pending' are allowed: a reviewer can decide before the
        # suspending worker has persisted waiting_hitl.)
        if self._run_store is not None:
            resumed = await self._run_store.update_status(
                run_id,
                WorkflowRunStatus.RUNNING,
                tenant_id=tenant_id,
                only_from=(
                    WorkflowRunStatus.WAITING_HITL.value,
                    WorkflowRunStatus.RUNNING.value,
                    WorkflowRunStatus.PENDING.value,
                    *((WorkflowRunStatus.PAUSED.value,) if sla_paused_here else ()),
                ),
            )
            if not resumed:
                _log.info("workflow_resume_skipped_run_not_waiting", run_id=run_id)
                return
        # A second resume of the run in this process must not merge into the
        # first resume's END checkpoint (it re-added the replayed cost).
        await self._drop_checkpoint(compiled, f"{run_id}::resume")
        try:
            final_state = await compiled.ainvoke(initial_state, config)
        except WorkflowCancelled:
            _log.info("workflow_resume_cancelled", run_id=run_id)
            return  # already CANCELLED via the API; never mark it failed
        except WorkflowPaused:
            _log.info("workflow_resume_paused", run_id=run_id)
            return
        except Exception as exc:
            _log.error(
                "workflow_resume_failed_worker", run_id=run_id, error=repr(exc), exc_info=True
            )
            await self._fail_run(run_id, tenant_id, exc, initial_state, definition)
            return
        await self._finalize_status(run_id, tenant_id, final_state, definition=definition)

    @staticmethod
    async def _drop_checkpoint(compiled: Any, thread_id: str) -> None:
        """Forget this process's checkpoint for ``thread_id`` (state is rebuilt
        from the database by the caller)."""
        deleter = getattr(compiled, "adelete_thread", None)
        if deleter is None:
            return
        try:
            await deleter(thread_id)
        except Exception as exc:  # a stale checkpoint is worse, but never fatal
            _log.warning("workflow_checkpoint_drop_failed", thread_id=thread_id, error=str(exc))

    async def _fail_run(
        self,
        run_id: str,
        tenant_id: str,
        exc: BaseException,
        state: Any,
        definition: WorkflowDefinition | None,
    ) -> None:
        """Mark the run FAILED with the failing step's error and id."""
        error, error_step_id = await self._failure_details(run_id, tenant_id, exc)
        if self._run_store is not None:
            await self._run_store.update_status(
                run_id,
                WorkflowRunStatus.FAILED,
                tenant_id=tenant_id,
                error=error,
                error_step_id=error_step_id,
            )
        self._fire_callback(WorkflowRunStatus.FAILED, {**state, "error": error}, definition)

    async def _failure_details(
        self, run_id: str, tenant_id: str, exc: BaseException
    ) -> tuple[str, str | None]:
        """(error, error_step_id) for a run that raised ``exc``.

        An aborting step tags its exception with ``workflow_step_id``; otherwise
        the failed step row supplies the step (and the error, when the exception
        has no message) — a failed run must never show an empty error.
        """
        error = str(exc)
        step_id = getattr(exc, "workflow_step_id", None)
        if step_id is None or not error:
            row_error, row_step = await self._failed_step(run_id, tenant_id)
            step_id = step_id or row_step
            error = error or row_error or ""
        return error or type(exc).__name__, step_id

    async def _failed_step(self, run_id: str, tenant_id: str) -> tuple[str | None, str | None]:
        """The error and id of the most recent failed step row of the run."""
        lister = getattr(self._run_store, "list_step_results", None)
        if lister is None:
            return None, None
        try:
            rows = await lister(tenant_id, run_id)
        except Exception as exc:
            _log.warning("workflow_failed_step_lookup_failed", run_id=run_id, error=str(exc))
            return None, None
        for row in reversed(list(rows or [])):
            if str(row.get("status") or "") == "failed" and row.get("error"):
                return str(row["error"]), str(row.get("step_id") or "") or None
        return None, None

    async def _finalize_status(
        self,
        run_id: str,
        tenant_id: str,
        final_state: Any,
        *,
        definition: WorkflowDefinition | None = None,
    ) -> None:
        if self._run_store is None:
            return
        raw_status = (final_state or {}).get("status") if isinstance(final_state, dict) else None
        halted = {
            WorkflowRunStatus.WAITING_HITL,
            WorkflowRunStatus.WAITING_TIMER,
            WorkflowRunStatus.PAUSED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }
        status = raw_status if raw_status in halted else WorkflowRunStatus.COMPLETE
        outputs = (
            final_state.get("outputs") if isinstance(final_state, dict) else None
        ) or None
        _fs = final_state if isinstance(final_state, dict) else {}
        error = _fs.get("error")
        error_step_id = _fs.get("error_step_id")
        if status in (WorkflowRunStatus.FAILED, WorkflowRunStatus.PAUSED) and not error:
            # A failed/paused run must name its failing step: fall back to the
            # failed step row when the state carries no error.
            row_error, row_step = await self._failed_step(run_id, tenant_id)
            error, error_step_id = row_error, error_step_id or row_step
        await self._run_store.update_status(
            run_id,
            status,
            tenant_id=tenant_id,
            error=error,
            error_step_id=error_step_id,
            outputs=outputs,
            # Persist the run's accumulated telemetry so the run row (and UI) show
            # real cost/tokens/duration instead of 0 — the graph accumulates these
            # in state but never writes them to the run row itself.
            cost_usd=_fs.get("cost_usd"),
            tokens_used=_fs.get("tokens_used"),
        )
        # Status is persisted first; the callback is handed off afterwards and
        # can never delay or fail the run's completion.
        self._fire_callback(status, {**_fs, "run_id": run_id}, definition)

    def _fire_callback(
        self,
        status: Any,
        state: Any,
        definition: WorkflowDefinition | None,
    ) -> None:
        """Deliver the run-completion callback for a terminal ``status``.

        Old bug: :meth:`send_callback` existed but nothing ever called it, and a
        trigger's ``callback_url`` was only put in never-persisted run metadata,
        so no caller was ever notified. Now every terminal transition (inline,
        worker, HITL resume, cancel, failure) lands here. Sandbox/test runs never
        call out (no side effects).
        """
        try:
            if status not in _CALLBACK_STATUSES:
                return
            st = state if isinstance(state, dict) else {}
            if st.get("is_test_run"):
                return
            url = self._resolve_callback_url(status, st, definition)
            if not url:
                return
            from app.workflow.callbacks import build_payload, dispatch_callback

            workflow_id = str(st.get("workflow_id") or "")
            payload = build_payload(
                run_id=str(st.get("run_id") or ""),
                workflow_id=workflow_id,
                status=str(getattr(status, "value", status)),
                state=st,
            )
            dispatch_callback(
                url,
                payload,
                tenant_id=str(st.get("tenant_id") or ""),
                workflow_id=workflow_id,
                celery_app=self._celery,
            )
        except Exception as exc:  # never let a callback problem touch the run
            _log.error("workflow_callback_schedule_failed", error=str(exc))

    def _resolve_callback_url(
        self, status: Any, state: dict[str, Any], definition: WorkflowDefinition | None
    ) -> str | None:
        """Trigger-supplied ``callback_url`` wins (the caller asked for exactly
        this run); otherwise the DSL ``callback`` block, honouring its
        ``on_failure`` flag."""
        meta_url = (state.get("run_metadata") or {}).get("callback_url")
        if meta_url:
            return str(meta_url)
        cb = definition.callback if definition is not None else None
        if cb is None or not cb.url:
            return None
        if status == WorkflowRunStatus.FAILED and not cb.on_failure:
            return None
        return str(self._ctx.resolve(cb.url, state))

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
        definition = await self._load_run_definition(run_id, workflow_id, tenant_id)
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
            await self._finalize_status(
                run_id, tenant_id, final_state, definition=definition
            )

    async def _load_live_definition(
        self, workflow_id: str, tenant_id: str
    ) -> tuple[WorkflowDefinition, str | None]:
        """Definition a NEW run executes, plus the published version it pins."""
        getter = getattr(self._run_store, "get_live_definition", None)
        if getter is None:
            return await self._load_definition(workflow_id, tenant_id), None
        data, version = await getter(workflow_id, tenant_id)
        return self._parse_definition(data, workflow_id), version

    async def _load_run_definition(
        self, run_id: str, workflow_id: str, tenant_id: str
    ) -> WorkflowDefinition:
        """Definition an EXISTING run executes: the version it pinned at start
        (WF-30), never whatever is live now."""
        getter = getattr(self._run_store, "get_run_definition", None)
        if getter is None:
            return await self._load_definition(workflow_id, tenant_id)
        return self._parse_definition(
            await getter(tenant_id, run_id, workflow_id), workflow_id
        )

    @staticmethod
    def _parse_definition(data: dict[str, Any], workflow_id: str) -> WorkflowDefinition:
        definition = WorkflowDefinition.from_json(data)
        if not definition.id:
            definition.id = workflow_id
        return definition

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
    def apply_input_defaults(
        definition: WorkflowDefinition, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """``inputs`` with every declared default filled in for an input that was
        not supplied (absent or null). A supplied value always wins.

        Old bug: defaults were only used to skip the required check and never
        merged, so a schedule / webhook / ``{}`` run rendered ``{{inputs.x}}`` as
        ``''`` (the resolver's value for a missing input).
        """
        merged = dict(inputs or {})
        for name, input_def in definition.inputs.items():
            if merged.get(name) is None and input_def.default is not None:
                merged[name] = copy.deepcopy(input_def.default)
        return merged

    @staticmethod
    def _validate_inputs(definition: WorkflowDefinition, inputs: dict[str, Any]) -> None:
        """Validate inputs AFTER :meth:`apply_input_defaults`."""
        for name, input_def in definition.inputs.items():
            if input_def.required and inputs.get(name) is None:
                raise WorkflowValidationError(
                    f"Required input {name!r} is missing and has no default"
                )
            if name in inputs and input_def.enum and inputs[name] not in input_def.enum:
                raise WorkflowValidationError(
                    f"Input {name!r} value {inputs[name]!r} not in enum {input_def.enum}"
                )

    async def _get_plan_tier(self, tenant_id: str) -> str:
        """Get plan tier for Celery queue routing."""
        # TenantService exposes get_tenant() → a profile dict. This called a
        # non-existent get() and read ``.plan`` off the result; the AttributeError
        # was swallowed, so EVERY workflow run was routed to workflows.free.
        tenant_service = self._services.get("tenant_service")
        getter = getattr(tenant_service, "get_tenant", None)
        if getter is None:
            return "free"
        try:
            tenant = await getter(tenant_id)
        except Exception as exc:
            # Unknown plan → the most restrictive queue, never a paid one.
            _log.warning("workflow_plan_lookup_failed", tenant_id=tenant_id, error=str(exc))
            return "free"
        plan = tenant.get("plan") if isinstance(tenant, dict) else getattr(tenant, "plan", None)
        plan = str(getattr(plan, "value", plan) or "free").lower()
        return plan if plan in _PLAN_QUEUES else "free"

    async def send_callback(
        self,
        definition: WorkflowDefinition,
        state: WorkflowState,
    ) -> None:
        """Schedule the completion callback for ``state``'s status (non-blocking).

        Kept for API compatibility; the engine itself calls :meth:`_fire_callback`
        on every terminal transition. Delivery is SSRF-guarded, signed, retried
        and never awaited here (see ``app.workflow.callbacks``).
        """
        self._fire_callback(state.get("status"), dict(state), definition)
