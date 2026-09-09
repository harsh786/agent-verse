"""Workflow Celery tasks — execution, HITL resume, periodic maintenance."""

# mypy: disable-error-code="misc,no-untyped-def,type-arg,union-attr"
from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)


# Cached DB-backed runner built once per Celery worker process (the FastAPI
# lifespan does not run in a worker, so app.state holds only the in-memory
# fallback runner — MemorySaver + mock tools + no persistence).
_WORKER_RUNNER: Any = None


def _build_worker_runner() -> Any:
    """Construct a DB-backed WorkflowRunner from the worker's fresh session
    factory, mirroring the FastAPI lifespan wiring. Cached per process."""
    global _WORKER_RUNNER
    if _WORKER_RUNNER is not None:
        return _WORKER_RUNNER
    from app.db.session import get_session_factory
    from app.scaling.tasks import _WORKER_CHECKPOINTER
    from app.workflow.compiler import WorkflowCompiler
    from app.workflow.context import ContextResolver
    from app.workflow.hitl_extension import HITLWorkflowGateway
    from app.workflow.run_store import PostgresWorkflowRunStore
    from app.workflow.runner import WorkflowRunner

    db_factory = get_session_factory()
    run_store = PostgresWorkflowRunStore(db_factory)
    # WS-3: reuse the API server's hitl_workflow_gateway when this worker
    # shares process state with it (e.g. tests, single-process deployments);
    # otherwise fall back to a worker-local instance so HITLStepNode at least
    # gets a working gateway instead of silently skipping approval creation.
    hitl_workflow_gateway: Any = None
    try:
        from app.main import app as _fastapi_app  # type: ignore[import]

        hitl_workflow_gateway = getattr(_fastapi_app.state, "hitl_workflow_gateway", None)
    except Exception:
        hitl_workflow_gateway = None
    if hitl_workflow_gateway is None:
        hitl_workflow_gateway = HITLWorkflowGateway()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        checkpointer=_WORKER_CHECKPOINTER,
        run_store=run_store,
        hitl_workflow_gateway=hitl_workflow_gateway,
    )
    _WORKER_RUNNER = WorkflowRunner(compiler=compiler, run_store=run_store, celery_app=celery_app)
    return _WORKER_RUNNER


def _get_runner() -> Any:
    """Return a DB-backed workflow runner.

    In-process (the FastAPI lifespan ran) the DB-backed runner is on
    ``app.state``. In a Celery worker the lifespan never runs, so ``app.state``
    carries only the in-memory fallback — detect that (no ``_run_store``) and
    build a worker-local DB-backed runner instead, so worker-executed runs
    actually persist and run real steps rather than MemorySaver + mock tools.
    """
    try:
        from app.main import app as fastapi_app  # type: ignore[import]

        state_runner = getattr(fastapi_app.state, "workflow_runner", None)
        if state_runner is not None and getattr(state_runner, "_run_store", None) is not None:
            return state_runner
    except Exception:
        state_runner = None
    try:
        return _build_worker_runner()
    except Exception as exc:  # pragma: no cover - defensive
        _log.error("worker_runner_build_failed", error=str(exc))
        return state_runner


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a Celery task (sync context)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=7200)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@celery_app.task(
    name="workflow.execute_workflow_run",
    bind=True,
    acks_late=True,
    max_retries=0,
)
def execute_workflow_run(
    self: Any,
    run_id: str,
    workflow_id: str,
    tenant_id: str,
    is_test_run: bool = False,
    mock_overrides: dict | None = None,
    resume: bool = False,
) -> None:
    """Execute a workflow run (or resume after HITL)."""
    runner = _get_runner()
    if runner is None:
        _log.error("workflow_runner_unavailable", run_id=run_id)
        return

    async def _execute() -> None:
        if resume:
            # State was already updated via aupdate_state; just re-invoke
            definition = await runner._load_definition(workflow_id, tenant_id)
            compiled = runner._compiler.compile(definition)
            config = {"configurable": {"thread_id": run_id}}
            current = await compiled.aget_state(config)
            if hasattr(current, "values"):
                await compiled.ainvoke(dict(current.values), config)
        else:
            # Fresh run — state already in checkpointer from runner.run()
            definition = await runner._load_definition(workflow_id, tenant_id)
            compiled = runner._compiler.compile(definition)
            config = {"configurable": {"thread_id": run_id}}
            current = await compiled.aget_state(config)
            if hasattr(current, "values"):
                await compiled.ainvoke(dict(current.values), config)

    try:
        _run_async(_execute())
    except Exception as exc:
        _log.error("execute_workflow_run_failed", run_id=run_id, error=str(exc))
        raise


@celery_app.task(name="workflow.check_hitl_escalations")
def check_hitl_escalations() -> None:
    """Run every 15 minutes to auto-escalate overdue HITL requests."""

    async def _check() -> None:
        try:
            from app.main import app as fastapi_app  # type: ignore[import]

            gateway = getattr(fastapi_app.state, "hitl_workflow_gateway", None)
            if gateway:
                await gateway.check_and_escalate_overdue()
        except Exception as exc:
            _log.warning("hitl_escalation_check_failed", error=str(exc))

    _run_async(_check())


@celery_app.task(name="workflow.retry_dead_letter_webhooks")
def retry_dead_letter_webhooks() -> None:
    """Run every 5 minutes to retry failed webhook deliveries."""

    async def _retry() -> None:
        try:
            runner = _get_runner()
            run_store = getattr(runner, "_run_store", None) if runner else None
            if run_store and hasattr(run_store, "get_retryable_webhooks"):
                events = await run_store.get_retryable_webhooks(max_attempts=3)
                for event in events:
                    await runner.run(
                        workflow_id=event["workflow_id"],
                        tenant_id=event["tenant_id"],
                        inputs=event["payload"],
                        trigger_type="webhook",
                    )
        except Exception as exc:
            _log.warning("dlq_retry_failed", error=str(exc))

    _run_async(_retry())


@celery_app.task(name="workflow.cleanup_expired_runs")
def cleanup_expired_runs() -> None:
    """Run daily to delete runs older than workflow's run_retention_days."""

    async def _cleanup() -> None:
        try:
            runner = _get_runner()
            run_store = getattr(runner, "_run_store", None) if runner else None
            if run_store and hasattr(run_store, "delete_expired_runs"):
                deleted = await run_store.delete_expired_runs()
                _log.info("cleanup_expired_runs_done", deleted=deleted)
        except Exception as exc:
            _log.warning("cleanup_expired_runs_failed", error=str(exc))

    _run_async(_cleanup())


# ── Register Beat schedules ───────────────────────────────────────────────────
# These are applied to celery_app.conf in app/scaling/celery_app.py
# The Beat schedule entries below must also be added to the existing beat_schedule
# dict in celery_app.py:
#
# "workflow-hitl-escalation-check": {
#     "task": "workflow.check_hitl_escalations",
#     "schedule": 900,  # every 15 minutes
# },
# "workflow-webhook-dlq-retry": {
#     "task": "workflow.retry_dead_letter_webhooks",
#     "schedule": 300,  # every 5 minutes
# },
# "workflow-cleanup-expired-runs": {
#     "task": "workflow.cleanup_expired_runs",
#     "schedule": 86400,  # daily
# },
