"""Functional/contract coverage boost for app/api/enterprise.py.

Targets currently-uncovered endpoints and branches: experiment rollback,
benchmarks (DB-backed), prompt-variant A/B CRUD, async GDPR export error
paths, contract signing failure paths, SAML login/metadata error paths,
SAML connectivity test, SCIM handler construction + full user CRUD, and
SCIM token provisioning failure.

Convention follows tests/api/test_enterprise_extra3.py: a FastAPI app is
built per-test with TenantMiddleware for auth, and app.state.* wired with
either real lightweight services or MagicMock/AsyncMock stand-ins.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import (
    compliance_router,
    intelligence_router,
    marketplace_router,
    scim_router,
)
from app.api.enterprise import (
    router as enterprise_router,
)
from app.intelligence.prompt_optimizer import PromptOptimizer
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-boost",
    plan=PlanTier.ENTERPRISE,
    api_key_id="kid-boost",
    roles=("admin",),
)
_VALID_KEY = "av_test_boost_key"


def _headers() -> dict[str, str]:
    return {"X-API-Key": _VALID_KEY}


def _make_app(**state: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(enterprise_router)
    app.include_router(marketplace_router)
    app.include_router(intelligence_router)
    app.include_router(compliance_router)
    app.include_router(scim_router)

    for key, value in state.items():
        setattr(app.state, key, value)
    return app


# ---------------------------------------------------------------------------
# Generic fake async DB session/factory — SQL-keyword routed.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list | None = None, scalar_val: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar_val

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return self._rows

    def scalar(self) -> Any:
        return self._scalar


class _FakeSession:
    """Routes `execute(text_stmt, params)` to a handler based on SQL keywords.

    `router` is a list of (keyword, callable(params) -> _FakeResult) pairs,
    checked in order; the first substring match wins. A statement matching
    none returns an empty _FakeResult (no rows).
    """

    def __init__(self, router: list[tuple[str, Any]], raise_on: str | None = None) -> None:
        self.router = router
        self.raise_on = raise_on
        self.executed: list[str] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> Any:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=cm)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def commit(self) -> None:
        return None

    async def execute(self, stmt: Any, params: dict | None = None) -> _FakeResult:
        text = str(stmt)
        self.executed.append(text)
        if self.raise_on and self.raise_on in text:
            raise RuntimeError(f"simulated DB failure for: {self.raise_on}")
        for keyword, handler in self.router:
            if keyword in text:
                return handler(params or {})
        return _FakeResult()


def _db_factory(session: _FakeSession) -> Any:
    return MagicMock(return_value=session)


# ---------------------------------------------------------------------------
# rollback_experiment — full success/failure/not-found matrix
# ---------------------------------------------------------------------------


class TestRollbackExperiment:
    def test_no_self_opt_v2_is_503(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/experiments/e1/rollback", json={}, headers=_headers()
        )
        assert resp.status_code == 503

    def test_experiment_not_found_is_404(self) -> None:
        opt_v2 = MagicMock()
        opt_v2.list_experiments = AsyncMock(return_value=[])
        app = _make_app(self_optimizer_v2=opt_v2)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/experiments/missing/rollback", json={}, headers=_headers()
        )
        assert resp.status_code == 404

    def test_list_experiments_raises_treated_as_not_found(self) -> None:
        """If listing itself blows up, the experiment lookup fails closed to 404."""
        opt_v2 = MagicMock()
        opt_v2.list_experiments = AsyncMock(side_effect=RuntimeError("boom"))
        app = _make_app(self_optimizer_v2=opt_v2)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/experiments/e1/rollback", json={}, headers=_headers()
        )
        assert resp.status_code == 404

    def test_rollback_failure_is_400(self) -> None:
        opt_v2 = MagicMock()
        opt_v2.list_experiments = AsyncMock(
            return_value=[{"id": "e1", "agent_id": "agent-1"}]
        )
        opt_v2.rollback = AsyncMock(return_value=False)
        app = _make_app(self_optimizer_v2=opt_v2)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/experiments/e1/rollback",
            json={"reason": "regression"},
            headers=_headers(),
        )
        assert resp.status_code == 400

    def test_rollback_success(self) -> None:
        opt_v2 = MagicMock()
        opt_v2.list_experiments = AsyncMock(
            return_value=[{"id": "e1", "agent_id": "agent-1"}]
        )
        opt_v2.rollback = AsyncMock(return_value=True)
        app = _make_app(self_optimizer_v2=opt_v2)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/experiments/e1/rollback",
            json={"reason": "regression found in prod"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rolled_back"
        assert body["agent_id"] == "agent-1"
        assert body["reason"] == "regression found in prod"
        opt_v2.rollback.assert_awaited_once()


def test_list_experiments_exception_returns_empty_list() -> None:
    opt_v2 = MagicMock()
    opt_v2.list_experiments = AsyncMock(side_effect=RuntimeError("db down"))
    app = _make_app(self_optimizer_v2=opt_v2)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/intelligence/experiments", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Marketplace: version history (non-empty) + version publish endpoint
# ---------------------------------------------------------------------------


def test_get_template_versions_returns_db_history_when_present() -> None:
    marketplace = MagicMock()
    marketplace.get_template = MagicMock(return_value={"template_id": "t1", "version": "1.0.0"})
    marketplace.get_version_history = AsyncMock(
        return_value=[{"version": "2.0.0", "template_id": "t1", "is_current": True}]
    )
    app = _make_app(marketplace=marketplace)
    app.state.marketplace_v2 = _v2_with({"id": "t1", "version": "1.0.0", "tenant_id": "x"})
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/marketplace/t1/versions", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"version": "2.0.0", "template_id": "t1", "is_current": True}]
    marketplace.get_version_history.assert_awaited_once()


def _v2_with(template: dict[str, Any] | None) -> Any:
    v2 = MagicMock()
    v2.get_template = AsyncMock(return_value=template)
    return v2


def test_publish_template_version() -> None:
    """Only the template's owner may snapshot a new version."""
    marketplace = MagicMock()
    marketplace.publish_version = AsyncMock(
        return_value={"version": "1.1.0", "template_id": "t1", "changelog": "fixes"}
    )
    app = _make_app(marketplace=marketplace)
    owned = {"id": "t1", "tenant_id": _CTX.tenant_id}
    app.state.marketplace_v2 = _v2_with(owned)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/marketplace/t1/publish",
        json={"version": "1.1.0", "changelog": "fixes"},
        headers=_headers(),
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == "1.1.0"
    kwargs = marketplace.publish_version.await_args.kwargs
    assert kwargs["template_id"] == "t1" and kwargs["template"] == owned

    # A template the caller can see but does not own (e.g. a public one).
    app.state.marketplace_v2 = _v2_with({"id": "t1", "tenant_id": "someone-else"})
    denied = client.post(
        "/marketplace/t1/publish", json={"version": "9.9.9"}, headers=_headers()
    )
    assert denied.status_code == 404
    assert marketplace.publish_version.await_count == 1


