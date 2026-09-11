"""2.W-2: WorkflowService advanced features must be real (DB-backed via the run
store) — not fabricated. These unit tests use a fake run store to prove the
service delegates to real persistence and returns honest-empty when no store is
wired (rather than inventing a version record, claiming permission success, or
reporting zeroed analytics).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.service import WorkflowService

pytestmark = pytest.mark.asyncio


class _FakeStore:
    """Minimal _WorkflowStore double for definition reads/updates."""

    def __init__(self) -> None:
        self.updated: dict[str, Any] = {}
        self._items = [
            {"id": "wf-1", "status": "published", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "wf-2", "status": "draft", "created_at": "2026-01-02T00:00:00Z"},
        ]

    async def get(self, *, tenant_id: str, workflow_id: str) -> dict[str, Any] | None:
        return next((i for i in self._items if i["id"] == workflow_id), None)

    async def list(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return list(self._items)

    async def update(self, *, tenant_id: str, workflow_id: str, **fields: Any) -> dict[str, Any]:
        self.updated = {"workflow_id": workflow_id, **fields}
        return {"id": workflow_id, **fields}


class _FakeRunStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def list_versions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        self.calls.append(("list_versions", (tenant_id, workflow_id), {}))
        return [
            {"version": "1.0.0", "change_summary": "init", "published_at": "2026-01-01T00:00:00Z"},
            {"version": "1.1.0", "change_summary": "tweak", "published_at": "2026-01-02T00:00:00Z"},
        ]

    async def get_definition_version(
        self, tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, Any] | None:
        self.calls.append(("get_definition_version", (tenant_id, workflow_id, version), {}))
        if version == "1":
            return {"version": "1", "definition_json": {"name": "restored", "steps": []}}
        return None

    async def get_permissions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return [{"id": "p1", "subject_id": "user-1", "permission": "viewer"}]

    async def add_permission(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        subject_type: str,
        subject_id: str,
        permission: str,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "add_permission",
                (tenant_id, workflow_id),
                {"subject_id": subject_id, "permission": permission},
            )
        )
        return {"id": "p-new", "subject_id": subject_id, "permission": permission}

    async def remove_permission(self, tenant_id: str, workflow_id: str, permission_id: str) -> bool:
        self.calls.append(("remove_permission", (tenant_id, workflow_id, permission_id), {}))
        return permission_id == "p1"

    async def workflow_run_stats(
        self, tenant_id: str, workflow_id: str, days: int
    ) -> dict[str, Any]:
        return {"total": 10, "completed": 7, "failed": 2, "avg_duration_s": 3.5}

    async def aggregate_run_stats(self, tenant_id: str, days: int) -> dict[str, Any]:
        return {"total": 12, "completed": 9, "failed": 1, "avg_duration_s": 4.0}

    async def list_webhook_events(
        self, tenant_id: str, workflow_id: str, *, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        return ([{"id": "e1", "status": "completed", "attempts": 1}], 1)


# ── Versions ──────────────────────────────────────────────────────────────────


async def test_list_versions_delegates_to_run_store() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    versions = await svc.list_versions(tenant_id="t1", workflow_id="wf-1")
    assert [v["version"] for v in versions] == ["1.0.0", "1.1.0"]


async def test_list_versions_honest_empty_without_run_store() -> None:
    svc = WorkflowService(_FakeStore(), None)
    assert await svc.list_versions(tenant_id="t1", workflow_id="wf-1") == []


async def test_restore_version_reads_version_and_updates_definition() -> None:
    store = _FakeStore()
    svc = WorkflowService(store, _FakeRunStore())
    result = await svc.restore_version(tenant_id="t1", workflow_id="wf-1", version=1)
    assert result is not None
    assert store.updated["workflow_id"] == "wf-1"
    assert store.updated["definition"] == {"name": "restored", "steps": []}


async def test_restore_missing_version_raises() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    with pytest.raises(ValueError, match="version"):
        await svc.restore_version(tenant_id="t1", workflow_id="wf-1", version=999)


# ── Permissions ───────────────────────────────────────────────────────────────


async def test_get_permissions_delegates() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    perms = await svc.get_permissions(tenant_id="t1", workflow_id="wf-1")
    assert perms[0]["subject_id"] == "user-1"


async def test_add_permission_persists_and_returns_record() -> None:
    rs = _FakeRunStore()
    svc = WorkflowService(_FakeStore(), rs)
    perm = await svc.add_permission(
        tenant_id="t1", workflow_id="wf-1", subject="user-9", role="editor"
    )
    assert perm["subject_id"] == "user-9"
    assert perm["permission"] == "editor"
    assert any(c[0] == "add_permission" for c in rs.calls)


async def test_remove_permission_returns_bool() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    assert (
        await svc.remove_permission(tenant_id="t1", workflow_id="wf-1", permission_id="p1") is True
    )
    assert (
        await svc.remove_permission(tenant_id="t1", workflow_id="wf-1", permission_id="nope")
        is False
    )


async def test_permissions_honest_without_run_store() -> None:
    svc = WorkflowService(_FakeStore(), None)
    assert await svc.get_permissions(tenant_id="t1", workflow_id="wf-1") == []
    assert (
        await svc.remove_permission(tenant_id="t1", workflow_id="wf-1", permission_id="p1") is False
    )


# ── Analytics ─────────────────────────────────────────────────────────────────


async def test_workflow_analytics_computed_from_runs() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    a = await svc.workflow_analytics(tenant_id="t1", workflow_id="wf-1", days=30)
    assert a["total_runs"] == 10
    assert a["success_rate"] == pytest.approx(0.7)
    assert a["error_rate"] == pytest.approx(0.2)
    assert a["avg_duration_s"] == pytest.approx(3.5)


async def test_analytics_summary_merges_definitions_and_runs() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    s = await svc.analytics_summary(tenant_id="t1", days=7)
    assert s["total_workflows"] == 2
    assert s["total_runs"] == 12  # from aggregate_run_stats, not fabricated 0
    assert s["success_rate"] == pytest.approx(0.75)


async def test_workflow_analytics_zero_runs_is_honest_not_fabricated() -> None:
    class _EmptyRun(_FakeRunStore):
        async def workflow_run_stats(self, *a: Any, **k: Any) -> dict[str, Any]:
            return {"total": 0, "completed": 0, "failed": 0, "avg_duration_s": 0.0}

    svc = WorkflowService(_FakeStore(), _EmptyRun())
    a = await svc.workflow_analytics(tenant_id="t1", workflow_id="wf-1", days=30)
    assert a["total_runs"] == 0
    assert a["success_rate"] == 0.0  # division-by-zero guarded, honest zero


# ── Webhook events ────────────────────────────────────────────────────────────


async def test_list_webhook_events_delegates() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    items, total = await svc.list_webhook_events(
        tenant_id="t1", workflow_id="wf-1", page=1, per_page=20
    )
    assert total == 1
    assert items[0]["id"] == "e1"


async def test_list_webhook_events_honest_without_run_store() -> None:
    svc = WorkflowService(_FakeStore(), None)
    assert await svc.list_webhook_events(tenant_id="t1", workflow_id="wf-1") == ([], 0)


# ── Marketplace (accepts q; no TypeError) ─────────────────────────────────────


async def test_marketplace_list_accepts_q_without_typeerror() -> None:
    svc = WorkflowService(_FakeStore(), _FakeRunStore())
    items, total = await svc.marketplace_list(category=None, q="anything", page=1, per_page=20)
    assert isinstance(items, list)
    assert isinstance(total, int)


# ── _enrich_trigger: derive trigger_type / schedule_cron from the definition ──


async def test_enrich_trigger_schedule() -> None:
    item = {
        "id": "w1",
        "definition": {"trigger": {"type": "schedule", "schedule": {"cron": "0 9 * * *"}}},
    }
    out = WorkflowService._enrich_trigger(item)
    assert out["trigger_type"] == "schedule"
    assert out["schedule_cron"] == "0 9 * * *"


async def test_enrich_trigger_webhook_has_no_cron() -> None:
    item = {"id": "w2", "definition": {"trigger": {"type": "webhook", "webhook": {"path": "/x"}}}}
    out = WorkflowService._enrich_trigger(item)
    assert out["trigger_type"] == "webhook"
    assert out.get("schedule_cron") is None


async def test_enrich_trigger_no_definition_is_noop() -> None:
    item = {"id": "w3"}
    out = WorkflowService._enrich_trigger(item)
    assert out.get("trigger_type") is None
    assert out.get("schedule_cron") is None
