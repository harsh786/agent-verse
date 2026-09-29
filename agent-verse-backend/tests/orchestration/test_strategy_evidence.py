"""Strategy certification is derived from evidence recorded when strategies really run.

Regression: certification was ``derive_state(capability, ())`` (empty evidence) and
``StrategyEvidenceStore`` was never written.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategies import router as strategies_router
from app.orchestration.strategy_certification import CertificationEvaluator
from app.orchestration.strategy_evidence import StrategyEvidenceRecorder
from app.orchestration.strategy_readiness import ReadinessEvaluator
from app.orchestration.strategy_registry import StrategyState, build_default_registry
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _recorder(**kwargs: Any) -> StrategyEvidenceRecorder:
    return StrategyEvidenceRecorder(build_default_registry(), **kwargs)


async def test_runs_are_recorded_per_tenant_and_strategy() -> None:
    recorder = _recorder()
    react = build_default_registry().resolve("react").capability
    await recorder.record_run(
        tenant_id="t-a",
        goal_id="g1",
        strategy_ids=["react", "reflection"],
        succeeded=True,
        runtime_path="v2",
    )
    await recorder.record_run(
        tenant_id="t-a",
        goal_id="g2",
        strategy_ids=["react"],
        succeeded=False,
        runtime_path="legacy",
    )

    rows = await recorder.list_evidence("t-a", react)
    assert [(row.evidence_type, row.result) for row in rows] == [
        ("production_run", "failed"),
        ("canary", "passed"),
    ]
    assert StrategyEvidenceRecorder.summarize(rows) == {
        "runs": 2,
        "succeeded": 1,
        "failed": 1,
        "canary_runs": 1,
        "last_run_at": rows[0].observed_at.isoformat(),
    }
    # Tenant-scoped: another tenant sees nothing.
    assert await recorder.list_evidence("t-b", react) == []


async def test_storage_is_bounded() -> None:
    recorder = _recorder(max_per_strategy=3, max_keys=2)
    registry = build_default_registry()
    for index in range(5):
        await recorder.record_run(
            tenant_id="t",
            goal_id=f"g{index}",
            strategy_ids=["react"],
            succeeded=True,
            runtime_path="v2",
        )
    assert len(await recorder.list_evidence("t", registry.resolve("react").capability)) == 3

    await recorder.record_run(
        tenant_id="t", goal_id="x", strategy_ids=["reflection"], succeeded=True, runtime_path="v2"
    )
    await recorder.record_run(
        tenant_id="t",
        goal_id="y",
        strategy_ids=["chain_of_thought"],
        succeeded=True,
        runtime_path="v2",
    )
    # Oldest key (react) evicted once more than max_keys strategies are tracked.
    assert await recorder.list_evidence("t", registry.resolve("react").capability) == []


async def test_certification_derives_from_evidence_and_never_certifies_without_it() -> None:
    clock = _Clock()
    recorder = _recorder(clock=clock)
    evaluator = CertificationEvaluator()
    react = build_default_registry().resolve("react").capability

    empty = evaluator.derive_state(
        react, await recorder.certification_evidence("t", react), now=clock.now
    )
    assert empty.certified is False
    assert "canary" in empty.missing_or_invalid_categories

    await recorder.record_run(
        tenant_id="t", goal_id="g", strategy_ids=["react"], succeeded=True, runtime_path="v2"
    )
    await recorder.record_run(
        tenant_id="t", goal_id="h", strategy_ids=["react"], succeeded=True, runtime_path="legacy"
    )
    decision = evaluator.derive_state(
        react, await recorder.certification_evidence("t", react), now=clock.now
    )
    assert decision.certified is False
    assert decision.state is not StrategyState.CERTIFIED
    assert "canary" not in decision.missing_or_invalid_categories
    # Legacy-path runs are counted but never certify anything.
    assert "integration" in decision.missing_or_invalid_categories

    clock.now += timedelta(days=31)
    expired = evaluator.derive_state(
        react, await recorder.certification_evidence("t", react), now=clock.now
    )
    assert "canary" in expired.missing_or_invalid_categories


async def test_failed_canary_does_not_count_as_evidence() -> None:
    recorder = _recorder()
    react = build_default_registry().resolve("react").capability
    await recorder.record_run(
        tenant_id="t", goal_id="g", strategy_ids=["react"], succeeded=False, runtime_path="v2"
    )
    decision = CertificationEvaluator().derive_state(
        react, await recorder.certification_evidence("t", react)
    )
    assert "canary" in decision.missing_or_invalid_categories


class _Session:
    def __init__(self, log: list[tuple[str, dict[str, Any]]]) -> None:
        self._log = log

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        self._log.append((str(statement), dict(params or {})))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


async def test_evidence_is_written_to_postgres_under_the_tenant_guc() -> None:
    log: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def factory() -> Any:
        yield _Session(log)

    recorder = _recorder(db_factory_getter=lambda: factory)
    await recorder.record_run(
        tenant_id="t-db", goal_id="g", strategy_ids=["react"], succeeded=True, runtime_path="v2"
    )
    assert "set_config('app.tenant_id'" in log[0][0]
    assert log[0][1] == {"tenant_id": "t-db"}
    insert_sql, insert_params = log[1]
    assert "INSERT INTO strategy_certification_evidence" in insert_sql
    assert insert_params["tenant_id"] == "t-db"
    assert insert_params["evidence_type"] == "canary"
    assert insert_params["result"] == "passed"
    assert insert_params["artifact_reference"] == "goal:g"


async def test_finished_goal_records_evidence_for_what_actually_ran() -> None:
    recorder = _recorder()
    svc = GoalService()
    svc._app_state = SimpleNamespace(state=SimpleNamespace(strategy_evidence=recorder))
    record = GoalRecord(
        goal_id="g-done",
        goal_text="goal",
        status="executing",  # type: ignore[arg-type]
        tenant_id="t-goal",
        priority="normal",
        dry_run=False,
        created_at="",
        execution_context={
            "strategy_execution": {"driver": "agent_graph", "patterns": ["react", "reflection"]},
            "strategy_runtime_path": "v2",
        },
    )
    svc._record_terminal_goal_metrics(record, "completed")
    for task in list(svc._db_tasks):
        await task

    registry = build_default_registry()
    for strategy_id in ("react", "reflection"):
        rows = await recorder.list_evidence("t-goal", registry.resolve(strategy_id).capability)
        assert [(row.evidence_type, row.result) for row in rows] == [("canary", "passed")]


TENANT = TenantContext("tenant-api", PlanTier.PROFESSIONAL, "key-1")


def _client(recorder: StrategyEvidenceRecorder) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == "valid" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(strategies_router)
    app.state.strategy_registry = build_default_registry()
    app.state.strategy_readiness = ReadinessEvaluator()
    app.state.strategy_certification = CertificationEvaluator()
    app.state.strategy_evidence = recorder
    return TestClient(app, raise_server_exceptions=False)


async def test_certification_api_reports_recorded_evidence() -> None:
    recorder = _recorder()
    await recorder.record_run(
        tenant_id=TENANT.tenant_id,
        goal_id="g",
        strategy_ids=["react"],
        succeeded=True,
        runtime_path="v2",
    )
    api = _client(recorder)
    certification = api.get("/strategies/react/certification", headers={"X-API-Key": "valid"})
    assert certification.status_code == 200
    body = certification.json()
    assert body["certified"] is False
    assert "canary" not in body["missing_evidence"]
    assert body["runtime_evidence"]["runs"] == 1

    catalogue = api.get("/strategies", headers={"X-API-Key": "valid"}).json()["strategies"]
    by_id = {item["strategy_id"]: item for item in catalogue}
    assert by_id["react"]["runtime_evidence"]["canary_runs"] == 1
    assert by_id["reflection"]["runtime_evidence"]["runs"] == 0
    assert not any(item["certified"] for item in catalogue)
