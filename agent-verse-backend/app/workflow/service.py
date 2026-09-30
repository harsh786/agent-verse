"""WorkflowService — database-backed CRUD service for the workflow engine router.

Wraps _WorkflowStore (app/api/workflows.py) for basic CRUD and delegates the
advanced features (versions, permissions, analytics, webhook events) to the
injected WorkflowRunStore, which reads/writes the real workflow_* tables.
Templates and the marketplace are backed by the SystemTemplateStore. When no
run store is wired the advanced reads return honest-empty results (never
fabricated data).

Wire up in main.py:
    from app.workflow.service import WorkflowService
    app.state.workflow_service = WorkflowService(app.state.workflow_store)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from app.workflow.state import WorkflowRunStatus

_log = structlog.get_logger(__name__)

# Statuses that mean a run is finished — cannot be cancelled/paused/resumed.
_TERMINAL_STATUSES = {"complete", "failed", "cancelled", "timed_out"}


_STEP_EVENTS = {
    "running": "step_started",
    "complete": "step_completed",
    "failed": "step_failed",
    "skipped": "step_skipped",
}


def _step_event(step: dict[str, Any], status: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": _STEP_EVENTS.get(status, "step_status"),
        "step_id": step.get("step_id"),
        "step_type": step.get("step_type"),
        "status": status,
    }
    if status == "failed" and step.get("error"):
        event["error"] = step["error"]
    output = step.get("output")
    if status == "complete" and isinstance(output, dict):
        event["output_keys"] = sorted(output)
    return event


def _terminal_event(run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "")
    if status == "complete":
        return {"event": "run_completed", "status": status, "outputs": run.get("outputs") or {}}
    if status == "cancelled":
        return {"event": "run_cancelled", "status": status}
    return {
        "event": "run_failed",
        "status": status,
        "error": run.get("error"),
        "error_step_id": run.get("error_step_id"),
    }


# Trigger types with a real firing path: api/webhook via POST /wf-hooks (and the
# trigger API), schedule via the beat scan. The DSL keeps the other literals
# (nl, event, file_drop, alertmanager, datadog, pagerduty) for round-trips, but
# nothing fires them yet.
SUPPORTED_TRIGGER_TYPES = frozenset({"api", "webhook", "schedule"})


def publish_problems(definition: dict[str, Any]) -> list[str]:
    """Reasons a definition must not be published (empty = publishable).

    Reads the raw stored DSL so an older definition never fails to load here.
    """
    from app.workflow.trigger_extract import extract_triggers, schedule_cron

    problems: list[str] = []
    for trigger in extract_triggers(definition):
        ttype = str(trigger.get("type") or "api")
        if ttype not in SUPPORTED_TRIGGER_TYPES:
            problems.append(
                f"trigger type {ttype!r} is not supported yet, so the workflow could never "
                f"start (supported: {', '.join(sorted(SUPPORTED_TRIGGER_TYPES))})"
            )
        elif ttype == "schedule" and not schedule_cron(trigger)[0]:
            problems.append("schedule trigger has no cron expression")
    steps = definition.get("steps") if isinstance(definition, dict) else None
    for step in steps or []:
        if not isinstance(step, dict) or step.get("type") != "hitl":
            continue
        strategy = (step.get("assignee") or {}).get("strategy")
        if strategy == "skill_based":
            problems.append(
                f"approval step {step.get('id')!r} uses assignee strategy 'skill_based', "
                "which is not supported yet (use round_robin, least_busy or specific)"
            )
    return problems


class WorkflowService:
    """Full-featured workflow service used by app/workflow/router.py."""

    def __init__(self, store: Any, run_store: Any | None = None) -> None:
        """
        Args:
            store: _WorkflowStore instance from app.api.workflows (already on
                   app.state.workflow_store). Provides create/list/get/update/delete.
            run_store: WorkflowRunStore (PostgresWorkflowRunStore) for run/step
                   queries. When ``None`` the run-query methods return empty results.
        """
        self._store = store
        self._run_store = run_store

    # ── Basic CRUD (delegated to _WorkflowStore) ─────────────────────────────

    async def create(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
        definition: dict | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._store.create(
            tenant_id=tenant_id,
            name=name,
            description=description,
            definition=definition or {},
            labels=labels or {},
        )

    @staticmethod
    def _enrich_trigger(item: dict[str, Any]) -> dict[str, Any]:
        """Derive ``trigger_type`` (and ``schedule_cron`` for schedules) from the
        workflow's definition so list/detail responses can badge how each
        workflow starts. No-op when the definition isn't on the item."""
        from app.workflow.trigger_extract import extract_triggers, schedule_cron

        defn = item.get("definition") or {}
        triggers = extract_triggers(defn if isinstance(defn, dict) else {})
        if triggers:
            first = triggers[0]
            item["trigger_type"] = first.get("type")
            if first.get("type") == "schedule":
                cron, _tz = schedule_cron(first)
                item["schedule_cron"] = cron or None
        return item

    async def list(
        self,
        tenant_id: str,
        page: int = 1,
        per_page: int = 20,
        status_filter: str | None = None,
        label_filter: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        items = [self._enrich_trigger(i) for i in await self._store.list(tenant_id=tenant_id)]
        # Apply filters
        if status_filter:
            items = [i for i in items if i.get("status") == status_filter]
        if label_filter:
            items = [i for i in items if label_filter in (i.get("labels") or [])]
        total = len(items)
        # Paginate
        start = (page - 1) * per_page
        return items[start : start + per_page], total

    async def get(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        item = await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)
        return self._enrich_trigger(item) if item else item

    async def update(
        self,
        tenant_id: str,
        workflow_id: str,
        updates: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        # Accept both the router's ``updates={...}`` dict and direct field kwargs
        # (used by archive/publish); merge into one partial-field set.
        fields = {**(updates or {}), **kwargs}
        return await self._store.update(tenant_id=tenant_id, workflow_id=workflow_id, **fields)

    async def archive(self, tenant_id: str, workflow_id: str) -> bool:
        """Archive (soft-delete) a workflow by setting status=archived."""
        item = await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if not item:
            return False
        await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="archived",
        )
        _log.info("workflow.archived", tenant_id=tenant_id, workflow_id=workflow_id)
        return True

    async def publish(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        """Publish a draft workflow (status draft → published).

        Publishing also *activates* the workflow's triggers: it mints the stable
        signed webhook token so an external system can fire the workflow (item 3),
        and — because the workflow is now ``published`` — the Celery beat scan
        (``fire_due_workflow_schedules``) begins evaluating its cron triggers
        (item 4). The returned dict carries the webhook token/path/url so the UI
        can show the caller their trigger URL.

        Raises ``ValueError`` (HTTP 422) when the definition uses a feature that
        cannot work once live, instead of publishing a workflow that never runs.
        """
        current = await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if current is None:
            return None
        problems = publish_problems(current.get("definition") or {})
        if problems:
            raise ValueError("Cannot publish: " + "; ".join(problems))
        result = await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="published",
            published_at=datetime.now(UTC).isoformat(),
        )
        if result is None:
            return None

        hook = await self.webhook_trigger(tenant_id, workflow_id)
        result["webhook_token"] = hook["webhook_token"]
        result["webhook_path"] = hook["webhook_path"]
        result["webhook_url"] = hook["webhook_url"]
        _log.info("workflow.published", tenant_id=tenant_id, workflow_id=workflow_id)
        return result

    # ── Webhook token (rotation) ──────────────────────────────────────────────

    async def webhook_token_version(self, tenant_id: str, workflow_id: str) -> int:
        """Current token version (0 when never rotated / no ACL-capable store)."""
        getter = getattr(self._run_store, "get_webhook_token_version", None)
        if getter is None:
            return 0
        return int(await getter(tenant_id, workflow_id))

    async def webhook_trigger(self, tenant_id: str, workflow_id: str) -> dict[str, str]:
        """The workflow's current inbound webhook token / path / URL."""
        from app.workflow.webhook_tokens import make_webhook_token

        version = await self.webhook_token_version(tenant_id, workflow_id)
        token = make_webhook_token(tenant_id, workflow_id, version)
        path = f"/wf-hooks/{token}"
        base = ""
        try:
            from app.core.config import get_settings

            base = (get_settings().workflow_webhook_base_url or "").rstrip("/")
        except Exception:
            base = ""
        return {
            "webhook_token": token,
            "webhook_path": path,
            "webhook_url": f"{base}{path}" if base else path,
        }

    async def rotate_webhook_token(self, tenant_id: str, workflow_id: str) -> dict[str, str]:
        """Invalidate every issued webhook URL and return the new one.

        Old bug: tokens were a stateless HMAC of (tenant, workflow) with no
        version, so a leaked URL worked forever."""
        rotate = getattr(self._run_store, "rotate_webhook_token", None)
        if rotate is None:
            raise RuntimeError("webhook token rotation needs the persistent run store")
        await rotate(tenant_id, workflow_id)
        _log.info("workflow.webhook_token_rotated", tenant_id=tenant_id, workflow_id=workflow_id)
        return await self.webhook_trigger(tenant_id, workflow_id)

    async def unpublish(self, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        """Unpublish a workflow (status published → draft)."""
        return await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="draft",
            published_at=None,
        )

    # ── Versions ──────────────────────────────────────────────────────────────

    async def list_versions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        """Return the real published version history from
        ``workflow_definition_versions``. Honest-empty when no run store is wired."""
        if self._run_store is None:
            return []
        return await self._run_store.list_versions(tenant_id, workflow_id)

    async def restore_version(
        self, tenant_id: str, workflow_id: str, version: str | int
    ) -> dict[str, Any] | None:
        """Restore a previous version: load its stored definition and write it
        back onto the current workflow. Raises ValueError if the version is
        unknown."""
        if self._run_store is None:
            raise ValueError(f"version {version} not found")
        snapshot = await self._run_store.get_definition_version(
            tenant_id, workflow_id, str(version)
        )
        if not snapshot:
            raise ValueError(f"version {version} not found")
        return await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            definition=snapshot.get("definition_json") or {},
        )

    # ── Permissions ───────────────────────────────────────────────────────────

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        # A run store without ACL storage can hold no grants (tenant default).
        getter = getattr(self._run_store, "get_permissions", None)
        if getter is None:
            return []
        result: list[dict[str, Any]] = await getter(tenant_id, workflow_id)
        return result

    async def add_permission(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        subject: str = "",
        role: str = "",
        subject_type: str = "user",
        **_: Any,
    ) -> dict[str, Any]:
        if self._run_store is None:
            raise RuntimeError("permission persistence unavailable (no run store wired)")
        return await self._run_store.add_permission(
            tenant_id,
            workflow_id,
            subject_type=subject_type,
            subject_id=subject,
            permission=role,
        )

    async def remove_permission(self, tenant_id: str, workflow_id: str, permission_id: str) -> bool:
        if self._run_store is None:
            return False
        return await self._run_store.remove_permission(tenant_id, workflow_id, permission_id)

    # ── Templates ─────────────────────────────────────────────────────────────

    async def list_templates(
        self,
        category: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return workflow templates from SystemTemplateStore if wired."""
        from app.main import app as _app

        template_store = getattr(getattr(_app, "state", None), "template_store_we", None)
        if template_store is None:
            return [], 0
        try:
            all_templates = await template_store.list_all()
            if category:
                all_templates = [t for t in all_templates if t.get("category") == category]
            total = len(all_templates)
            start = (page - 1) * per_page
            return all_templates[start : start + per_page], total
        except Exception as exc:
            _log.warning("workflow.service.list_templates_failed", error=str(exc))
            return [], 0

    async def get_template(self, slug: str) -> dict[str, Any] | None:
        from app.main import app as _app

        template_store = getattr(getattr(_app, "state", None), "template_store_we", None)
        if template_store is None:
            return None
        try:
            return await template_store.get_by_slug(slug)
        except Exception:
            return None

    async def instantiate_template(
        self,
        tenant_id: str,
        slug: str,
        name: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        template = await self.get_template(slug)
        if not template:
            raise ValueError(f"Template '{slug}' not found")
        return await self.create(
            tenant_id=tenant_id,
            name=name or template.get("name", slug),
            description=template.get("description", ""),
            definition=template.get("definition", {}),
            labels=template.get("labels", []),
        )

    # ── Analytics ─────────────────────────────────────────────────────────────

    @staticmethod
    def _rates(stats: dict[str, Any]) -> tuple[float, float]:
        """(success_rate, error_rate) from a run-stats dict, div-by-zero safe."""
        total = int(stats.get("total") or 0)
        if total <= 0:
            return 0.0, 0.0
        completed = int(stats.get("completed") or 0)
        failed = int(stats.get("failed") or 0)
        return completed / total, failed / total

    async def analytics_summary(self, tenant_id: str, days: int = 30) -> dict[str, Any]:
        items = await self._store.list(tenant_id=tenant_id)
        stats = (
            await self._run_store.aggregate_run_stats(tenant_id, days)
            if self._run_store is not None
            else {}
        )
        success_rate, _ = self._rates(stats)
        return {
            "total_workflows": len(items),
            "published": sum(1 for i in items if i.get("status") == "published"),
            "draft": sum(1 for i in items if i.get("status") == "draft"),
            "archived": sum(1 for i in items if i.get("status") == "archived"),
            "total_runs": int(stats.get("total") or 0),
            "success_rate": success_rate,
            "avg_duration_s": float(stats.get("avg_duration_s") or 0.0),
            "period_days": days,
        }

    async def workflow_analytics(
        self, tenant_id: str, workflow_id: str, days: int = 30
    ) -> dict[str, Any]:
        stats = (
            await self._run_store.workflow_run_stats(tenant_id, workflow_id, days)
            if self._run_store is not None
            else {}
        )
        success_rate, error_rate = self._rates(stats)
        return {
            "workflow_id": workflow_id,
            "total_runs": int(stats.get("total") or 0),
            "success_rate": success_rate,
            "avg_duration_s": float(stats.get("avg_duration_s") or 0.0),
            "error_rate": error_rate,
            "period_days": days,
        }

    # ── Webhook Events ────────────────────────────────────────────────────────

    async def list_webhook_events(
        self,
        tenant_id: str,
        workflow_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._run_store is None:
            return [], 0
        return await self._run_store.list_webhook_events(
            tenant_id, workflow_id, limit=per_page, offset=(page - 1) * per_page
        )

    # ── Runs (delegated to the injected WorkflowRunStore) ─────────────────────

    async def list_runs(
        self,
        tenant_id: str,
        *,
        workflow_id: str | None = None,
        status_filter: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._run_store is None:
            return [], 0
        return await self._run_store.list(
            tenant_id,
            workflow_id=workflow_id,
            status=status_filter,
            limit=per_page,
            offset=(page - 1) * per_page,
        )

    async def get_run(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        if self._run_store is None:
            return None
        return await self._run_store.get(tenant_id, run_id)

    async def list_step_results(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        if self._run_store is None:
            return []
        return await self._run_store.list_step_results(tenant_id, run_id)

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        if self._run_store is None:
            return None
        return await self._run_store.get_step_result(tenant_id, run_id, step_id)

    async def cancel_run(self, tenant_id: str, run_id: str) -> bool:
        """Cancel a run unless it is already in a terminal state."""
        if self._run_store is None:
            return False
        run = await self._run_store.get(tenant_id, run_id)
        if run is None or run.get("status") in _TERMINAL_STATUSES:
            return False
        return await self._run_store.update_status(
            run_id, WorkflowRunStatus.CANCELLED, tenant_id=tenant_id
        )

    async def pause_run(self, tenant_id: str, run_id: str) -> bool:
        """Pause a run that is currently pending or running."""
        if self._run_store is None:
            return False
        run = await self._run_store.get(tenant_id, run_id)
        if run is None or run.get("status") not in ("pending", "running"):
            return False
        return await self._run_store.update_status(
            run_id, WorkflowRunStatus.PAUSED, tenant_id=tenant_id
        )

    async def resume_run(self, tenant_id: str, run_id: str) -> bool:
        """Resume a paused run."""
        if self._run_store is None:
            return False
        run = await self._run_store.get(tenant_id, run_id)
        if run is None or run.get("status") != "paused":
            return False
        return await self._run_store.update_status(
            run_id, WorkflowRunStatus.RUNNING, tenant_id=tenant_id
        )

    async def retry_run(self, tenant_id: str, run_id: str, runner: Any = None) -> str | None:
        """Retry a failed run as a NEW run and dispatch it. Returns the new run_id.

        The new run reuses the failed run's inputs (and its callback_url), is
        seeded with the failed run's COMPLETE step results, and goes through the
        same ``WorkflowRunner.run`` path as any new run (row + Celery dispatch,
        or inline without a broker). Because the engine skips steps already
        persisted COMPLETE, the retry resumes after the last completed step
        instead of redoing them. It runs against the workflow's CURRENT
        definition; steps whose ids no longer exist are simply not reused.

        Old bug: this inserted a ``pending`` row and never dispatched it, so the
        retry sat pending forever (while claiming checkpoint resume).

        Returns None when the run is unknown or not ``failed``. Raises
        ``WorkflowEngineUnavailableError`` when no durable runner is wired —
        rather than creating an orphan row nothing will ever execute.
        """
        from app.workflow.runner import WorkflowEngineUnavailableError

        if self._run_store is None:
            return None
        run = await self._run_store.get(tenant_id, run_id)
        if run is None or run.get("status") != "failed":
            return None
        if runner is None or getattr(runner, "_run_store", None) is None:
            raise WorkflowEngineUnavailableError("workflow runner not available for retry")
        prior_meta = run.get("run_metadata") or {}
        metadata: dict[str, Any] = {"retry_of": run_id}
        if prior_meta.get("callback_url"):
            metadata["callback_url"] = prior_meta["callback_url"]
        new_run_id: str = await runner.run(
            workflow_id=run.get("workflow_id", ""),
            tenant_id=tenant_id,
            inputs=run.get("inputs") or {},
            trigger_type="retry",
            run_metadata=metadata,
            seed_from_run_id=run_id,
        )
        return new_run_id

    async def get_run_debug(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        """Return the run plus its step results and variable snapshot for debugging."""
        if self._run_store is None:
            return None
        run = await self._run_store.get(tenant_id, run_id)
        if run is None:
            return None
        steps = await self._run_store.list_step_results(tenant_id, run_id)
        return {
            "run": run,
            "step_outputs": {s["step_id"]: s.get("output") for s in steps},
            "steps": steps,
            "vars": run.get("outputs", {}),
        }

    async def stream_run_events(
        self,
        tenant_id: str,
        run_id: str,
        *,
        poll_interval: float = 1.0,
        heartbeat_interval: float = 15.0,
        max_duration: float = 3600.0,
    ) -> Any:
        """Live run event stream, read from the persisted run (any replica).

        Polls the run row and its step rows and emits what changed:
        ``step_started`` / ``step_completed`` / ``step_failed`` / ``step_skipped``
        / ``step_status``, ``run_status`` on every run-status change,
        ``run_waiting`` when it waits for a human or timer, ``heartbeat`` when
        quiet, and ends only on a terminal status with ``run_completed`` /
        ``run_failed`` / ``run_cancelled`` (or ``stream_timeout`` after
        ``max_duration`` — the client reconnects).

        Old bug: a one-shot snapshot that ended with ``run_failed`` for every
        non-terminal run, so a UI watching a running or waiting run showed it as
        failed.
        """
        import asyncio
        import time

        run = await self.get_run(tenant_id, run_id) if self._run_store else None
        if run is None:
            yield {"event": "run_not_found", "error": "run not found"}
            return

        started = last_emit = time.monotonic()
        seen_steps: dict[str, str] = {}
        last_status: str | None = None
        while True:
            events: list[dict[str, Any]] = []
            for step in await self.list_step_results(tenant_id, run_id):
                key = f"{step.get('step_id')}@{step.get('started_at') or ''}"
                status = str(step.get("status") or "")
                if seen_steps.get(key) == status:
                    continue
                seen_steps[key] = status
                events.append(_step_event(step, status))

            status = str(run.get("status") or "")
            if status != last_status:
                last_status = status
                events.append({"event": "run_status", "status": status})
                if status in ("waiting_hitl", "waiting_timer"):
                    events.append({"event": "run_waiting", "status": status})
            terminal = _terminal_event(run) if status in _TERMINAL_STATUSES else None
            if terminal is not None:
                events.append(terminal)

            now = time.monotonic()
            if events:
                last_emit = now
                for event in events:
                    yield event
            elif now - last_emit >= heartbeat_interval:
                last_emit = now
                yield {"event": "heartbeat", "status": status}
            if terminal is not None:
                return
            if now - started >= max_duration:
                yield {"event": "stream_timeout", "status": status}
                return

            await asyncio.sleep(poll_interval)
            refreshed = await self.get_run(tenant_id, run_id)
            if refreshed is None:
                yield {"event": "run_not_found", "error": "run not found"}
                return
            run = refreshed

    # ── Marketplace ───────────────────────────────────────────────────────────

    async def marketplace_list(
        self,
        page: int = 1,
        per_page: int = 20,
        category: str | None = None,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """The workflow marketplace is the gallery of installable system
        templates. Backed by the real SystemTemplateStore; filtered by category
        and free-text ``q`` (name/description/tags)."""
        from app.main import app as _app

        template_store = getattr(getattr(_app, "state", None), "template_store_we", None)
        if template_store is None:
            return [], 0
        try:
            templates = await template_store.list_all()
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("workflow.service.marketplace_list_failed", error=str(exc))
            return [], 0
        if category:
            templates = [t for t in templates if t.get("category") == category]
        if q:
            needle = q.lower()
            templates = [
                t
                for t in templates
                if needle in str(t.get("name", "")).lower()
                or needle in str(t.get("description", "")).lower()
                or any(needle in str(tag).lower() for tag in (t.get("tags") or []))
            ]
        total = len(templates)
        start = (page - 1) * per_page
        return templates[start : start + per_page], total
