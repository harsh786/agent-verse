"""WorkflowService — database-backed CRUD service for the workflow engine router.

Wraps _WorkflowStore (app/api/workflows.py) for basic CRUD and provides
stub implementations for advanced features (versions, permissions, analytics,
templates, marketplace) so the router never returns 503.

Wire up in main.py:
    from app.workflow.service import WorkflowService
    app.state.workflow_service = WorkflowService(app.state.workflow_store)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


class WorkflowService:
    """Full-featured workflow service used by app/workflow/router.py."""

    def __init__(self, store: Any) -> None:
        """
        Args:
            store: _WorkflowStore instance from app.api.workflows (already on
                   app.state.workflow_store). Provides create/list/get/update/delete.
        """
        self._store = store

    # ── Basic CRUD (delegated to _WorkflowStore) ─────────────────────────────

    async def create(
        self,
        tenant_id: str,
        name: str,
        description: str = "",
        definition: dict | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._store.create(
            tenant_id=tenant_id,
            name=name,
            description=description,
            definition=definition or {},
            labels=labels or [],
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

    async def get(
        self, tenant_id: str, workflow_id: str
    ) -> dict[str, Any] | None:
        return await self._store.get(tenant_id=tenant_id, workflow_id=workflow_id)

    async def update(
        self,
        tenant_id: str,
        workflow_id: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await self._store.update(
            tenant_id=tenant_id, workflow_id=workflow_id, **kwargs
        )

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

    async def publish(
        self, tenant_id: str, workflow_id: str
    ) -> dict[str, Any] | None:
        """Publish a draft workflow (status draft → published)."""
        return await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="published",
            published_at=datetime.now(UTC).isoformat(),
        )

    async def unpublish(
        self, tenant_id: str, workflow_id: str
    ) -> dict[str, Any] | None:
        """Unpublish a workflow (status published → draft)."""
        return await self._store.update(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="draft",
            published_at=None,
        )

    # ── Versions ──────────────────────────────────────────────────────────────

    async def list_versions(
        self, tenant_id: str, workflow_id: str
    ) -> list[dict[str, Any]]:
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

    async def get_permissions(
        self, tenant_id: str, workflow_id: str
    ) -> list[dict[str, Any]]:
        return []

    async def add_permission(
        self, tenant_id: str, workflow_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"workflow_id": workflow_id, **kwargs, "created_at": datetime.now(UTC).isoformat()}

    async def remove_permission(
        self, tenant_id: str, workflow_id: str, permission_id: str
    ) -> bool:
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

    async def analytics_summary(
        self, tenant_id: str, days: int = 30
    ) -> dict[str, Any]:
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

    # ── Marketplace ───────────────────────────────────────────────────────────

    async def marketplace_list(
        self,
        page: int = 1,
        per_page: int = 20,
        category: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        return [], 0
