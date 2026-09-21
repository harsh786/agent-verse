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
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/marketplace/t1/versions", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"version": "2.0.0", "template_id": "t1", "is_current": True}]
    marketplace.get_version_history.assert_awaited_once()


def test_publish_template_version() -> None:
    marketplace = MagicMock()
    marketplace.publish_version = AsyncMock(
        return_value={"version": "1.1.0", "template_id": "t1", "changelog": "fixes"}
    )
    app = _make_app(marketplace=marketplace)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/marketplace/t1/publish",
        json={"version": "1.1.0", "changelog": "fixes"},
        headers=_headers(),
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == "1.1.0"
    marketplace.publish_version.assert_awaited_once_with(
        template_id="t1", version="1.1.0", changelog="fixes", db=marketplace.publish_version.await_args.kwargs["db"]
    )


# ---------------------------------------------------------------------------
# list_installs — agent_store fallback branches
# ---------------------------------------------------------------------------


def test_list_installs_agent_store_awaitable_list() -> None:
    """agent_store.list() returns an awaitable (coroutine) list of agents."""

    async def _agents_coro() -> list[dict[str, Any]]:
        return [{"marketplace_template_id": "tmpl-1"}, {"marketplace_template_id": "tmpl-2"}]

    agent_store = MagicMock()
    agent_store.list = MagicMock(return_value=_agents_coro())
    marketplace = MagicMock()
    app = _make_app(marketplace=marketplace, agent_store=agent_store)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/marketplace/installs", headers=_headers())
    assert resp.status_code == 200
    assert set(resp.json()["installed_ids"]) == {"tmpl-1", "tmpl-2"}


def test_list_installs_agent_store_exception_falls_back_empty() -> None:
    agent_store = MagicMock()
    agent_store.list = MagicMock(side_effect=RuntimeError("boom"))
    marketplace = MagicMock()
    app = _make_app(marketplace=marketplace, agent_store=agent_store)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/marketplace/installs", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["installed_ids"] == []


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


def test_benchmarks_your_and_platform_metrics_top_10_percent() -> None:
    """Real DB-backed benchmark computation: your success rate crushes platform."""

    def goals_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(100, 95)])  # 95% success

    def cost_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(0.01,)])

    def eval_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(0.9, 0.85, 0.88, 0.95, 0.8, 0.876)])

    def plat_goals_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(1000, 720, 0.05)])

    def plat_eval_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(0.75, 0.72, 0.76, 0.88, 0.71)])

    # Routing must disambiguate the "your" (tenant-scoped) queries from the
    # near-identical "platform" ones. Unique markers picked from the actual
    # SQL text in app/api/enterprise.py:get_benchmarks:
    #   - your goal-count query is the only one with "AS completed"
    #   - your eval query is the only one with the combined COALESCE column
    #   - platform goal+cost query is the only one with "AVG(cost_usd) AS avg_cost"
    #   - your cost query is the only one with "AVG(cost_usd) FROM goals"
    router = [
        ("AVG(cost_usd) FROM goals", cost_handler),
        ("AS completed", goals_handler),
        ("COALESCE", eval_handler),
        ("AVG(cost_usd) AS avg_cost", plat_goals_handler),
        ("AVG(score_task_completion)", plat_eval_handler),
    ]
    session = _FakeSession(router=router)
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/intelligence/benchmarks?days=7", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["your_success_rate"] == 0.95
    assert body["percentile_success"] == 10
    assert body["comparison_label"] == "Top 10%"
    assert body["your_eval_score"] == 0.876
    assert body["dimensions"]["your"]["safety"] == 0.95
    assert "platform_avg_success_rate" in body
    assert body["percentile_cost"] in (10, 25, 50, 75)


def test_benchmarks_below_average_and_high_cost_percentile() -> None:
    def goals_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(100, 10)])  # 10% success — well below platform

    def cost_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(0.5,)])  # expensive vs platform 0.05

    def eval_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(0, 0, 0, 0, 0, 0)])  # falsy -> your_dims stays {}

    def plat_goals_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[(1000, 720, 0.05)])

    router = [
        ("AVG(cost_usd) FROM goals", cost_handler),
        ("AS completed", goals_handler),
        ("COALESCE", eval_handler),
        ("AVG(cost_usd) AS avg_cost", plat_goals_handler),
    ]
    session = _FakeSession(router=router)
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/intelligence/benchmarks", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["percentile_success"] == 75
    assert body["comparison_label"] == "Below Average"
    assert body["percentile_cost"] == 75
    # eval_score stayed 0 (falsy row[5]) so "your" dims fall back to platform defaults
    assert body["dimensions"]["your"] == body["dimensions"]["platform"]