# ---------------------------------------------------------------------------
# list_installs — reads install records, never guesses from the agent store
# ---------------------------------------------------------------------------


def test_list_installs_ignores_agent_store_guesses() -> None:
    """Installs come from the marketplace's install records only. The endpoint
    used to fall back to scanning agents for a ``marketplace_template_id``
    attribute no agent carries, masking that it never read the installs."""
    agent_store = MagicMock()
    agent_store.list = MagicMock(return_value=[{"marketplace_template_id": "tmpl-1"}])
    app = _make_app(marketplace=MagicMock(), agent_store=agent_store)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/marketplace/installs", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["installed_ids"] == []
    agent_store.list.assert_not_called()


# ---------------------------------------------------------------------------
# stream_simulation — agent_store.get() exception is swallowed
# ---------------------------------------------------------------------------


def test_stream_simulation_agent_store_get_raises_still_streams() -> None:
    async def _run_streaming(**kwargs: Any) -> Any:
        yield {"type": "step", "n": 1}

    simulation = MagicMock()
    simulation.run_streaming = _run_streaming
    agent_store = MagicMock()
    agent_store.get = MagicMock(side_effect=RuntimeError("agent store down"))
    app = _make_app(simulation_runner=simulation, agent_store=agent_store)
    client = TestClient(app, raise_server_exceptions=False)
    with client.stream(
        "POST",
        "/enterprise/simulation/stream",
        json={"goal": "test goal", "agent_id": "agent-x"},
        headers=_headers(),
    ) as resp:
        assert resp.status_code == 200
        content = b"".join(resp.iter_bytes())
    assert b"simulation_started" in content


