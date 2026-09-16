"""Coverage for ``app/api/observability.py`` — real-time logs (list + SSE
stream), structured metrics, time-series, and the per-goal trace endpoint.

Follows the project convention (see ``tests/triggers/test_api.py``) of a bare
FastAPI app with the router mounted and a middleware injecting
``request.state.tenant``. DB-backed branches are covered with a fake async
session (no live Postgres); the SSE generator is driven directly as an async
function so it terminates deterministically instead of looping forever.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import observability as obs

# NOTE: asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests;
# a module-level `pytestmark = pytest.mark.asyncio` would also tag the sync
# parametrized tests below, which pytest-asyncio warns about (and
# filterwarnings = ["error"] turns into a failure).


# ── App / client fixtures ──────────────────────────────────────────────────────


def _make_app(*, with_tenant: bool = True, goal_service: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(obs.router)
    app.state.goal_service = goal_service

    @app.middleware("http")
    async def inject_tenant(request, call_next):  # type: ignore[no-untyped-def]
        if with_tenant:
            request.state.tenant = SimpleNamespace(tenant_id="tenant-1", plan="free", api_key="k")
        return await call_next(request)

    return app


@pytest.fixture(autouse=True)
def _reset_log_store() -> None:
    """The router uses a module-level singleton; keep tests isolated."""
    obs.log_store._redis = None
    obs.log_store._memory_buffer = {}
    yield
    obs.log_store._redis = None
    obs.log_store._memory_buffer = {}


# ── _require_tenant / 401 ──────────────────────────────────────────────────────


async def test_logs_401_without_tenant() -> None:
    app = _make_app(with_tenant=False)
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 401


async def test_metrics_401_without_tenant() -> None:
    app = _make_app(with_tenant=False)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 401


async def test_timeseries_401_without_tenant() -> None:
    app = _make_app(with_tenant=False)
    client = TestClient(app)
    resp = client.get("/observability/timeseries")
    assert resp.status_code == 401


async def test_goal_trace_401_without_tenant() -> None:
    app = _make_app(with_tenant=False)
    client = TestClient(app)
    resp = client.get("/observability/goals/g1/trace")
    assert resp.status_code == 401


# ── list_logs ──────────────────────────────────────────────────────────────────


async def test_list_logs_from_memory_store() -> None:
    await obs.log_store.emit("tenant-1", "info", "hello", source="test")
    await obs.log_store.emit("tenant-1", "error", "boom", source="test")

    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "memory"
    assert data["total"] == 2
    assert {log["level"] for log in data["logs"]} == {"info", "error"}


async def test_list_logs_level_filter_memory() -> None:
    await obs.log_store.emit("tenant-1", "info", "hello")
    await obs.log_store.emit("tenant-1", "error", "boom")

    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/logs?level=error")
    assert resp.status_code == 200
    data = resp.json()
    assert all(log["level"] == "error" for log in data["logs"])


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
    obs.log_store._redis = fake_redis

    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "redis_stream"
    assert data["logs"][0]["message"] == "from redis"


async def test_list_logs_fallback_to_goal_events() -> None:
    goal_svc = AsyncMock()
    goal_svc.list_goals.return_value = {
        "goals": [{"id": "g1"}, {"goal_id": "g2"}],
    }

    async def _get_events(goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        if goal_id == "g1":
            return [
                {"type": "goal_complete", "ts": "2026-01-01T00:00:00Z"},
                {"type": "goal_failed", "reason": "timeout", "ts": "2026-01-01T00:01:00Z"},
            ]
        raise RuntimeError("boom")  # exercises the inner except Exception: pass

    goal_svc.get_events.side_effect = _get_events

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "goal_events"
    messages = {log["message"] for log in data["logs"]}
    assert "Goal completed successfully" in messages
    assert "Goal failed: timeout" in messages


async def test_list_logs_fallback_goal_service_list_goals_raises() -> None:
    goal_svc = AsyncMock()
    goal_svc.list_goals.side_effect = RuntimeError("down")

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    assert resp.json()["logs"] == []


async def test_list_logs_fallback_goals_as_plain_list() -> None:
    goal_svc = AsyncMock()
    goal_svc.list_goals.return_value = [{"id": "g1"}]
    goal_svc.get_events.return_value = [{"type": "unknown_event", "ts": "2026-01-01T00:00:00Z"}]

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logs"][0]["message"] == "Event: unknown_event"


async def test_list_logs_since_filter() -> None:
    await obs.log_store.emit("tenant-1", "info", "old")
    app = _make_app()
    client = TestClient(app)
    # Far-future `since` excludes everything.
    resp = client.get("/observability/logs?since=2099-01-01T00:00:00Z")
    assert resp.status_code == 200
    assert resp.json()["logs"] == []


async def test_list_logs_since_filter_malformed_is_ignored() -> None:
    await obs.log_store.emit("tenant-1", "info", "old")
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/logs?since=not-a-date")
    assert resp.status_code == 200
    assert len(resp.json()["logs"]) == 1


async def test_list_logs_limit_is_bounded() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/logs?limit=1000")
    assert resp.status_code == 422  # le=500


# ── _evt_to_message ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("evt", "expected"),
    [
        ({"type": "goal_complete"}, "Goal completed successfully"),
        ({"type": "goal_failed", "reason": "oops"}, "Goal failed: oops"),
        ({"type": "goal_cancelled"}, "Goal was cancelled"),
        ({"type": "plan_ready", "steps": [1, 2, 3]}, "Plan ready with 3 steps"),
        ({"type": "step_started", "step": "do the thing"}, "Started step: do the thing"),
        ({"type": "step_complete", "step": "did it"}, "Completed step: did it"),
        ({"type": "tool_call_complete", "tool_name": "search"}, "Tool call: search"),
        (
            {"type": "tool_call_failed", "tool_name": "search", "error": "timeout"},
            "Tool failed: search — timeout",
        ),
        ({"type": "goal_started"}, "Goal execution started"),
        ({"type": "approval_required"}, "Human approval required"),
        ({"type": "approval_granted"}, "Human approval granted"),
        ({"type": "mystery"}, "Event: mystery"),
    ],
)
def test_evt_to_message_mappings(evt: dict[str, Any], expected: str) -> None:
    assert obs._evt_to_message(evt) == expected


# ── stream_logs (SSE) ──────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, disconnect_after: int = 0) -> None:
        self.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id="tenant-1"))
        self.app = SimpleNamespace(state=SimpleNamespace(goal_service=None))
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


async def test_stream_logs_sends_connected_then_historical_then_stops() -> None:
    await obs.log_store.emit("tenant-1", "info", "hist-1")
    # 1 historical entry -> its is_disconnected check must pass (call #1), then
    # the while-loop's own check (call #2) trips and ends the stream.
    request = _FakeRequest(disconnect_after=1)
    response = await obs.stream_logs(request)  # type: ignore[arg-type]

    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert '"type": "connected"' in joined
    assert "hist-1" in joined


async def test_stream_logs_redis_path_yields_new_entries_then_disconnects() -> None:
    fake_redis = AsyncMock()
    obs.log_store._redis = fake_redis

    call_count = {"n": 0}

    async def _stream_new_since(tenant_id: str, last_id: str = "$") -> list[dict[str, Any]]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [{"id": "e1", "message": "new-entry", "_stream_id": "5-0"}]
        return []

    obs.log_store.stream_new_since = _stream_new_since  # type: ignore[method-assign]
    obs.log_store.query = AsyncMock(return_value=[])  # type: ignore[method-assign]

    # Checks: #1 top-of-while, #2 before yielding the one new entry, #3 back at
    # top-of-while on the next iteration (ends the stream).
    request = _FakeRequest(disconnect_after=2)
    response = await obs.stream_logs(request)  # type: ignore[arg-type]
    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert "new-entry" in joined


async def test_stream_logs_no_redis_emits_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(obs.asyncio, "sleep", _no_sleep)
    request = _FakeRequest(disconnect_after=1)
    request.app.state.goal_service = None
    response = await obs.stream_logs(request)  # type: ignore[arg-type]
    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert "heartbeat" in joined


async def test_stream_logs_disconnect_during_historical_burst_returns_early() -> None:
    await obs.log_store.emit("tenant-1", "info", "hist-1")
    await obs.log_store.emit("tenant-1", "info", "hist-2")

    class _ImmediateDisconnect(_FakeRequest):
        async def is_disconnected(self) -> bool:
            self._calls += 1
            return True  # disconnected from the very first check

    request = _ImmediateDisconnect()
    response = await obs.stream_logs(request)  # type: ignore[arg-type]
    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
    assert '"type": "connected"' in joined
    # Returned before yielding any historical entry.
    assert "hist-1" not in joined


# ── get_structured_metrics ─────────────────────────────────────────────────────


async def test_metrics_no_goal_service_returns_defaults() -> None:
    app = _make_app(goal_service=None)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_goals"] == 0
    assert data["latency_percentiles"] == {"p50": 0, "p95": 0, "p99": 0}


class _FakeResult:
    def __init__(self, *, fetchone: Any = None, fetchall: Any = None) -> None:
        self._fetchone = fetchone
        self._fetchall = fetchall or []

    def fetchone(self) -> Any:
        return self._fetchone

    def fetchall(self) -> Any:
        return self._fetchall


class _FakeDBSession:
    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = list(results)

    async def __aenter__(self) -> _FakeDBSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
        if self._results:
            return self._results.pop(0)
        return _FakeResult()


def _fake_db(batches: list[list[_FakeResult]]) -> Any:
    queue = list(batches)

    def _factory() -> _FakeDBSession:
        results = queue.pop(0) if queue else []
        return _FakeDBSession(results)

    return _factory


async def test_metrics_db_backed_percentiles_and_token_usage() -> None:
    percentile_row = _FakeResult(fetchone=(10, 0.9, 100.0, 200.0, 300.0))
    token_rows = _FakeResult(fetchall=[("anthropic", 500), ("openai", 100)])
    goal_svc = SimpleNamespace(_db=_fake_db([[_FakeResult(), percentile_row], [token_rows]]))

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_goals"] == 10
    assert data["success_rate"] == 0.9
    assert data["latency_percentiles"] == {"p50": 100, "p95": 200, "p99": 300}
    assert data["token_usage_by_provider"] == [
        {"label": "anthropic", "value": 500},
        {"label": "openai", "value": 100},
    ]


async def test_metrics_db_query_raises_falls_back_to_memory() -> None:
    class _RaisingDB:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    goal_svc = AsyncMock()
    goal_svc._db = _RaisingDB()
    goal_svc.get_metrics.return_value = {"total_goals": 3, "success_rate": 0.5}
    goal_svc._goal_durations = {"tenant-1": [1.0, 2.0, 3.0]}
    goal_svc._metrics_cache = {}

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_goals"] == 3
    assert data["success_rate"] == 0.5
    assert data["latency_percentiles"]["p50"] > 0


async def test_metrics_no_db_uses_in_memory_fallback_and_metrics_cache() -> None:
    goal_svc = AsyncMock()
    goal_svc._db = None
    goal_svc.get_metrics.return_value = {"total_goals": 2, "success_rate": 1.0}
    goal_svc._goal_durations = {}
    goal_svc._metrics_cache = {
        "tenant-1": {"token_usage_by_provider": [{"label": "anthropic", "value": 42}]}
    }

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_goals"] == 2
    assert data["token_usage_by_provider"] == [{"label": "anthropic", "value": 42}]


async def test_metrics_get_metrics_raises_is_swallowed() -> None:
    goal_svc = AsyncMock()
    goal_svc._db = None
    goal_svc.get_metrics.side_effect = RuntimeError("boom")

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/metrics")
    assert resp.status_code == 200
    assert resp.json()["total_goals"] == 0


# ── get_timeseries ──────────────────────────────────────────────────────────────


async def test_timeseries_no_goal_service_returns_empty() -> None:
    app = _make_app(goal_service=None)
    client = TestClient(app)
    resp = client.get("/observability/timeseries")
    assert resp.status_code == 200
    assert resp.json() == {
        "goals_per_hour": [],
        "cost_per_hour": [],
        "avg_latency_per_hour": [],
    }


async def test_timeseries_in_memory_mode_buckets_by_hour() -> None:
    record_ok = SimpleNamespace(
        tenant_id="tenant-1",
        created_at="2026-01-01T10:15:00Z",
        status=SimpleNamespace(value="complete"),
        cost_usd=0.25,
    )
    record_failed = SimpleNamespace(
        tenant_id="tenant-1",
        created_at="2026-01-01T10:45:00Z",
        status="failed",
        cost_usd=0.0,
    )
    record_other_tenant = SimpleNamespace(
        tenant_id="tenant-2",
        created_at="2026-01-01T10:50:00Z",
        status="complete",
        cost_usd=1.0,
    )
    record_bad_ts = SimpleNamespace(
        tenant_id="tenant-1", created_at="not-a-date", status="complete", cost_usd=0.0
    )
    goal_svc = SimpleNamespace(
        _db=None,
        _goals={
            "g1": record_ok,
            "g2": record_failed,
            "g3": record_other_tenant,
            "g4": record_bad_ts,
        },
    )

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/timeseries?bucket=hour")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["goals_per_hour"]) == 1
    bucket = data["goals_per_hour"][0]
    assert bucket["count"] == 2
    assert bucket["success"] == 1
    assert bucket["failed"] == 1
    assert data["cost_per_hour"][0]["cost_usd"] == 0.25


async def test_timeseries_in_memory_mode_since_until_and_minute_day_buckets() -> None:
    record = SimpleNamespace(
        tenant_id="tenant-1",
        created_at="2026-01-01T10:15:30Z",
        status="complete",
        cost_usd=1.0,
    )
    goal_svc = SimpleNamespace(_db=None, _goals={"g1": record})
    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)

    resp_minute = client.get("/observability/timeseries?bucket=minute")
    assert resp_minute.json()["goals_per_hour"][0]["ts"] == "2026-01-01T10:15:00Z"

    resp_day = client.get("/observability/timeseries?bucket=day")
    assert resp_day.json()["goals_per_hour"][0]["ts"] == "2026-01-01T00:00:00Z"

    # since in the future excludes the record.
    resp_since = client.get("/observability/timeseries?since=2099-01-01T00:00:00Z")
    assert resp_since.json()["goals_per_hour"] == []

    # until in the past excludes the record.
    resp_until = client.get("/observability/timeseries?until=2000-01-01T00:00:00Z")
    assert resp_until.json()["goals_per_hour"] == []

    # malformed since/until are ignored (record still included).
    resp_bad = client.get("/observability/timeseries?since=nope&until=nope")
    assert len(resp_bad.json()["goals_per_hour"]) == 1


async def test_timeseries_db_backed_mode() -> None:
    import datetime as _dt

    goals_result = _FakeResult(
        fetchall=[(_dt.datetime(2026, 1, 1, 10, tzinfo=_dt.UTC), 5, 4, 1)]
    )
    cost_result = _FakeResult(fetchall=[(_dt.datetime(2026, 1, 1, 10, tzinfo=_dt.UTC), 1.2345)])
    lat_result = _FakeResult(
        fetchall=[(_dt.datetime(2026, 1, 1, 10, tzinfo=_dt.UTC), 123.456, 456.789)]
    )
    goal_svc = SimpleNamespace(
        _db=_fake_db([[_FakeResult(), goals_result, cost_result, lat_result]])
    )

    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/timeseries?bucket=hour")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goals_per_hour"][0]["count"] == 5
    assert data["cost_per_hour"][0]["cost_usd"] == 1.2345
    assert data["avg_latency_per_hour"][0]["p50_ms"] == 123
    assert data["avg_latency_per_hour"][0]["p95_ms"] == 457


async def test_timeseries_db_backed_mode_query_raises_returns_empty_arrays() -> None:
    class _RaisingDB:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    goal_svc = SimpleNamespace(_db=_RaisingDB())
    app = _make_app(goal_service=goal_svc)
    client = TestClient(app)
    resp = client.get("/observability/timeseries")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"goals_per_hour": [], "cost_per_hour": [], "avg_latency_per_hour": []}


# ── get_goal_trace ──────────────────────────────────────────────────────────────


async def test_goal_trace_computes_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.observability.run_timeline import InMemoryRunTimelineStore

    store = InMemoryRunTimelineStore()
    store.append(
        "tenant-1",
        "goal-1",
        {
            "name": "gen_ai.chat",
            "cost_usd": "0.001",
            "input_tokens": "100",
            "output_tokens": "50",
        },
    )
    store.append(
        "tenant-1",
        "goal-1",
        {"name": "tool_call", "cost_usd": 0.002, "input_tokens": 0, "output_tokens": 0},
    )
    import app.observability.tracing as tracing_mod

    monkeypatch.setattr(tracing_mod, "get_run_timeline_store", lambda: store)

    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/goals/goal-1/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == "goal-1"
    assert len(data["entries"]) == 2
    assert data["summary"]["steps"] == 2
    assert data["summary"]["generations"] == 1
    assert data["summary"]["total_cost_usd"] == pytest.approx(0.003)
    assert data["summary"]["total_input_tokens"] == 100
    assert data["summary"]["total_output_tokens"] == 50


async def test_goal_trace_empty_when_no_entries() -> None:
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/observability/goals/unknown-goal/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["summary"]["steps"] == 0
    assert data["summary"]["total_cost_usd"] == 0