def test_benchmarks_db_query_exceptions_fall_back_to_defaults() -> None:
    session = _FakeSession(router=[], raise_on="goals")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/intelligence/benchmarks", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    # Falls back to hardcoded platform defaults when DB explodes.
    assert body["platform_avg_success_rate"] == 0.72
    assert body["your_success_rate"] == 0.0


# ---------------------------------------------------------------------------
# run_eval_suite — full success path
# ---------------------------------------------------------------------------


def test_run_eval_suite_success() -> None:
    result = MagicMock()
    result.run_id = "run-42"
    result.total_tasks = 3
    result.passed_tasks = 2
    result.failed_tasks = 1
    result.pass_rate = 0.667
    result.run_at = "2026-01-01T00:00:00"
    task_result = MagicMock()
    task_result.task_id = "task-1"
    task_result.passed = True
    task_result.failure_reasons = []
    task_result.duration_seconds = 1.2345
    result.task_results = [task_result]

    runner = MagicMock()
    runner.run_suite = AsyncMock(return_value=result)
    goal_service = MagicMock()
    app = _make_app(eval_suite_runner=runner, goal_service=goal_service)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/intelligence/eval-suites/suite-1/run", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-42"
    assert body["total"] == 3
    assert body["passed"] == 2
    assert body["task_results"][0]["duration_seconds"] == 1.23
    runner.run_suite.assert_awaited_once()


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
        target = optimizer._variants["tid-boost"][variant_id]
        target.eval_scores.extend([0.8, 0.9])
        target.run_count = 2

        report_resp = client.get(
            f"/intelligence/prompt-variants/{variant_id}/report", headers=_headers()
        )
        assert report_resp.status_code == 200
        report = report_resp.json()
        assert report["mean_score"] == 0.85
        assert report["run_count"] == 2

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

    def test_delete_variant_stored_in_global_scope(self) -> None:
        """A variant registered without an explicit tenant lands in "global"."""
        optimizer = PromptOptimizer()
        variant = optimizer.register_variant("k", "Global V", "text", tenant_id="global")
        app = _make_app(prompt_optimizer=optimizer)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete(
            f"/intelligence/prompt-variants/{variant.variant_id}", headers=_headers()
        )
        assert resp.status_code == 204
        assert variant.variant_id not in optimizer._variants.get("global", {})


# ---------------------------------------------------------------------------
# GDPR export job — DB insert / Celery enqueue failure paths
# ---------------------------------------------------------------------------


def test_start_gdpr_export_db_insert_failure_still_returns_pending() -> None:
    session = _FakeSession(router=[], raise_on="gdpr_export_jobs")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/compliance/export/start", headers=_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_start_gdpr_export_celery_enqueue_failure_still_returns_pending() -> None:
    """Celery task import/enqueue is best-effort; job creation must not fail."""
    session = _FakeSession(router=[])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    with patch("app.scaling.tasks.run_gdpr_export.delay", side_effect=RuntimeError("no broker")):
        resp = client.post("/compliance/export/start", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert "job_id" in body


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


def test_sign_contract_db_exception_is_500() -> None:
    session = _FakeSession(router=[], raise_on="enterprise_contracts")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/enterprise/contracts/baa/sign",
        json={"signer_name": "Alice", "signer_email": "alice@example.com"},
        headers=_headers(),
    )
    assert resp.status_code == 500


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


def test_saml_login_redirects_to_idp_on_success() -> None:
    def saml_handler(_p: dict) -> _FakeResult:
        return _FakeResult(rows=[("idp", "https://idp.example.com/sso", "CERT", "sp")])

    session = _FakeSession(router=[("saml_configs", saml_handler)])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    resp = client.get("/enterprise/saml/login", headers=_headers())
    assert resp.status_code in (302, 303, 307)
    # python3-saml is not installed in test env -> falls back to the raw IdP URL.
    assert resp.headers["location"] == "https://idp.example.com/sso"


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

    def test_sso_url_reachable(self) -> None:
        class _FakeResp:
            status_code = 200

        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=_FakeResp())
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

    def test_sso_url_connection_error(self) -> None:
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
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