# ---------------------------------------------------------------------------
# Benchmarks — DB-backed "your" + "platform" metrics and percentile branches
# ---------------------------------------------------------------------------


def _bench_router(
    your: dict[str, tuple], platform: dict[str, tuple]
) -> list[tuple[str, Any]]:
    """Route benchmark SQL by table; tenant-scoped queries carry a ``tid`` param."""

    def pick(table: str) -> Any:
        def h(p: dict) -> _FakeResult:
            src = your if "tid" in p else platform
            return _FakeResult(rows=[src[table]] if table in src else [])

        return h

    return [
        ("FROM cost_ledger", pick("cost")),
        ("FROM evaluations", pick("eval")),
        ("FROM goals", pick("goals")),
    ]


def _shared_platform(
    success: float | None, cost: float | None, eval_score: float | None = None
) -> Any:
    """Patch the shared /insights platform computation (MEM-31) with fixed figures."""
    live = success is not None
    shared = {
        "platform_avg_success_rate": success,
        "platform_avg_cost_usd": cost,
        "data_source": "live_platform_data" if live else "insufficient_data",
    }
    evals = {"eval_score": eval_score, "dims": {"safety": 0.88} if eval_score else {}}
    return (
        patch("app.api.insights.compute_platform_benchmarks", AsyncMock(return_value=shared)),
        patch(
            "app.api.insights.compute_platform_eval_benchmarks", AsyncMock(return_value=evals)
        ),
    )


