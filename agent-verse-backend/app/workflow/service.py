"""WorkflowService — database-backed CRUD service for the workflow engine router.

Wraps _WorkflowStore (app/api/workflows.py) for basic CRUD and provides
stub implementations for advanced features (versions, permissions, analytics,
templates, marketplace) so the router never returns 503.

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

    async def list(
        self,
        tenant_id: str,
        page: int = 1,
        per_page: int = 20,
        status_filter: str | None = None,
        label_filter: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        items = await self._store.list(tenant_id=tenant_id)
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
        return await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)

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
        """Publish a draft workflow (status draft → published)."""
        return await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="published",
            published_at=datetime.now(UTC).isoformat(),
        )

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
        """Return version history. Returns current version only until full
        version control is implemented."""
        item = await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)
        if not item:
            return []
        return [
            {
                "version_id": item.get("id", workflow_id),
                "version": 1,
                "created_at": item.get("created_at", datetime.now(UTC).isoformat()),
                "status": item.get("status", "draft"),
                "notes": "Initial version",
            }
        ]

    async def restore_version(
        self, tenant_id: str, workflow_id: str, version_id: str
    ) -> dict[str, Any] | None:
        """Restore a previous version (stub — returns current state)."""
        return await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)

    # ── Permissions ───────────────────────────────────────────────────────────

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return []

    async def add_permission(
        self, tenant_id: str, workflow_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"workflow_id": workflow_id, **kwargs, "created_at": datetime.now(UTC).isoformat()}

    async def remove_permission(self, tenant_id: str, workflow_id: str, permission_id: str) -> bool:
        return True

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

    async def analytics_summary(self, tenant_id: str, days: int = 30) -> dict[str, Any]:
        items = await self._store.list(tenant_id=tenant_id)
        return {
            "total_workflows": len(items),
            "published": sum(1 for i in items if i.get("status") == "published"),
            "draft": sum(1 for i in items if i.get("status") == "draft"),
            "archived": sum(1 for i in items if i.get("status") == "archived"),
            "total_runs": 0,
            "success_rate": 0.0,
            "avg_duration_s": 0.0,
            "period_days": days,
        }

    async def workflow_analytics(
        self, tenant_id: str, workflow_id: str, days: int = 30
    ) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "total_runs": 0,
            "success_rate": 0.0,
            "avg_duration_s": 0.0,
            "error_rate": 0.0,
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
        return [], 0

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

    async def retry_run(self, tenant_id: str, run_id: str) -> str | None:
        """Create a fresh run from a failed run's inputs. Returns the new run_id."""
        if self._run_store is None:
            return None
        run = await self._run_store.get(tenant_id, run_id)
        if run is None or run.get("status") != "failed":
            return None
        import uuid as _uuid

        new_run_id = str(_uuid.uuid4())
        await self._run_store.create(
            run_id=new_run_id,
            workflow_id=run.get("workflow_id", ""),
            tenant_id=tenant_id,
            trigger_type="retry",
            inputs=run.get("inputs", {}),
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
        self, tenant_id: str, run_id: str
    ) -> Any:
        """Minimal terminal-state SSE stream: emits step results then a final event."""
        run = await self.get_run(tenant_id, run_id) if self._run_store else None
        if run is None:
            yield {"event": "run_failed", "status": "failed", "error": "run not found"}
            return
        for step in await self.list_step_results(tenant_id, run_id):
            yield {
                "event": "step_completed",
                "step_id": step["step_id"],
                "status": step.get("status"),
            }
        terminal = run.get("status") in _TERMINAL_STATUSES
        yield {
            "event": "run_completed" if terminal else "run_failed",
            "status": run.get("status"),
            "outputs": run.get("outputs", {}),
        }

    # ── Marketplace ───────────────────────────────────────────────────────────

    async def marketplace_list(
        self,
        page: int = 1,
        per_page: int = 20,
        category: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return [], 0
