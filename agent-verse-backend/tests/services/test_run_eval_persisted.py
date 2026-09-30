"""POST /goals/{id}/eval (MEM-20): real provider, persisted, not stale on other replicas.

``run_eval`` read ``getattr(self, "_app_provider")`` — never set on GoalService —
so "Run Eval" always scored heuristically; the result lived only in this
replica's cache and other replicas kept serving their older cached scorecard.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.core.errors import ServiceUnavailableError
from app.intelligence.eval import EvalScorecard
from app.providers import guarded_completion as gc
from app.providers.fake import FakeProvider
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_CTX = TenantContext(tenant_id="t-rescore", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_GID = "g-rescore"


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    gc.set_platform_cost_services(lambda: (RecordingController(), None))
    yield
    gc.set_platform_cost_services(saved)


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, db: _SharedDB) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        p = dict(params or {})
        if "set_config" in sql:
            return _Result(None)
        if self._db.fail_writes and "INSERT INTO evaluations" in sql:
            raise RuntimeError("connection refused")
        if "INSERT INTO evaluations" in sql:
            key = (p["tid"], p["gid"], p["strategy_execution_id"], p["evaluator_version"])
            if key in self._db.rows and "DO UPDATE" not in sql:
                return _Result(None)  # ON CONFLICT DO NOTHING
            self._db.clock += 1
            self._db.rows[key] = (p["scores"], p["avg"], p["passed"], self._db.clock)
            return _Result(None)
        if "FROM evaluations" in sql:
            matches = [
                v for k, v in self._db.rows.items() if k[0] == p["tid"] and k[1] == p["gid"]
            ]
            if not matches:
                return _Result(None)
            latest = max(matches, key=lambda r: r[3])
            return _Result(latest[:3])
        return _Result(None)


class _SharedDB:
    """One Postgres shared by every replica in the test."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], tuple[Any, ...]] = {}
        self.clock = 0
        self.fail_writes = False

    def __call__(self) -> _Session:
        return _Session(self)


def _record() -> GoalRecord:
    return GoalRecord(
        goal_id=_GID,
        goal_text="Summarise churn",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        events=[
            {"type": "plan_ready", "steps": ["Load cohort", "Summarise"]},
            {"type": "verification_done", "success": True},
        ],
    )


def _replica(db: _SharedDB, *, provider: Any = None) -> GoalService:
    svc = GoalService(app_state=SimpleNamespace(_app_provider=provider))
    svc._db = db

    async def _load(goal_id: str, tenant_ctx: TenantContext) -> GoalRecord | None:
        return _record() if goal_id == _GID else None

    svc._db_get_goal_record = _load  # type: ignore[method-assign]
    return svc


async def test_run_eval_uses_the_app_provider_and_says_so() -> None:
    provider = ScriptedProvider("0.8")
    svc = _replica(_SharedDB(), provider=provider)
    out = await svc.run_eval(_GID, _CTX)
    assert out["scorer"] == "llm"
    assert out["scores"]["coherence"] == pytest.approx(0.8)
    assert out["scores"]["accuracy"] == pytest.approx(0.8)
    assert len(provider.requests) == 2


@pytest.mark.parametrize("provider", [None, FakeProvider()])
async def test_run_eval_without_a_real_provider_is_heuristic(provider: Any) -> None:
    svc = _replica(_SharedDB(), provider=provider)
    out = await svc.run_eval(_GID, _CTX)
    assert out["scorer"] == "heuristic"


async def test_failed_llm_scoring_is_not_reported_as_llm() -> None:
    svc = _replica(_SharedDB(), provider=ScriptedProvider(fail=True))
    out = await svc.run_eval(_GID, _CTX)
    assert out["scorer"] == "heuristic"


async def test_rescore_is_persisted_and_read_by_another_replica() -> None:
    db = _SharedDB()
    scorer = _replica(db, provider=ScriptedProvider("0.8"))
    other = _replica(db)
    # The other replica cached an older scorecard for the goal.
    other._eval_scores[_GID] = EvalScorecard(goal_id=_GID, scores={"accuracy": 0.1})

    out = await scorer.run_eval(_GID, _CTX)
    assert out["persisted"] is True

    seen = await other.get_eval(_GID, _CTX)
    assert seen["status"] == "evaluated"
    assert seen["scores"] == out["scores"]
    assert seen["average_score"] == pytest.approx(out["average_score"], abs=1e-6)


async def test_second_rescore_replaces_the_first() -> None:
    db = _SharedDB()
    await _replica(db, provider=ScriptedProvider("0.2")).run_eval(_GID, _CTX)
    await _replica(db, provider=ScriptedProvider("0.9")).run_eval(_GID, _CTX)
    seen = await _replica(db).get_eval(_GID, _CTX)
    assert seen["scores"]["coherence"] == pytest.approx(0.9)


async def test_rescore_that_cannot_be_persisted_fails_loudly() -> None:
    db = _SharedDB()
    db.fail_writes = True
    svc = _replica(db, provider=ScriptedProvider("0.8"))
    with pytest.raises(ServiceUnavailableError):
        await svc.run_eval(_GID, _CTX)


async def test_scores_json_round_trips() -> None:
    db = _SharedDB()
    out = await _replica(db).run_eval(_GID, _CTX)
    (stored,) = db.rows.values()
    assert json.loads(stored[0]) == out["scores"]