def test_benchmarks_your_and_platform_metrics_top_10_percent() -> None:
    """Real-schema benchmark computation: your success rate crushes platform."""
    router = _bench_router(
        your={
            "goals": (100, 95, 1),
            "cost": (0.01, 100),
            "eval": (50, 0.876, 0.9, 0.85, 0.88, 0.95, 0.8),
        },
        platform={},
    )
    session = _FakeSession(router=router)
    app = _make_app(db_session_factory=_db_factory(session))
    app.state.system_db_session_factory = object()
    client = TestClient(app, raise_server_exceptions=False)
    p1, p2 = _shared_platform(0.72, 0.05, 0.764)
    with p1, p2:
        resp = client.get("/intelligence/benchmarks?days=7", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["your_success_rate"] == 0.95
    assert body["percentile_success"] == 10
    assert body["comparison_label"] == "Top 10%"
    assert body["your_eval_score"] == 0.876
    assert body["dimensions"]["your"]["safety"] == 0.95
    assert body["platform_avg_success_rate"] == 0.72
    assert body["platform_avg_eval_score"] == 0.764
    assert body["percentile_cost"] == 10
    assert body["data_source"] == "live_platform_data"


def test_benchmarks_sql_matches_real_schema() -> None:
    """Regression: the SQL referenced goals.cost_usd and evaluations.score_* /
    run_at — columns that do not exist — so every query failed silently."""
    session = _FakeSession(router=[])
    app = _make_app(db_session_factory=_db_factory(session))
    TestClient(app, raise_server_exceptions=False).get(
        "/intelligence/benchmarks", headers=_headers()
    )
    sql = [q for q in session.executed if "set_config" not in q]
    assert sql, "benchmark endpoint ran no SQL"
    for q in sql:
        assert "score_task_completion" not in q and "run_at" not in q, q
        if "FROM goals" in q:
            assert "cost_usd" not in q, q
    assert any("FROM cost_ledger" in q for q in sql)
    assert any("average_score" in q and "FROM evaluations" in q for q in sql)


def test_benchmarks_below_average_and_high_cost_percentile() -> None:
    router = _bench_router(
        your={"goals": (100, 10, 1), "cost": (0.5, 100)},
        platform={},
    )
    session = _FakeSession(router=router)
    app = _make_app(db_session_factory=_db_factory(session))
    app.state.system_db_session_factory = object()
    client = TestClient(app, raise_server_exceptions=False)
    p1, p2 = _shared_platform(0.72, 0.05)
    with p1, p2:
        resp = client.get("/intelligence/benchmarks", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["percentile_success"] == 75
    assert body["comparison_label"] == "Below Average"
    assert body["percentile_cost"] == 75
    # No eval rows: no invented dimension scores.
    assert body["dimensions"]["your"] == {}
    assert body["your_eval_score"] is None


def test_benchmarks_too_few_tenants_is_insufficient_data() -> None:
    """A platform aggregate over < 5 tenants is withheld (k-anonymity)."""
    router = _bench_router(
        your={"goals": (100, 50, 1)},
        platform={"goals": (1000, 720, 2), "cost": (0.05, 1000)},
    )
    session = _FakeSession(router=router)
    client = TestClient(
        _make_app(db_session_factory=_db_factory(session)), raise_server_exceptions=False
    )
    body = client.get("/intelligence/benchmarks", headers=_headers()).json()
    assert body["platform_avg_success_rate"] is None
    assert body["platform_avg_cost_usd"] is None
    assert body["data_source"] == "insufficient_data"
    assert body["percentile_success"] is None
    assert body["your_success_rate"] == 0.5


def test_benchmarks_db_query_exceptions_are_503_not_nulls() -> None:
    # MEM-31: a failed tenant-metrics query used to be swallowed into nulls,
    # which read as "you have no data"; it is an honest 503 now.
    session = _FakeSession(router=[], raise_on="goals")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/intelligence/benchmarks", headers=_headers())
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# run_eval_suite — full success path
# ---------------------------------------------------------------------------


def test_run_eval_suite_success() -> None:
    """POST /run answers 202 at once; the outcome is recorded by the background run."""
    from app.intelligence.eval_suite import EvalSuiteResult, GoldenTaskResult

    result = EvalSuiteResult(suite_id="suite-1", total_tasks=1, passed_tasks=1)
    result.task_results = [
        GoldenTaskResult(task_id="task-1", goal="g", passed=True, duration_seconds=1.2345)
    ]
    runner = MagicMock()
    runner.run_suite = AsyncMock(return_value=result)
    app = _make_app(eval_suite_runner=runner, goal_service=MagicMock())
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(
        "/intelligence/eval-suites", json={"suite_id": "suite-1"}, headers=_headers()
    ).status_code == 201
    client.post(
        "/intelligence/eval-suites/suite-1/tasks", json={"goal": "g"}, headers=_headers()
    )

    resp = client.post("/intelligence/eval-suites/suite-1/run", headers=_headers())
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running" and body["total"] == 1
    runner.run_suite.assert_awaited_once()
    kwargs = runner.run_suite.await_args.kwargs
    assert kwargs["run_id"] == body["run_id"]
    assert [t.goal for t in kwargs["tasks"]] == ["g"]

    runs = client.get("/intelligence/eval-suites/suite-1/results", headers=_headers()).json()
    assert runs[0]["run_id"] == body["run_id"]
    assert runs[0]["status"] == "completed" and runs[0]["passed"] == 1
    assert runs[0]["task_results"][0]["duration_seconds"] == 1.23


def test_run_eval_suite_failure_is_recorded() -> None:
    runner = MagicMock()
    runner.run_suite = AsyncMock(side_effect=RuntimeError("provider down"))
    client = TestClient(
        _make_app(eval_suite_runner=runner, goal_service=MagicMock()),
        raise_server_exceptions=False,
    )
    client.post("/intelligence/eval-suites", json={"suite_id": "s-f"}, headers=_headers())
    assert client.post("/intelligence/eval-suites/s-f/run", headers=_headers()).status_code == 202
    runs = client.get("/intelligence/eval-suites/s-f/results", headers=_headers()).json()
    assert runs[0]["status"] == "failed" and "provider down" in runs[0]["error"]


def test_run_unknown_eval_suite_is_404() -> None:
    client = TestClient(
        _make_app(eval_suite_runner=MagicMock(), goal_service=MagicMock()),
        raise_server_exceptions=False,
    )
    assert client.post("/intelligence/eval-suites/nope/run", headers=_headers()).status_code == 404


# ---------------------------------------------------------------------------
# Prompt variant A/B testing — full CRUD (previously entirely uncovered)
# ---------------------------------------------------------------------------


class TestPromptVariantsCRUD:
    def test_create_list_promote_report_delete_lifecycle(self) -> None:
        optimizer = PromptOptimizer()
        app = _make_app(prompt_optimizer=optimizer)
        client = TestClient(app, raise_server_exceptions=False)

        # Create a challenger variant.
        create_resp = client.post(
            "/intelligence/prompt-variants",
            json={
                "key": "planner_prompt",
                "name": "Challenger A",
                "prompt_text": "You are a terse planner.",
            },
            headers=_headers(),
        )
        assert create_resp.status_code == 201
        variant = create_resp.json()
        assert variant["is_control"] is False
        assert variant["mean_score"] is None
        variant_id = variant["id"]

        # List variants for this tenant, filtered by key.
        list_resp = client.get(
            "/intelligence/prompt-variants?key=planner_prompt", headers=_headers()
        )
        assert list_resp.status_code == 200
        listed = list_resp.json()
        assert any(v["id"] == variant_id for v in listed)

        # Record a couple of eval scores directly on the optimizer, then
        # confirm the report reflects them.
        optimizer.record_result(variant_id, 0.8, cost_usd=0.02, latency_ms=900)
        optimizer.record_result(variant_id, 0.9, cost_usd=0.04, latency_ms=1800)

        report_resp = client.get(
            f"/intelligence/prompt-variants/{variant_id}/report", headers=_headers()
        )
        assert report_resp.status_code == 200
        report = report_resp.json()
        assert report["mean_score"] == 0.85
        assert report["run_count"] == 2
        assert report["mean_cost_usd"] == 0.03
        assert report["p95_latency_ms"] == 2500  # upper edge of the 1.8 s bucket

        # Promote the challenger to control.
        promote_resp = client.post(
            f"/intelligence/prompt-variants/{variant_id}/promote", headers=_headers()
        )
        assert promote_resp.status_code == 200
        promoted = promote_resp.json()
        assert promoted["promoted"] is True
        assert optimizer._variants["tid-boost"][variant_id].is_control is True

        # Delete it.
        delete_resp = client.delete(
            f"/intelligence/prompt-variants/{variant_id}", headers=_headers()
        )
        assert delete_resp.status_code == 204
        assert variant_id not in optimizer._variants.get("tid-boost", {})

    def test_promote_unknown_variant_is_404(self) -> None:
        app = _make_app(prompt_optimizer=PromptOptimizer())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/intelligence/prompt-variants/does-not-exist/promote", headers=_headers()
        )
        assert resp.status_code == 404

    def test_delete_unknown_variant_is_404(self) -> None:
        app = _make_app(prompt_optimizer=PromptOptimizer())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete(
            "/intelligence/prompt-variants/does-not-exist", headers=_headers()
        )
        assert resp.status_code == 404

    def test_report_unknown_variant_is_404(self) -> None:
        app = _make_app(prompt_optimizer=PromptOptimizer())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/intelligence/prompt-variants/does-not-exist/report", headers=_headers()
        )
        assert resp.status_code == 404

    def test_shared_global_variant_is_read_only_for_tenants(self) -> None:
        """Regression: any tenant could delete or promote the shared "global" variants."""
        optimizer = PromptOptimizer()
        variant = optimizer.register_variant("k", "Global V", "text", tenant_id="global")
        app = _make_app(prompt_optimizer=optimizer)
        client = TestClient(app, raise_server_exceptions=False)
        vid = variant.variant_id
        assert client.delete(f"/intelligence/prompt-variants/{vid}", headers=_headers()).status_code == 404
        assert client.post(
            f"/intelligence/prompt-variants/{vid}/promote", headers=_headers()
        ).status_code == 404
        assert vid in optimizer._variants["global"]
        assert optimizer._variants["global"][vid].is_control is False
        # Reading the shared variant's report is allowed.
        assert client.get(
            f"/intelligence/prompt-variants/{vid}/report", headers=_headers()
        ).status_code == 200

    def test_other_tenants_variant_is_invisible(self) -> None:
        """Regression: promote/report found variants across ALL tenants."""
        optimizer = PromptOptimizer()
        foreign = optimizer.register_variant("planner", "Theirs", "t", tenant_id="other-tenant")
        client = TestClient(_make_app(prompt_optimizer=optimizer), raise_server_exceptions=False)
        fid = foreign.variant_id
        for method, path in [
            ("POST", f"/intelligence/prompt-variants/{fid}/promote"),
            ("GET", f"/intelligence/prompt-variants/{fid}/report"),
            ("DELETE", f"/intelligence/prompt-variants/{fid}"),
        ]:
            assert client.request(method, path, headers=_headers()).status_code == 404
        assert optimizer._variants["other-tenant"][fid].is_control is False


