"""Workflow Celery tasks — execution, HITL resume, periodic maintenance."""

# mypy: disable-error-code="misc,no-untyped-def,type-arg,union-attr"
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

_log = get_logger(__name__)

# A scheduled cron occurrence is only fired when it came due within this many
# seconds of the scan. The beat scan runs every 60s, so this tolerates a couple
# of missed scans (beat restart / brief outage) without replaying occurrences
# that were due long ago (e.g. while the workflow was unpublished).
_SCHEDULE_GRACE_SECONDS = 150


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
    # Cross-process HITL (gap #2): give the worker's gateway the same durable,
    # RLS-scoped Postgres approval store the API uses, so a pending approval the
    # worker creates when a run suspends at a HITL step is visible to the API's
    # /approvals endpoints (and a decision the API writes is visible here). The
    # FastAPI lifespan — which normally wires this — never runs in a worker.
    if getattr(hitl_workflow_gateway, "_approval_store", None) is None:
        from app.workflow.approval_store import PostgresWorkflowApprovalStore

        hitl_workflow_gateway._approval_store = PostgresWorkflowApprovalStore(db_factory)
    # Wire the REAL services so worker-executed steps use the configured model
    # (not FakeProvider) and OCR/RAG steps actually work — the FastAPI lifespan
    # that normally wires these never runs in a Celery worker. NOTE: step nodes
    # receive services from the COMPILER (node_class(step, ctx, **compiler._services)),
    # so these must go on the compiler, not the runner.
    from app.ocr.engine import OcrEngine
    from app.providers.registry import resolve_provider

    _wf_provider = resolve_provider()
    _wf_knowledge: Any = None
    try:
        from app.rag.store import KnowledgeStore

        _wf_knowledge = KnowledgeStore(db_factory)
    except Exception as _ks_exc:
        _log.warning("worker_runner_knowledge_store_unavailable", error=str(_ks_exc)[:120])
    # Wire a real MCP client so workflow tool steps dispatch actual connectors
    # (Telegram, Slack, HTTP tools, …) instead of returning a mock. Mirrors the
    # goal worker's MCP wiring; the FastAPI lifespan never runs in a Celery worker.
    _wf_mcp_client: Any = None
    try:
        import os as _os

        import redis.asyncio as _aioredis_wf

        from app.mcp.client import MCPClient
        from app.mcp.registry import MCPRegistry
        from app.mcp.servers.registry_wiring import get_builtin_server_configs
        from app.providers.vault import (
            RedisConnectorSecretStore,
            get_vault,
            resolve_connector_secret_ref_for_tenant,
        )

        for _bcfg in get_builtin_server_configs():
            MCPRegistry.register_builtin_handler(_bcfg["server_id"], _bcfg["handler"])
        _wf_redis = _aioredis_wf.from_url(
            _os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
        )
        _wf_secret_store = RedisConnectorSecretStore(redis=_wf_redis, vault=get_vault())

        async def _wf_resolve_secret(ref: str, tenant_ctx: Any = None) -> str | None:
            return await resolve_connector_secret_ref_for_tenant(
                ref, store=_wf_secret_store, tenant_ctx=tenant_ctx
            )

        _wf_mcp_client = MCPClient(
            MCPRegistry(_wf_redis), secret_resolver=_wf_resolve_secret, redis=_wf_redis
        )
    except Exception as _mcp_exc:
        _log.warning("worker_runner_mcp_client_unavailable", error=str(_mcp_exc)[:120])
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        checkpointer=_WORKER_CHECKPOINTER,
        run_store=run_store,
        hitl_workflow_gateway=hitl_workflow_gateway,
        llm_provider=_wf_provider,
        provider=_wf_provider,
        ocr_engine=OcrEngine(),
        knowledge_store=_wf_knowledge,
        mcp_client=_wf_mcp_client,
    )
    _WORKER_RUNNER = WorkflowRunner(
        compiler=compiler,
        run_store=run_store,
        celery_app=celery_app,
        hitl_workflow_gateway=hitl_workflow_gateway,
    )
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
    hitl_decision: dict | None = None,
) -> None:
    """Execute a workflow run (or resume after HITL)."""
    runner = _get_runner()
    if runner is None:
        _log.error("workflow_runner_unavailable", run_id=run_id)
        return

    async def _execute() -> None:
        if resume and hitl_decision:
            # Cross-process HITL resume (gap #2): the run suspended in a worker
            # whose per-process checkpointer this process cannot read, so the
            # reviewer's decision travels in the task payload. Reconstruct the
            # run from the persisted record + decision and advance to terminal.
            await runner.execute_resume_fresh(
                run_id,
                workflow_id,
                tenant_id,
                step_id=hitl_decision["step_id"],
                action=hitl_decision["action"],
                actor_id=hitl_decision.get("actor_id", ""),
                note=hitl_decision.get("note"),
                form_data=hitl_decision.get("form_data"),
            )
        elif resume:
            # Legacy same-process resume: state was updated via aupdate_state on
            # this process's checkpointer; just re-invoke.
            definition = await runner._load_definition(workflow_id, tenant_id)
            compiled = runner._compiler.compile(definition)
            config = {"configurable": {"thread_id": run_id}}
            current = await compiled.aget_state(config)
            if hasattr(current, "values"):
                await compiled.ainvoke(dict(current.values), config)
        else:
            # Fresh run. runner.run()'s Celery-dispatch branch persists the run
            # row but never seeds the LangGraph checkpointer, and this worker
            # runs on its own per-process checkpointer — so there is nothing to
            # read back. Reconstruct the initial state from the persisted run
            # record + definition and execute it, so the worker runs real steps.
            await runner.execute_fresh(
                run_id,
                workflow_id,
                tenant_id,
                is_test_run=is_test_run,
                mock_overrides=mock_overrides or {},
            )

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


