"""Coverage for ``app/api/observability.py`` — real-time logs (list + SSE
stream), structured metrics, time-series, and the per-goal trace endpoint.

Follows the project convention (see ``tests/triggers/test_api.py``) of a bare
FastAPI app with the router mounted and a middleware injecting
``request.state.tenant``. DB-backed branches are covered with a fake async
session (no live Postgres) that records the statements it is handed; the SSE
generator is driven directly so it terminates deterministically.

Contract (fail closed — never fabricate):
* metrics / timeseries need the Postgres backend: 501 in in-memory mode, 503 when
  the database errors (the old code swallowed errors into zeros and queried
  columns that do not exist);
* logs come from the tenant log store fed by the structlog pipeline; a store
  failure is a 503, not an empty list;
* goal traces are served only where the process-local timeline is authoritative.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql.elements import TextClause

from app.api import observability as obs

# NOTE: asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests;
# a module-level `pytestmark = pytest.mark.asyncio` would also tag the sync
# parametrized tests below, which pytest-asyncio warns about (and
# filterwarnings = ["error"] turns into a failure).

_TENANT = "3f1c2a9e-5b7d-4c8e-9f0a-1b2c3d4e5f60"


# ── App / client fixtures ──────────────────────────────────────────────────────


def _make_app(
    *,
    with_tenant: bool = True,
    goal_service: Any = None,
    db: Any = None,
    tenant_id: str = _TENANT,
) -> FastAPI:
    app = FastAPI()
    app.include_router(obs.router)
    app.state.goal_service = goal_service
    app.state.db_session_factory = db

    @app.middleware("http")
    async def inject_tenant(request, call_next):  # type: ignore[no-untyped-def]
        if with_tenant:
            request.state.tenant = SimpleNamespace(tenant_id=tenant_id, plan="free", api_key="k")
        return await call_next(request)

    return app


@pytest.fixture(autouse=True)
def _reset_log_store() -> None:
    """The router uses a module-level singleton; keep tests isolated."""
    obs.log_store.reset()
    yield
    obs.log_store.reset()


# ── _require_tenant / 401 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/observability/logs",
        "/observability/metrics",
        "/observability/timeseries",
        "/observability/goals/g1/trace",
    ],
)
def test_401_without_tenant(path: str) -> None:
    client = TestClient(_make_app(with_tenant=False))
    assert client.get(path).status_code == 401


# ── list_logs ──────────────────────────────────────────────────────────────────


async def test_list_logs_from_memory_store() -> None:
    await obs.log_store.emit(_TENANT, "info", "hello", source="test")
    await obs.log_store.emit(_TENANT, "error", "boom", source="test")

    client = TestClient(_make_app())
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "memory"
    assert data["total"] == 2
    assert {log["level"] for log in data["logs"]} == {"info", "error"}


async def test_list_logs_is_tenant_scoped() -> None:
    await obs.log_store.emit(_TENANT, "info", "mine")
    await obs.log_store.emit("other-tenant", "info", "theirs")

    data = TestClient(_make_app()).get("/observability/logs").json()
    assert [log["message"] for log in data["logs"]] == ["mine"]


async def test_list_logs_level_filter_memory() -> None:
    await obs.log_store.emit(_TENANT, "info", "hello")
    await obs.log_store.emit(_TENANT, "error", "boom")

    resp = TestClient(_make_app()).get("/observability/logs?level=error")
    assert resp.status_code == 200
    data = resp.json()
    assert [log["message"] for log in data["logs"]] == ["boom"]


async def test_list_logs_redis_backed() -> None:
    fake_redis = AsyncMock()
    fake_redis.xrevrange.return_value = [
        (
            "1-0",
            {
                b"id": b"1-0",
                b"timestamp": b"2026-01-01T00:00:00+00:00",
                b"level": b"info",
                b"message": b"from redis",
                b"source": b"x",
                b"goal_id": b"g1",
            },
        )
    ]
    obs.log_store.set_redis(fake_redis)

    resp = TestClient(_make_app()).get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "redis_stream"
    assert data["logs"][0]["message"] == "from redis"


async def test_list_logs_redis_failure_is_503_not_empty() -> None:
    """Regression: a Redis error used to fall through to an empty memory buffer."""
    fake_redis = AsyncMock()
    fake_redis.xrevrange.side_effect = ConnectionError("redis down")
    obs.log_store.set_redis(fake_redis)

    resp = TestClient(_make_app()).get("/observability/logs")
    assert resp.status_code == 503
    assert "log store" in resp.json()["detail"].lower()


async def test_list_logs_does_not_fabricate_logs_from_goal_events() -> None:
    """The endpoint serves the log store only — no synthesised 'logs' from events."""
    goal_svc = AsyncMock()
    goal_svc.list_goals.return_value = {"goals": [{"id": "g1"}]}
    goal_svc.get_events.return_value = [{"type": "goal_complete", "ts": "2026-01-01T00:00:00Z"}]

    resp = TestClient(_make_app(goal_service=goal_svc)).get("/observability/logs")
    assert resp.status_code == 200
    assert resp.json()["logs"] == []
    goal_svc.list_goals.assert_not_called()


async def test_list_logs_since_filter() -> None:
    await obs.log_store.emit(_TENANT, "info", "old")
    resp = TestClient(_make_app()).get("/observability/logs?since=2099-01-01T00:00:00Z")
    assert resp.status_code == 200
    assert resp.json()["logs"] == []


async def test_list_logs_since_filter_malformed_is_422() -> None:
    await obs.log_store.emit(_TENANT, "info", "old")
    resp = TestClient(_make_app()).get("/observability/logs?since=not-a-date")
    assert resp.status_code == 422


def test_list_logs_limit_is_bounded() -> None:
    resp = TestClient(_make_app()).get("/observability/logs?limit=1000")
    assert resp.status_code == 422  # le=500


async def test_logs_endpoint_serves_records_fed_by_structlog_pipeline() -> None:
    """End to end: a structlog line with a bound tenant shows up in /logs."""
    import structlog

    from app.observability.logging import feed_tenant_log_store

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tenant_id=_TENANT, goal_id="g-42")
    try:
        feed_tenant_log_store(None, "warning", {"event": "tool_retry", "level": "warning"})
    finally:
        structlog.contextvars.clear_contextvars()

    data = TestClient(_make_app()).get("/observability/logs").json()
    assert data["total"] == 1
    entry = data["logs"][0]
    assert entry["message"] == "tool_retry"
    assert entry["level"] == "warning"
    assert entry["goal_id"] == "g-42"


# ── stream_logs (SSE) ──────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, disconnect_after: int = 0) -> None:
        self.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id=_TENANT))
        self.app = SimpleNamespace(state=SimpleNamespace(goal_service=None))
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


async def _drain(response: Any) -> str:
    chunks = [chunk async for chunk in response.body_iterator]
    return "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)


async def test_stream_logs_sends_connected_then_historical_then_stops() -> None:
    await obs.log_store.emit(_TENANT, "info", "hist-1")
    request = _FakeRequest(disconnect_after=1)
    joined = await _drain(await obs.stream_logs(request))  # type: ignore[arg-type]
    assert '"type": "connected"' in joined
    assert "hist-1" in joined


async def test_stream_logs_redis_path_yields_new_entries_then_disconnects() -> None:
    obs.log_store.set_redis(AsyncMock())
    call_count = {"n": 0}

    async def _stream_new_since(tenant_id: str, last_id: str = "$") -> list[dict[str, Any]]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"id": "e1", "message": "new-entry", "_stream_id": "5-0"}]
        return []

    obs.log_store.stream_new_since = _stream_new_since  # type: ignore[method-assign]
    obs.log_store.query = AsyncMock(return_value=[])  # type: ignore[method-assign]

    request = _FakeRequest(disconnect_after=2)
    joined = await _drain(await obs.stream_logs(request))  # type: ignore[arg-type]
    assert "new-entry" in joined


async def test_stream_logs_redis_error_emits_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(obs.asyncio, "sleep", _no_sleep)
    obs.log_store.set_redis(AsyncMock())
    obs.log_store.query = AsyncMock(return_value=[])  # type: ignore[method-assign]
    obs.log_store.stream_new_since = AsyncMock(  # type: ignore[method-assign]
        side_effect=ConnectionError("redis down")
    )
    request = _FakeRequest(disconnect_after=1)
    joined = await _drain(await obs.stream_logs(request))  # type: ignore[arg-type]
    assert '"type": "error"' in joined


async def test_stream_logs_initial_query_error_emits_error_and_ends() -> None:
    obs.log_store.query = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    request = _FakeRequest(disconnect_after=100)
    joined = await _drain(await obs.stream_logs(request))  # type: ignore[arg-type]
    assert '"type": "error"' in joined


async def test_stream_logs_no_redis_emits_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(obs.asyncio, "sleep", _no_sleep)
    request = _FakeRequest(disconnect_after=1)
    joined = await _drain(await obs.stream_logs(request))  # type: ignore[arg-type]
    assert "heartbeat" in joined


async def test_stream_logs_disconnect_during_historical_burst_returns_early() -> None:
    await obs.log_store.emit(_TENANT, "info", "hist-1")
    await obs.log_store.emit(_TENANT, "info", "hist-2")

    class _ImmediateDisconnect(_FakeRequest):
        async def is_disconnected(self) -> bool:
            return True

    joined = await _drain(await obs.stream_logs(_ImmediateDisconnect()))  # type: ignore[arg-type]
    assert '"type": "connected"' in joined
    assert "hist-1" not in joined


# ── Fake tenant-scoped DB ──────────────────────────────────────────────────────


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def one(self) -> dict[str, Any]:
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeTx:
    async def __aenter__(self) -> _FakeTx:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeDBSession:
    """Answers Core SELECTs from a queue; records the RLS ``set_config`` calls."""

    def __init__(self, owner: _FakeDB) -> None:
        self._owner = owner

    async def __aenter__(self) -> _FakeDBSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> _FakeTx:
        return _FakeTx()

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        if isinstance(stmt, TextClause):
            self._owner.guc_calls.append(dict(params or {}))
            return _FakeResult()
        if self._owner.error is not None:
            raise self._owner.error
        self._owner.statements.append(stmt)
        return self._owner.results.pop(0) if self._owner.results else _FakeResult()


class _FakeDB:
    def __init__(
        self, results: list[_FakeResult] | None = None, error: Exception | None = None
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.statements: list[Any] = []
        self.guc_calls: list[dict[str, Any]] = []

    def __call__(self) -> _FakeDBSession:
        return _FakeDBSession(self)


# ── get_structured_metrics ─────────────────────────────────────────────────────


def test_metrics_in_memory_mode_is_501_not_zeros() -> None:
    """No Postgres → the endpoint says so instead of returning a dashboard of zeros."""
    resp = TestClient(_make_app(db=None)).get("/observability/metrics")
    assert resp.status_code == 501
    assert "postgres" in resp.json()["detail"].lower()


def test_metrics_db_backed_percentiles_and_token_usage() -> None:
    summary = _FakeResult(
        [{"total": 10, "completed": 9, "finished": 10, "p50_s": 0.1, "p95_s": 0.2, "p99_s": 0.3}]
    )
    tokens = _FakeResult(
        [{"model": "claude-x", "tokens": 500}, {"model": "gpt-y", "tokens": 100}]
    )
    db = _FakeDB([summary, tokens])

    resp = TestClient(_make_app(db=db)).get("/observability/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_goals"] == 10
    assert data["success_rate"] == 0.9
    assert data["latency_percentiles"] == {"p50": 100, "p95": 200, "p99": 300}
    assert data["goal_duration_percentiles"] == [
        {"percentile": "p50", "ms": 100},
        {"percentile": "p95", "ms": 200},
        {"percentile": "p99", "ms": 300},
    ]
    assert data["token_usage_by_provider"] == [
        {"label": "claude-x", "value": 500},
        {"label": "gpt-y", "value": 100},
    ]
    # Every query ran under the tenant's RLS GUC.
    assert {"tid": _TENANT} in db.guc_calls


def test_metrics_no_finished_goals_reports_null_not_zero() -> None:
    summary = _FakeResult(
        [{"total": 2, "completed": 0, "finished": 0, "p50_s": None, "p95_s": None, "p99_s": None}]
    )
    db = _FakeDB([summary, _FakeResult([])])
    data = TestClient(_make_app(db=db)).get("/observability/metrics").json()
    assert data["total_goals"] == 2
    assert data["success_rate"] is None
    assert data["latency_percentiles"] == {"p50": None, "p95": None, "p99": None}
    assert data["goal_duration_percentiles"] == []


def test_metrics_db_error_is_503() -> None:
    """Regression: a DB error (e.g. UndefinedColumn) used to be swallowed into zeros."""
    db = _FakeDB(error=RuntimeError('column "duration_s" does not exist'))
    resp = TestClient(_make_app(db=db)).get("/observability/metrics")
    assert resp.status_code == 503
    assert "database" in resp.json()["detail"].lower()


def test_metrics_malformed_since_is_422() -> None:
    resp = TestClient(_make_app(db=_FakeDB())).get("/observability/metrics?since=nope")
    assert resp.status_code == 422


# ── get_timeseries ──────────────────────────────────────────────────────────────


def test_timeseries_in_memory_mode_is_501_not_fabricated() -> None:
    """Regression: in-memory mode summed a ``cost_usd`` GoalRecord never has → zeros."""
    goal_svc = SimpleNamespace(_db=None, _goals={})
    resp = TestClient(_make_app(goal_service=goal_svc, db=None)).get("/observability/timeseries")
    assert resp.status_code == 501


def test_timeseries_db_backed_mode() -> None:
    ts = _dt.datetime(2026, 1, 1, 10, tzinfo=_dt.UTC)
    goals_result = _FakeResult([{"bucket": ts, "total": 5, "success": 4, "failed": 1}])
    lat_result = _FakeResult([{"bucket": ts, "p50_s": 0.123456, "p95_s": 0.456789}])
    cost_result = _FakeResult([{"bucket": ts, "total_cost": 1.2345}])
    db = _FakeDB([goals_result, lat_result, cost_result])

    resp = TestClient(_make_app(db=db)).get("/observability/timeseries?bucket=hour")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goals_per_hour"] == [
        {"ts": ts.isoformat(), "count": 5, "success": 4, "failed": 1}
    ]
    assert data["cost_per_hour"] == [{"ts": ts.isoformat(), "cost_usd": 1.2345}]
    assert data["avg_latency_per_hour"] == [{"ts": ts.isoformat(), "p50_ms": 123, "p95_ms": 457}]
    assert len(db.statements) == 3
    assert {"tid": _TENANT} in db.guc_calls


def test_timeseries_non_uuid_tenant_skips_cost_ledger() -> None:
    """goal_cost_breakdowns.tenant_id is UUID; a non-UUID tenant can own no rows."""
    db = _FakeDB([_FakeResult([]), _FakeResult([])])
    resp = TestClient(_make_app(db=db, tenant_id="legacy-tenant")).get("/observability/timeseries")
    assert resp.status_code == 200
    assert resp.json()["cost_per_hour"] == []
    assert len(db.statements) == 2


def test_timeseries_db_error_is_503() -> None:
    db = _FakeDB(error=RuntimeError("db down"))
    resp = TestClient(_make_app(db=db)).get("/observability/timeseries")
    assert resp.status_code == 503


@pytest.mark.parametrize("qs", ["since=nope", "until=nope", "bucket=week"])
def test_timeseries_bad_params_are_422(qs: str) -> None:
    resp = TestClient(_make_app(db=_FakeDB())).get(f"/observability/timeseries?{qs}")
    assert resp.status_code == 422


def test_timeseries_since_after_until_is_422() -> None:
    resp = TestClient(_make_app(db=_FakeDB())).get(
        "/observability/timeseries?since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z"
    )
    assert resp.status_code == 422


# ── get_goal_trace ──────────────────────────────────────────────────────────────


async def test_goal_trace_computes_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.observability.run_timeline import InMemoryRunTimelineStore

    store = InMemoryRunTimelineStore()
    store.append(
        _TENANT,
        "goal-1",
        {"name": "gen_ai.chat", "cost_usd": "0.001", "input_tokens": "100", "output_tokens": "50"},
    )
    store.append(
        _TENANT,
        "goal-1",
        {"name": "tool_call", "cost_usd": 0.002, "input_tokens": 0, "output_tokens": 0},
    )
    import app.observability.tracing as tracing_mod

    monkeypatch.setattr(tracing_mod, "get_run_timeline_store", lambda: store)

    resp = TestClient(_make_app()).get("/observability/goals/goal-1/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == "goal-1"
    assert data["scope"] == "process"
    assert len(data["entries"]) == 2
    assert data["summary"]["steps"] == 2
    assert data["summary"]["generations"] == 1
    assert data["summary"]["total_cost_usd"] == pytest.approx(0.003)
    assert data["summary"]["total_input_tokens"] == 100
    assert data["summary"]["total_output_tokens"] == 50


def test_goal_trace_empty_when_no_entries() -> None:
    resp = TestClient(_make_app()).get("/observability/goals/unknown-goal/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["summary"]["steps"] == 0


def test_goal_trace_501_when_goals_run_on_distributed_workers() -> None:
    """Goals dispatched to Celery run in another process: this replica's in-process
    timeline would be empty/partial, so the endpoint refuses rather than mislead."""
    goal_svc = SimpleNamespace(_db=None, _task_queue=object())
    resp = TestClient(_make_app(goal_service=goal_svc)).get("/observability/goals/g1/trace")
    assert resp.status_code == 501
    assert "replica" in resp.json()["detail"].lower()


def test_goal_trace_501_in_durable_multi_replica_mode() -> None:
    resp = TestClient(_make_app(db=_FakeDB())).get("/observability/goals/g1/trace")
    assert resp.status_code == 501