# ---------------------------------------------------------------------------
# GDPR export job — DB insert / Celery enqueue failure paths
# ---------------------------------------------------------------------------


def test_start_gdpr_export_db_insert_failure_is_503() -> None:
    """Regression: a failed INSERT was swallowed and answered 'pending' for a job
    that does not exist."""
    session = _FakeSession(router=[], raise_on="gdpr_export_jobs")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/compliance/export/start", headers=_headers())
    assert resp.status_code == 503


def test_start_gdpr_export_celery_enqueue_failure_is_503_and_marks_job_failed() -> None:
    """Regression: with no worker enqueued the job stayed 'pending' forever while
    the API answered success. Now 503, and the recorded row is marked failed."""
    seen: list[str] = []

    def _update(_p: dict) -> _FakeResult:
        seen.append("update")
        return _FakeResult(rows=[])

    session = _FakeSession(router=[("UPDATE gdpr_export_jobs", _update)])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    with patch("app.scaling.tasks.run_gdpr_export.delay", side_effect=RuntimeError("no broker")):
        resp = client.post("/compliance/export/start", headers=_headers())
    assert resp.status_code == 503
    assert seen == ["update"]


def test_gdpr_export_status_found_row() -> None:
    class _Completed:
        def isoformat(self) -> str:
            return "2026-01-01T00:00:00+00:00"

    def jobs_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[("completed", _Completed(), "http://x/export.json", None)])

    session = _FakeSession(router=[("gdpr_export_jobs", jobs_handler)])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/compliance/export/jobs/job-42", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["download_url"] == "http://x/export.json"
    assert body["completed_at"] == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Contracts — explicit no-DB branch + sign failure (500)