def _sched_redis() -> Any:
    """Sync Redis client for schedule-fire dedup (shared worker pool), or None."""
    try:
        from app.scaling.tasks import _get_sync_redis

        return _get_sync_redis()
    except Exception as exc:  # pragma: no cover - redis optional in some envs
        _log.warning("workflow_schedule_redis_unavailable", error=str(exc)[:120])
        return None


def _cron_bounds(cron: str, now: datetime, tz_name: str) -> tuple[datetime, datetime] | None:
    """Return (prev_occurrence <= now, next_occurrence > now) for ``cron`` in
    ``tz_name``, or None if the expression is invalid."""
    from croniter import croniter

    if not croniter.is_valid(cron):
        return None
    base = now
    try:
        from zoneinfo import ZoneInfo

        base = now.astimezone(ZoneInfo(tz_name or "UTC"))
    except Exception:
        base = now
    itr = croniter(cron, base)
    prev: datetime = itr.get_prev(datetime)
    nxt: datetime = itr.get_next(datetime)
    return prev, nxt


@celery_app.task(name="workflow.fire_due_workflow_schedules")
def fire_due_workflow_schedules() -> dict[str, int]:
    """Beat task (every 60s): fire published workflows whose cron schedule is due.

    Activates item-4 recurring triggers. Scans every published workflow with a
    ``trigger.type == "schedule"`` across all tenants (RLS-bypassed system scan),
    and for each cron occurrence that just came due, dispatches one run. A Redis
    SETNX keyed on the occurrence timestamp makes each occurrence fire exactly
    once even with overlapping scans or multiple beat replicas.
    """

    async def _scan_and_fire() -> dict[str, int]:
        from sqlalchemy import text as sa_text

        from app.db.rls import system_session
        from app.db.session import get_session_factory

        runner = _get_runner()
        if runner is None:
            _log.error("workflow_schedule_runner_unavailable")
            return {"scanned": 0, "fired": 0}

        now = datetime.now(UTC)
        db_factory = get_session_factory()
        async with db_factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                sa_text(
                    "SELECT id, tenant_id, definition FROM workflows "
                    "WHERE status = 'published'"
                )
            )
            rows = result.fetchall()

        from app.workflow.trigger_extract import extract_triggers, schedule_cron

        redis = _sched_redis()
        scanned = 0
        fired = 0
        for row in rows:
            wf_id, tenant_id, definition = str(row[0]), str(row[1]), (row[2] or {})
            # Pick the first schedule trigger (builder plural or DSL singular).
            sched_trigger = next(
                (t for t in extract_triggers(definition) if t.get("type") == "schedule"),
                None,
            )
            if sched_trigger is None:
                continue
            cron, tz_name = schedule_cron(sched_trigger)
            if not cron:
                continue
            scanned += 1
            try:
                bounds = _cron_bounds(cron, now, tz_name)
            except Exception as exc:
                _log.warning("workflow_schedule_cron_error", workflow_id=wf_id, error=str(exc))
                continue
            if bounds is None:
                _log.warning("workflow_schedule_invalid_cron", workflow_id=wf_id, cron=cron)
                continue
            prev, nxt = bounds
            # Only fire an occurrence that came due within the grace window.
            if (now - prev).total_seconds() > _SCHEDULE_GRACE_SECONDS:
                continue
            # Dedup: this occurrence fires at most once. TTL covers the gap until
            # the next occurrence so the key can't expire while ``prev`` is still
            # the current occurrence (which would double-fire).
            ttl = max(int((nxt - now).total_seconds()) + 60, 120)
            key = f"wf:sched:{wf_id}:{int(prev.timestamp())}"
            if redis is not None:
                try:
                    if not redis.set(key, "1", nx=True, ex=ttl):
                        continue  # already claimed by another scan / replica
                except Exception as exc:
                    _log.warning("workflow_schedule_dedup_failed", error=str(exc)[:120])
            try:
                run_id = await runner.run(
                    workflow_id=wf_id,
                    tenant_id=tenant_id,
                    inputs={},
                    trigger_type="schedule",
                    trigger_payload={"scheduled_for": prev.isoformat(), "cron": cron},
                )
                fired += 1
                _log.info(
                    "workflow_schedule_fired",
                    workflow_id=wf_id,
                    run_id=run_id,
                    scheduled_for=prev.isoformat(),
                )
            except Exception as exc:
                _log.error("workflow_schedule_fire_failed", workflow_id=wf_id, error=str(exc))
        _log.info("workflow_schedules_scanned", scanned=scanned, fired=fired)
        return {"scanned": scanned, "fired": fired}

    try:
        return _run_async(_scan_and_fire())
    except Exception as exc:
        _log.error("fire_due_workflow_schedules_failed", error=str(exc))
        return {"scanned": 0, "fired": 0}


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