class TestScimHandlerAndUserCrud:
    def test_list_users_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, False, True, "viewer", {}),
            users_rows=[_USER_ROW],
            count=1,
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/scim/v2/Users", headers=_scim_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["totalResults"] == 1
        assert body["Resources"][0]["userName"] == "alice@example.com"

    def test_get_user_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, False, True, "viewer", {}),
            users_row=_USER_ROW,
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/scim/v2/Users/scim-1", headers=_scim_headers())
        assert resp.status_code == 200
        assert resp.json()["userName"] == "alice@example.com"

    def test_create_user_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, False, True, "viewer", {}),
            users_row=_USER_ROW,
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/scim/v2/Users",
            json={"userName": "alice@example.com", "name": {"givenName": "Alice"}},
            headers=_scim_headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["userName"] == "alice@example.com"

    def test_create_user_disabled_by_config_is_403(self) -> None:
        router = _make_scim_router(
            scim_config_row=(False, True, False, True, "viewer", {}),
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/scim/v2/Users",
            json={"userName": "bob@example.com"},
            headers=_scim_headers(),
        )
        assert resp.status_code == 403

    def test_replace_user_put_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, False, True, "viewer", {}),
            users_row=_USER_ROW,
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put(
            "/scim/v2/Users/scim-1",
            json={"name": {"givenName": "Alice", "familyName": "Updated"}, "active": True},
            headers=_scim_headers(),
        )
        assert resp.status_code == 200

    def test_patch_user_deactivate_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, True, True, "viewer", {}),
            users_row=(
                "11111111-1111-1111-1111-111111111111",
                "alice@example.com",
                "Alice A",
                False,
                "scim-1",
                None,
                None,
            ),
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(
            "/scim/v2/Users/scim-1",
            json={"Operations": [{"op": "replace", "path": "active", "value": False}]},
            headers=_scim_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_delete_user_success(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, True, True, "viewer", {}),
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/scim/v2/Users/scim-1", headers=_scim_headers())
        assert resp.status_code == 204

    def test_delete_user_disabled_is_403(self) -> None:
        router = _make_scim_router(
            scim_config_row=(True, True, False, True, "viewer", {}),
        )
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/scim/v2/Users/scim-1", headers=_scim_headers())
        assert resp.status_code == 403

    def test_handler_falls_back_to_defaults_when_no_scim_config_row(self) -> None:
        """No scim_configs row -> default permissive config is used (create allowed)."""
        router = _make_scim_router(scim_config_row=None, users_row=_USER_ROW)
        session = _FakeSession(router=router)
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/scim/v2/Users",
            json={"userName": "alice@example.com"},
            headers=_scim_headers(),
        )
        assert resp.status_code == 201

    def test_handler_config_load_exception_falls_back_to_defaults(self) -> None:
        session = _FakeSession(router=[], raise_on="scim_configs")
        # scim_tokens auth must still succeed before config load fails.
        session.router = [("scim_tokens", lambda _p: _FakeResult(rows=[("tid-boost",)]))]
        session.raise_on = "scim_configs"

        # After the auth query succeeds, the next execute() call (config load)
        # should raise, which _get_scim_handler catches internally.
        original_execute = session.execute

        async def _execute(stmt: Any, params: dict | None = None) -> _FakeResult:
            text = str(stmt)
            if "scim_tokens" in text:
                return _FakeResult(rows=[("tid-boost",)])
            if "scim_configs" in text:
                raise RuntimeError("config table missing")
            return _FakeResult(rows=[_USER_ROW])

        session.execute = _execute
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/scim/v2/Users",
            json={"userName": "alice@example.com"},
            headers=_scim_headers(),
        )
        # Falls back to default permissive config -> creation still succeeds.
        assert resp.status_code == 201

    def test_no_bearer_token_is_401(self) -> None:
        app = _make_app(db_session_factory=_db_factory(_FakeSession(router=[])))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/scim/v2/Users")
        assert resp.status_code == 401

    def test_invalid_bearer_token_is_401(self) -> None:
        session = _FakeSession(router=[("scim_tokens", lambda _p: _FakeResult(rows=[]))])
        app = _make_app(db_session_factory=_db_factory(session))
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/scim/v2/Users", headers=_scim_headers())
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SCIM token provisioning — DB failure path
# ---------------------------------------------------------------------------


def test_provision_scim_token_db_exception_is_500() -> None:
    session = _FakeSession(router=[], raise_on="scim_tokens")
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/enterprise/scim/provision-token", headers=_headers())
    assert resp.status_code == 500


def test_provision_scim_token_success() -> None:
    session = _FakeSession(router=[])
    app = _make_app(db_session_factory=_db_factory(session))
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/enterprise/scim/provision-token", headers=_headers())
    assert resp.status_code == 201
    body = resp.json()
    assert "token" in body and "prefix" in body


# ---------------------------------------------------------------------------
# _get_db fallback exception path (no app.state.db_session_factory, and
# get_session_factory() itself raises) — must not blow up the request.
# ---------------------------------------------------------------------------


def test_get_db_falls_back_to_none_when_session_factory_raises() -> None:
    app = _make_app()  # no db_session_factory set on app.state at all
    client = TestClient(app, raise_server_exceptions=False)
    with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no DATABASE_URL")):
        resp = client.get("/enterprise/contracts", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# _require_tenant — unauthenticated request to a real gated endpoint is 401
# ---------------------------------------------------------------------------


def test_unauthenticated_request_to_gated_endpoint_is_401() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/enterprise/compliance/residency")
    assert resp.status_code == 401