# ---------------------------------------------------------------------------


def test_list_contracts_returns_empty_list_when_db_is_none() -> None:
    with patch("app.api.enterprise._get_db", return_value=None):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/enterprise/contracts", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == []


def test_sign_contract_db_exception_is_503() -> None:
    session = _FakeSession(router=[], raise_on="enterprise_contracts")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/enterprise/contracts/baa/sign",
        json={"signer_name": "Alice", "signer_email": "alice@example.com"},
        headers=_headers(),
    )
    assert resp.status_code == 503
    assert "simulated" not in resp.text  # no exception text leaked


# ---------------------------------------------------------------------------
# SAML — metadata generic exception, login success + failure, connection test
# ---------------------------------------------------------------------------


def test_saml_metadata_generic_exception_is_500() -> None:
    def saml_handler(_p: dict) -> _FakeResult:
        return _FakeResult(
            rows=[("idp", "https://idp.example.com/sso", "CERT", "sp", {}, None)]
        )

    session = _FakeSession(router=[("saml_configs", saml_handler)])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    with patch(
        "app.auth.saml_provider.SAMLProvider.get_sp_metadata",
        side_effect=RuntimeError("xml serialization exploded"),
    ):
        resp = client.get("/enterprise/saml/metadata", headers=_headers())
    assert resp.status_code == 500


