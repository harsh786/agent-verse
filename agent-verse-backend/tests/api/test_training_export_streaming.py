"""OPS-37: the training export streams keyset batches and large exports are
durable jobs — nothing materialises the whole export in memory."""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import training_export as api
from app.tenancy.context import PlanTier, TenantContext
from app.training_export import jobs as export_jobs
from app.training_export.stream import iter_training_examples
from tests._rls_recorder import RlsRecordingDb

TENANT = "t-stream"
_T0 = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _goal_rows(n: int) -> list[tuple[Any, ...]]:
    # newest first: (id, goal_text, created_at, score)
    return [(f"g{i:04d}", f"goal {i}", _T0 - dt.timedelta(seconds=i), 0.9) for i in range(n)]


def _paged_rows(all_rows: list[tuple[Any, ...]]) -> Any:
    def rows_for(sql: str, p: dict[str, Any]) -> list[Any]:
        if "FROM goals g" in sql and not sql.startswith("SELECT COUNT(*)"):
            rows = all_rows
            if "after_ts" in p:
                rows = [r for r in rows if (r[2], r[0]) < (p["after_ts"], p["after_id"])]
            return rows[: p["lim"]]
        if "FROM goal_steps" in sql:
            return [(gid, "out", [{"tool_name": "t"}]) for gid in p["gids"]]
        return []

    return rows_for


async def test_iterator_reads_keyset_batches_and_holds_one_batch() -> None:
    db = RlsRecordingDb(rows_for=_paged_rows(_goal_rows(25)))
    seen = [e["goal_id"] async for e in iter_training_examples(db, TENANT, 0.8, 23, batch_size=10)]
    assert seen == [f"g{i:04d}" for i in range(23)]
    goal_queries = db.touching("FROM goals g")
    # 10 + 10 + 3: three bounded batches, each its own query with a LIMIT.
    assert [q.params["lim"] for q in goal_queries] == [10, 10, 3]
    assert "after_ts" not in goal_queries[0].params
    assert goal_queries[1].params["after_id"] == "g0009"
    assert all(q.tenant_guc == TENANT for q in goal_queries)
    steps = db.touching("FROM goal_steps")
    assert all("LEFT(output" in q.sql and "rn < :max_steps OR rn_desc = 1" in q.sql for q in steps)


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


def test_export_streams_every_line_from_batches() -> None:
    db = RlsRecordingDb(rows_for=_paged_rows(_goal_rows(450)))
    resp = _client(db).post("/intelligence/export-training-data?limit=450")
    assert resp.status_code == 200
    lines = resp.text.split("\n")
    assert len(lines) == 450
    assert json.loads(lines[0])["messages"][1]["content"] == "goal 0"
    assert len(db.touching("FROM goals g")) == 3  # 200 + 200 + 50


def test_preview_is_an_aggregate_not_a_collect() -> None:
    def rows_for(sql: str, p: dict[str, Any]) -> list[Any]:
        if sql.startswith("SELECT COUNT(*)"):
            # count, avg, min, max, then the five histogram buckets (a10-F234-04)
            return [(5000, 0.9, 0.81, 0.99, 0, 10, 20, 30, 40)]
        return _paged_rows(_goal_rows(5000))(sql, p)

    db = RlsRecordingDb(rows_for=rows_for)
    body = _client(db).get("/intelligence/export-training-data/preview?limit=10000").json()
    assert body["count"] == 5000
    assert len(body["samples"]) == 3
    sample_queries = [q for q in db.touching("FROM goals g") if "COUNT(*)" not in q.sql]
    assert [q.params["lim"] for q in sample_queries] == [3]


# ── jobs ──────────────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}


def test_job_create_needs_object_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: None)
    resp = _client(RlsRecordingDb()).post("/intelligence/export-training-data/jobs")
    assert resp.status_code == 503


def test_job_create_enqueue_failure_is_503_and_job_marked_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: _FakeStore())

    def _rows(sql: str, p: dict[str, Any]) -> list[Any]:
        if sql.startswith("INSERT INTO training_export_jobs"):
            return [(p["id"], TENANT, "queued", p["fmt"], p["min_score"], p["lim"],
                     None, None, None, _T0, None, None)]
        return []

    def _boom(job_id: str, tenant_id: str) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(api, "_enqueue", _boom)
    db = RlsRecordingDb(rows_for=_rows)
    resp = _client(db).post("/intelligence/export-training-data/jobs?limit=50000")
    assert resp.status_code == 503
    (upd,) = db.touching("UPDATE training_export_jobs")
    assert "status = 'failed'" in upd.sql and upd.tenant_guc == TENANT


def test_job_create_queues_and_returns_202(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: _FakeStore())
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(api, "_enqueue", lambda j, t: queued.append((j, t)))

    def _rows(sql: str, p: dict[str, Any]) -> list[Any]:
        if sql.startswith("INSERT INTO training_export_jobs"):
            return [(p["id"], TENANT, "queued", p["fmt"], p["min_score"], p["lim"],
                     None, None, None, _T0, None, None)]
        return []

    resp = _client(RlsRecordingDb(rows_for=_rows)).post(
        "/intelligence/export-training-data/jobs?format=anthropic&limit=200000"
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued" and body["limit"] == 200000
    assert queued == [(body["job_id"], TENANT)]


def test_jobs_need_the_database() -> None:
    app_client = _client(None)
    assert app_client.post("/intelligence/export-training-data/jobs").status_code == 503
