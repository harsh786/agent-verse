"""NF-17 unit tests: export retention setting, beat wiring, fail-closed sweep, 410."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import training_export as api
from app.core.config import Settings
from app.training_export import jobs
from app.tenancy.context import PlanTier, TenantContext


def test_retention_settings_have_safe_defaults() -> None:
    fields = Settings.model_fields
    assert fields["training_export_retention_hours"].default == 168
    assert fields["training_export_expiry_batch_size"].default == 100
    assert fields["training_export_expiry_max_batches"].default == 10


async def test_zero_retention_disables_expiry_without_touching_anything() -> None:
    db = MagicMock(side_effect=AssertionError("no DB access when disabled"))
    out = await jobs.expire_finished_exports(db, None, retention_hours=0)
    assert out["status"] == "disabled"
    assert out["expired"] == 0


async def test_without_object_storage_nothing_is_marked_expired() -> None:
    db = MagicMock(side_effect=AssertionError("no row may be marked without a delete"))
    with pytest.raises(jobs.TrainingExportUnavailableError):
        await jobs.expire_finished_exports(db, None, retention_hours=24)


def test_beat_runs_the_sweep_hourly_on_the_maintenance_queue() -> None:
    from app.scaling.celery_app import celery_app
    from app.training_export.tasks import EXPIRE_TASK_NAME

    entry = celery_app.conf.beat_schedule["expire-training-exports-hourly"]
    assert entry["task"] == EXPIRE_TASK_NAME
    assert entry["options"]["queue"] == "maintenance"
    assert celery_app.conf.task_routes[EXPIRE_TASK_NAME] == {"queue": "maintenance"}
    assert EXPIRE_TASK_NAME in celery_app.tasks


def test_beat_task_reports_an_error_instead_of_fake_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training_export import tasks

    monkeypatch.setattr(jobs, "object_store_from_env", lambda: None)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: MagicMock())
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")  # no beat guard lock

    out = tasks.expire_training_exports.run()

    assert out["status"] == "error"
    assert "object storage is not configured" in out["error"]


def test_download_of_an_expired_export_is_410(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _found(db: Any, tenant_id: str, job_id: str) -> tuple[str, str]:
        return "expired", ""

    monkeypatch.setattr(jobs, "get_job_object_key", _found)
    app = FastAPI()
    app.include_router(api.router)
    app.state.db_session_factory = MagicMock()
    app.state.goal_service = SimpleNamespace(_goals={}, _eval_scores={})

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id="t-nf17", plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    resp = TestClient(app).get("/intelligence/export-training-data/jobs/j1/download")
    assert resp.status_code == 410
    assert resp.json()["status"] == "expired"