def test_saml_login_without_library_is_501() -> None:
    def saml_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[("idp", "https://idp.example.com/sso", "CERT", "sp")])

    session = _FakeSession(router=[("saml_configs", saml_handler)])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/enterprise/saml/login", headers=_headers())
    # python3-saml (optional extra) is not installed in the test env → a clean
    # 501; it used to redirect to the raw IdP URL with no AuthnRequest.
    assert resp.status_code == 501
    assert "SAML not installed" in resp.json()["detail"]


def test_saml_login_generic_exception_is_500() -> None:
    session = _FakeSession(router=[], raise_on="saml_configs")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/enterprise/saml/login", headers=_headers())
    assert resp.status_code == 500


class TestSamlConnectionTest:
    def test_metadata_xml_valid(self) -> None:
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/enterprise/saml/test",
            json={"metadata_xml": "<EntityDescriptor></EntityDescriptor>"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_metadata_xml_invalid(self) -> None:
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/enterprise/saml/test",
            json={"metadata_xml": "<not-well-formed"},
            headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False

    def test_no_input_provided(self) -> None:
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/enterprise/saml/test", json={}, headers=_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "No SSO URL" in body["message"]

    def test_sso_url_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # saml/test is SSRF-guarded (DNS-resolving) and sends via client.request.
        monkeypatch.setattr("app.net.ssrf_guard._resolve_host", lambda h: ["93.184.216.34"])

        class _FakeResp:
            status_code = 200
            is_redirect = False

        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.request = AsyncMock(return_value=_FakeResp())
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with patch("httpx.AsyncClient", return_value=fake_client):
            resp = client.post(
                "/enterprise/saml/test",
                json={"sso_url": "https://idp.example.com/sso"},
                headers=_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["status_code"] == 200

    def test_sso_url_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.net.ssrf_guard._resolve_host", lambda h: ["93.184.216.34"])
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.request = AsyncMock(side_effect=RuntimeError("connection refused"))
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with patch("httpx.AsyncClient", return_value=fake_client):
            resp = client.post(
                "/enterprise/saml/test",
                json={"sso_url": "https://idp.example.com/sso"},
                headers=_headers(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Connection failed" in body["message"]


# ---------------------------------------------------------------------------
# SCIM — handler construction (auth + config load) and full user CRUD
# ---------------------------------------------------------------------------


_SCIM_TOKEN = "raw-scim-bearer-token"


def _scim_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_SCIM_TOKEN}"}


def _make_scim_router(
    scim_config_row: tuple | None,
    users_row: tuple | None = None,
    users_rows: list[tuple] | None = None,
    count: int = 0,
) -> list[tuple[str, Any]]:
    import hashlib

    token_hash = hashlib.sha256(_SCIM_TOKEN.encode()).hexdigest()

    def token_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[("tid-boost",)])

    def config_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[scim_config_row] if scim_config_row else [])

    def count_handler(_p: dict) -> _FakeResult:
        return _FakeResult(scalar_val=count)

    def users_list_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=users_rows or [])

    def user_single_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[users_row] if users_row else [])

    return [
        ("COUNT(*) FROM users", count_handler),
        ("scim_tokens", token_handler),
        ("scim_configs", config_handler),
        ("FROM users", users_list_handler if users_rows is not None else user_single_handler),
        ("INSERT INTO users", user_single_handler),
        ("UPDATE users", user_single_handler),
    ]


_USER_ROW = ("11111111-1111-1111-1111-111111111111", "alice@example.com", "Alice A", True, "scim-1", None, None)


# SCIM: the handler was rewritten on the ORM (tenant_memberships); these
# mock-SQL tests asserted columns that never existed. Coverage lives in
# tests/tenancy/test_tenancy_auth_integration.py (real Postgres, RLS).


