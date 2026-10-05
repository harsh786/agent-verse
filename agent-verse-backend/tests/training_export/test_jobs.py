"""OPS-37: durable training-export jobs are fenced, bounded and honest.

* a claim hands the worker a token; every later write (heartbeat, completion,
  failure) is fenced by it, so a worker that lost its claim cannot overwrite
  the row of the worker that took over;
* a redelivered task that finds the job held by a live worker reports busy
  (and the Celery task retries later) instead of skipping it forever;
* a tenant cannot pile up unbounded active jobs (429);
* the final step survives the per-goal step cap (it is the exported answer).
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import training_export as api
from app.tenancy.context import PlanTier, TenantContext
from app.training_export import jobs as export_jobs
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "t-jobs"
JOB = "j" * 32
_T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _job_row(status: str, *, key: str | None = None, error: str | None = None) -> tuple[Any, ...]:
    return (JOB, TENANT, status, "openai", 0.8, 3, None, key, error, _T0, None, None)


class _Store:
    def __init__(self, *, fail_upload: bool = False, fail_open: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self._fail_upload = fail_upload
        self._fail_open = fail_open

    async def upload(self, key: str, fileobj: Any) -> None:
        if self._fail_upload:
            raise OSError("bucket gone")
        self.objects[key] = fileobj.read()

    async def open_stream(self, key: str) -> Any:
        if self._fail_open:
            raise OSError("minio down")
        return io.BytesIO(self.objects[key])


def _worker_rows(
    *,
    claim: bool = True,
    status_after_claim: str = "running",
    heartbeat_ok: bool = True,
    finish_ok: bool = True,
    n_goals: int = 3,
) -> Any:
    def rows_for(sql: str, p: dict[str, Any]) -> list[Any]:
        if sql.startswith("UPDATE training_export_jobs SET status = 'running'"):
            return [(JOB,)] if claim else []
        if sql.startswith("UPDATE training_export_jobs SET heartbeat_at"):
            return [(JOB,)] if heartbeat_ok else []
        if sql.startswith("UPDATE training_export_jobs SET status = 'complete'"):
            return [(JOB,)] if finish_ok else []
        if sql.startswith("UPDATE training_export_jobs SET status = 'failed'"):
            return [(JOB,)]
        if sql.startswith("SELECT") and "FROM training_export_jobs" in sql:
            return [_job_row(status_after_claim)]
        if "FROM goals g" in sql:
            rows = [
                (f"g{i}", f"goal {i}", _T0 - dt.timedelta(seconds=i), 0.9) for i in range(n_goals)
            ]
            return rows[: p["lim"]]
        if "FROM goal_steps" in sql:
            return [(gid, "out", [{"tool_name": "t"}]) for gid in p["gids"]]
        return []

    return rows_for


# ── worker: claim fencing ─────────────────────────────────────────────────────


async def test_claim_issues_a_token_and_completion_is_fenced_by_it() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows())
    store = _Store()
    result = await export_jobs.run_export_job(db, store, JOB, TENANT)
    assert result == {"job_id": JOB, "status": "complete", "example_count": 3}

    (claim,) = db.touching("SET status = 'running'")
    token = claim.params["tok"]
    assert token and "claim_token = :tok" in claim.sql and "heartbeat_at" in claim.sql
    (done,) = db.touching("SET status = 'complete'")
    assert "claim_token = :tok" in done.sql and done.params["tok"] == token
    assert store.objects[export_jobs.object_key(TENANT, JOB)].count(b"\n") == 3
    assert_tenant_scoped(db, "training_export_jobs", TENANT, min_statements=3)


async def test_worker_heartbeats_while_streaming_under_its_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "HEARTBEAT_SECONDS", 0.0)
    db = RlsRecordingDb(rows_for=_worker_rows())
    await export_jobs.run_export_job(db, _Store(), JOB, TENANT)
    beats = db.touching("SET heartbeat_at")
    assert beats, "a long export must refresh its heartbeat"
    token = db.touching("SET status = 'running'")[0].params["tok"]
    assert all(b.params["tok"] == token and "claim_token = :tok" in b.sql for b in beats)


async def test_worker_that_lost_its_claim_aborts_without_touching_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "HEARTBEAT_SECONDS", 0.0)
    db = RlsRecordingDb(rows_for=_worker_rows(heartbeat_ok=False))
    store = _Store()
    with pytest.raises(export_jobs.ExportClaimLostError):
        await export_jobs.run_export_job(db, store, JOB, TENANT)
    # The new owner's row is not marked failed or complete by the stale worker.
    assert not db.touching("SET status = 'failed'")
    assert not db.touching("SET status = 'complete'")
    assert not store.objects


async def test_lost_claim_at_completion_is_not_reported_as_success() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows(finish_ok=False))
    with pytest.raises(export_jobs.ExportClaimLostError):
        await export_jobs.run_export_job(db, _Store(), JOB, TENANT)
    assert not db.touching("SET status = 'failed'")


async def test_failure_is_recorded_under_the_claim_token() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows())
    with pytest.raises(OSError):
        await export_jobs.run_export_job(db, _Store(fail_upload=True), JOB, TENANT)
    (failed,) = db.touching("SET status = 'failed'")
    token = db.touching("SET status = 'running'")[0].params["tok"]
    assert failed.params["tok"] == token and "claim_token = :tok" in failed.sql
    assert "bucket gone" in failed.params["err"]


async def test_missing_object_storage_fails_the_job_honestly() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows())
    with pytest.raises(export_jobs.TrainingExportUnavailableError):
        await export_jobs.run_export_job(db, None, JOB, TENANT)
    (failed,) = db.touching("SET status = 'failed'")
    assert "object storage" in failed.params["err"]


async def test_job_held_by_a_live_worker_reports_busy() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows(claim=False, status_after_claim="running"))
    result = await export_jobs.run_export_job(db, _Store(), JOB, TENANT)
    assert result["status"] == "busy"
    assert not db.touching("FROM goals g")


async def test_finished_job_is_skipped_on_redelivery() -> None:
    db = RlsRecordingDb(rows_for=_worker_rows(claim=False, status_after_claim="complete"))
    result = await export_jobs.run_export_job(db, _Store(), JOB, TENANT)
    assert result["status"] == "skipped"


async def test_api_enqueue_failure_only_fails_a_still_queued_job() -> None:
    db = RlsRecordingDb()
    await export_jobs.mark_failed(db, TENANT, JOB, "enqueue failed")
    (failed,) = db.touching("SET status = 'failed'")
    assert "status = 'queued'" in failed.sql


# ── Celery task ───────────────────────────────────────────────────────────────


def test_task_is_registered_and_routed_to_maintenance() -> None:
    from app.scaling.celery_app import celery_app
    from app.training_export import tasks

    assert "app.training_export.tasks" in celery_app.conf.include
    assert "app.coordination.pattern_runs.tasks" in celery_app.conf.include
    routes = cast(Mapping[str, Mapping[str, str]], celery_app.conf.task_routes)
    assert routes[tasks.TASK_NAME]["queue"] == "maintenance"


class _RetryError(Exception):
    pass


def test_busy_job_is_retried_after_the_heartbeat_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training_export import tasks

    async def _busy(*_a: Any) -> dict[str, Any]:
        return {"job_id": JOB, "status": "busy"}

    monkeypatch.setattr(export_jobs, "run_export_job", _busy)
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: RlsRecordingDb())
    calls: list[dict[str, Any]] = []

    def _retry(**kw: Any) -> Exception:
        calls.append(kw)
        return _RetryError()

    task = tasks.run_training_export
    monkeypatch.setattr(task, "retry", _retry)
    task.push_request(retries=0)
    try:
        with pytest.raises(_RetryError):
            task.run(JOB, TENANT)
    finally:
        task.pop_request()
    assert calls and calls[0]["countdown"] >= export_jobs.STALE_HEARTBEAT_SECONDS


def test_busy_job_after_the_last_retry_is_left_to_its_live_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.training_export import tasks

    async def _busy(*_a: Any) -> dict[str, Any]:
        return {"job_id": JOB, "status": "busy"}

    monkeypatch.setattr(export_jobs, "run_export_job", _busy)
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: None)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: RlsRecordingDb())
    task = tasks.run_training_export
    task.push_request(retries=task.max_retries)
    try:
        assert task.run(JOB, TENANT)["status"] == "busy"
    finally:
        task.pop_request()


# ── API: active-job cap, download honesty ─────────────────────────────────────


def _client(db: Any) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.state.db_session_factory = db
    app.state.goal_service = SimpleNamespace(_goals={}, _eval_scores={})

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    return TestClient(app)


def test_create_is_429_when_the_tenant_has_too_many_active_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: _Store())
    queued: list[str] = []
    monkeypatch.setattr(api, "_enqueue", lambda j, t: queued.append(j))
    db = RlsRecordingDb()  # the capped INSERT ... SELECT inserts no row
    resp = _client(db).post("/intelligence/export-training-data/jobs")
    assert resp.status_code == 429
    assert queued == []
    (insert,) = db.touching("INSERT INTO training_export_jobs")
    assert insert.params["cap"] == export_jobs.MAX_ACTIVE_JOBS_PER_TENANT
    assert "pg_advisory_xact_lock" in " ".join(s.sql for s in db.statements)
    assert insert.tenant_guc == TENANT


def test_download_of_a_failed_job_is_409_with_its_status() -> None:
    db = RlsRecordingDb(
        rows_for=lambda sql, p: (
            [_job_row("failed", error="OSError: bucket gone")]
            if "FROM training_export_jobs" in sql
            else []
        )
    )
    resp = _client(db).get(f"/intelligence/export-training-data/jobs/{JOB}/download")
    assert resp.status_code == 409
    assert resp.json()["status"] == "failed"


def test_get_failed_job_reports_its_error_and_no_download() -> None:
    db = RlsRecordingDb(
        rows_for=lambda sql, p: (
            [_job_row("failed", error="OSError: bucket gone")]
            if "FROM training_export_jobs" in sql
            else []
        )
    )
    body = _client(db).get(f"/intelligence/export-training-data/jobs/{JOB}").json()
    assert body["status"] == "failed" and body["error"] == "OSError: bucket gone"
    assert "download_url" not in body and body["has_file"] is False


def test_unknown_job_is_404() -> None:
    resp = _client(RlsRecordingDb()).get(f"/intelligence/export-training-data/jobs/{JOB}")
    assert resp.status_code == 404


def test_download_with_unreadable_object_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: _Store(fail_open=True))
    db = RlsRecordingDb(
        rows_for=lambda sql, p: (
            [_job_row("complete", key="tenants/x/k.jsonl")]
            if "FROM training_export_jobs" in sql
            else []
        )
    )
    resp = _client(db).get(f"/intelligence/export-training-data/jobs/{JOB}/download")
    assert resp.status_code == 503


# ── step cap keeps the final answer ───────────────────────────────────────────


async def test_step_cap_keeps_the_final_step() -> None:
    from app.training_export.stream import iter_training_examples

    db = RlsRecordingDb(rows_for=_worker_rows(n_goals=1))
    [e async for e in iter_training_examples(db, TENANT, 0.8, 1)]
    (steps,) = db.touching("FROM goal_steps")
    # The capped window must always include the goal's last step (its answer).
    assert "rn_desc = 1" in steps.sql
