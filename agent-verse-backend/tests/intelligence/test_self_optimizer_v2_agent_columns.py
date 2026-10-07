"""SelfOptimizerV2 against the REAL agents schema.

It read and wrote ``agents.config`` — a column that does not exist — and the
error was swallowed, so on Postgres no experiment ever started and no winner
was ever applied. ``increment_goals`` was a read-modify-write on a JSON blob
(lost updates under concurrency), and an experiment the runtime excluded
(changes it cannot apply per goal) never concluded, blocking all future ones.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.agent import Agent
from app.intelligence.self_optimizer_v2 import (
    EXPERIMENT_APPLICABLE_KEYS,
    AgentConfigUnavailableError,
    SelfOptimizerV2,
    TenantOptimizationState,
)


class _AsyncSession:
    """Async facade over a sync SQLAlchemy Session (no aiosqlite in this env)."""

    def __init__(self, session: Session) -> None:
        self._s = session

    async def __aenter__(self) -> _AsyncSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._s.close()
        return False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        return self._s.execute(stmt, params or {})

    async def commit(self) -> None:
        self._s.commit()

    def flush(self) -> None:
        self._s.flush()


@pytest.fixture
def agents_db(tmp_path: Any) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / 'agents.db'}")

    @event.listens_for(engine, "connect")
    def _pg_shims(dbapi_conn: Any, _rec: Any) -> None:
        dbapi_conn.create_function("set_config", 3, lambda _k, v, _l: v)
        dbapi_conn.create_function("NOW", 0, lambda: "2026-01-01 00:00:00")

    Agent.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, tenant_id, name, goal_template, system_prompt, "
                "model_override, autonomy_mode, connector_ids, trigger_config, max_iterations, "
                "timeout_seconds, allowed_collection_ids, policy_ids, version, is_archived, "
                "is_active, created_at, updated_at) VALUES ('a1', 't1', 'n', '', 'old prompt', "
                "'', 'bounded-autonomous', '[]', '{}', 15, 300, '[]', '[]', 1, 0, 1, "
                "'2026-01-01', '2026-01-01')"
            )
        )
    factory = sessionmaker(engine)
    yield lambda: _AsyncSession(factory())
    engine.dispose()


class _Redis:
    """Minimal async Redis whose non-atomic ops yield between read and write."""

    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    async def get(self, k: str) -> str | None:
        await asyncio.sleep(0)
        return self.d.get(k)

    async def setex(self, k: str, _ttl: int, v: str) -> None:
        await asyncio.sleep(0)
        self.d[k] = v

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.d[k] = str(v)

    async def incr(self, k: str) -> int:
        self.d[k] = str(int(self.d.get(k, "0")) + 1)
        return int(self.d[k])

    async def expire(self, _k: str, _s: int) -> bool:
        return True


async def test_reads_config_from_real_agent_columns(agents_db: Any) -> None:
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    cfg = await opt._read_current_agent_config("t1", "a1")
    assert cfg is not None
    assert cfg["system_prompt"] == "old prompt"
    assert cfg["max_iterations"] == 15


async def test_apply_writes_real_agent_columns(agents_db: Any) -> None:
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    ok = await opt.apply_suggestion(
        "t1", "a1", "exp-1", {"system_prompt": "new prompt", "max_iterations": 15}
    )
    assert ok is True
    async with agents_db() as s:
        res = await s.execute(text("SELECT system_prompt FROM agents WHERE id='a1'"))
        prompt = res.scalar()
    assert prompt == "new prompt"


async def test_read_error_is_not_swallowed() -> None:
    def _broken() -> Any:
        raise ConnectionError("db down")

    opt = SelfOptimizerV2(_Redis(), _broken, lambda: None)
    with pytest.raises(AgentConfigUnavailableError):
        await opt._read_current_agent_config("t1", "a1")


async def test_missing_agent_row_reads_as_none(agents_db: Any) -> None:
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    assert await opt._read_current_agent_config("t1", "nope") is None


async def test_increment_goals_is_atomic() -> None:
    state = TenantOptimizationState(_Redis())
    await asyncio.gather(*[state.increment_goals("t1", "a1") for _ in range(25)])
    assert (await state.get("t1", "a1"))["goals_completed"] == 25


async def test_reset_goal_counter_via_update() -> None:
    redis = _Redis()
    state = TenantOptimizationState(redis)
    for _ in range(3):
        await state.increment_goals("t1", "a1")
    await state.update("t1", "a1", {"goals_completed": 0, "current_experiment_id": None})
    assert (await state.get("t1", "a1"))["goals_completed"] == 0
    assert await state.increment_goals("t1", "a1") == 1


async def test_unapplicable_suggestion_does_not_start_experiment(agents_db: Any) -> None:
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    opt._get_recent_metrics = AsyncMock(return_value={})  # type: ignore[method-assign]
    opt._generate_suggestion = AsyncMock(  # type: ignore[method-assign]
        return_value={"suggested_change": {"field": "max_iterations", "new_value": 30}}
    )
    opt._create_experiment = AsyncMock(return_value="exp-x")  # type: ignore[method-assign]
    assert await opt._maybe_start_experiment("t1", "a1", None) is None
    opt._create_experiment.assert_not_awaited()


async def test_applicable_suggestion_starts_experiment(agents_db: Any) -> None:
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    opt._get_recent_metrics = AsyncMock(return_value={})  # type: ignore[method-assign]
    opt._generate_suggestion = AsyncMock(  # type: ignore[method-assign]
        return_value={"suggested_change": {"field": "system_prompt", "new_value": "better"}}
    )
    opt._create_experiment = AsyncMock(return_value="exp-y")  # type: ignore[method-assign]
    assert await opt._maybe_start_experiment("t1", "a1", None) == "exp-y"
    kwargs = opt._create_experiment.await_args.kwargs
    assert kwargs["candidate_config"]["system_prompt"] == "better"
    assert kwargs["control_config"]["system_prompt"] == "old prompt"


async def test_excluded_experiment_concludes_and_unblocks() -> None:
    redis = _Redis()
    executed: list[tuple[str, dict[str, Any]]] = []

    class _S:
        async def __aenter__(self) -> _S:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
            executed.append((str(stmt), dict(params or {})))
            return None

        async def commit(self) -> None:
            return None

    opt = SelfOptimizerV2(redis, lambda: _S(), lambda: None)
    await opt._state.update("t1", "a1", {"current_experiment_id": "exp-1"})
    await opt.conclude_unrealizable("t1", "a1", "exp-1", keys=["max_iterations"])
    assert (await opt._state.get("t1", "a1"))["current_experiment_id"] is None
    update = next(p for sql, p in executed if "UPDATE improvement_experiments" in sql)
    assert update["exp_id"] == "exp-1" and update["tenant_id"] == "t1"


async def test_initialize_concludes_excluded_experiment() -> None:
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    tenant = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    opt = AsyncMock()
    opt.get_arm_assignment = AsyncMock(
        return_value={
            "arm": "candidate",
            "experiment_id": "exp-1",
            "config": {},
            "changed_keys": ["max_iterations"],
        }
    )
    opt.conclude_unrealizable = AsyncMock()
    g = AgentGraph(planner=FakeProvider(), executor=FakeProvider(), verifier=FakeProvider())
    g._agent_id = "a1"
    state = AgentState(goal="g", tenant_ctx=tenant)
    await g._apply_experiment_arm(opt, state, tenant)
    opt.conclude_unrealizable.assert_awaited_once()
    assert opt.conclude_unrealizable.await_args.args[:3] == ("t1", "a1", "exp-1")
    assert "_experiment_arm" not in state.context


def test_applicable_keys_match_the_runtime() -> None:
    from app.agent.nodes.initialize_mixin import InitializeMixin

    assert InitializeMixin._EXPERIMENT_APPLICABLE_KEYS == EXPERIMENT_APPLICABLE_KEYS



# ── a05-F095-04: the rollout gate pins a fully-autonomous agent's config ─────
#
# Owner decision: applying a winner to a fully-autonomous agent is accepted; the
# agent is demoted in the same transaction and its eval suite re-run against
# the new config (promotion back is the post-run hook's job).


async def _set_autonomy(agents_db: Any, mode: str, suite: str | None = None) -> None:
    async with agents_db() as s:
        await s.execute(
            text("UPDATE agents SET autonomy_mode = :m, eval_suite_id = :s WHERE id = 'a1'"),
            {"m": mode, "s": suite},
        )
        await s.commit()


async def _agent_row(agents_db: Any) -> tuple[Any, ...]:
    async with agents_db() as s:
        res = await s.execute(
            text("SELECT system_prompt, autonomy_mode FROM agents WHERE id = 'a1'")
        )
        return tuple(res.fetchone())


async def _marker(agents_db: Any) -> dict[str, Any] | None:
    import json as _json

    async with agents_db() as s:
        res = await s.execute(text("SELECT autonomy_revalidation FROM agents WHERE id = 'a1'"))
        raw = res.fetchone()[0]
    return _json.loads(raw) if isinstance(raw, str) else raw


class _Dispatched:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, tenant_id: str, plan: str, run_id: str) -> None:
        self.calls.append((tenant_id, plan, run_id))


async def _suite(tenant: str, suite: str) -> Any:
    from app.intelligence.eval_suite_store import EvalSuiteStore

    store = EvalSuiteStore(None, tenant)
    await store.create(suite, name=suite, description="")
    await store.import_tasks(
        suite, [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(5)], replace=False
    )
    return store


def _optimizer(agents_db: Any, tenant: str, dispatch: Any = None) -> SelfOptimizerV2:
    from app.intelligence.eval_suite_store import EvalSuiteStore

    return SelfOptimizerV2(
        _Redis(), agents_db, lambda: None,
        eval_store_factory=lambda tid: EvalSuiteStore(None, tid),
        revalidation_dispatcher=dispatch,
    )


async def test_apply_to_a_fully_autonomous_agent_demotes_it_and_starts_its_suite(
    agents_db: Any,
) -> None:
    import uuid as _uuid

    tenant = f"t1-{_uuid.uuid4().hex[:6]}"
    async with agents_db() as s:
        await s.execute(text("UPDATE agents SET tenant_id = :t WHERE id = 'a1'"), {"t": tenant})
        await s.commit()
    suite = f"s-{_uuid.uuid4().hex[:6]}"
    store = await _suite(tenant, suite)
    await _set_autonomy(agents_db, "fully-autonomous", suite)
    dispatched = _Dispatched()
    outcome: dict[str, Any] = {}
    reason = await _optimizer(agents_db, tenant, dispatched)._apply_suggestion(
        tenant, "a1", "exp-1",
        {"system_prompt": "new prompt", "autonomy_mode": "fully-autonomous"},
        outcome=outcome,
    )
    assert reason is None
    assert await _agent_row(agents_db) == ("new prompt", "bounded-autonomous")
    marker = await _marker(agents_db)
    assert marker is not None and marker["state"] == "pending"
    assert marker["reason"] == "config_changed_pending_eval"
    assert marker["source"] == "self_optimizer_apply:exp-1"
    assert outcome["revalidation"]["run_id"] == marker["run_id"]
    assert dispatched.calls == [(tenant, "free", marker["run_id"])]
    run = await store.get_run(marker["run_id"])
    assert run is not None and run["status"] == "running" and run["agent_id"] == "a1"
    assert run["agent_config_hash"] == marker["agent_config_hash"]


async def test_apply_when_the_run_cannot_start_still_demotes_and_says_why(
    agents_db: Any,
) -> None:
    await _set_autonomy(agents_db, "fully-autonomous", None)  # no suite attached
    reason = await _optimizer(agents_db, "t1")._apply_suggestion(
        "t1", "a1", "exp-1", {"system_prompt": "new prompt"}
    )
    assert reason is None
    assert await _agent_row(agents_db) == ("new prompt", "bounded-autonomous")
    marker = await _marker(agents_db)
    assert marker is not None and marker["state"] == "failed"
    assert "No eval suite" in marker["error"]


async def test_apply_pending_returns_the_revalidation(agents_db: Any) -> None:
    await _set_autonomy(agents_db, "fully-autonomous", None)
    opt = _optimizer(agents_db, "t1")
    outcome: dict[str, Any] = {}
    assert await opt._apply_suggestion(
        "t1", "a1", "exp-1", {"system_prompt": "new prompt"}, outcome=outcome
    ) is None
    assert outcome["revalidation"]["reason"] == "config_changed_pending_eval"


async def test_apply_proceeds_when_the_gate_is_disabled_by_the_owner(
    agents_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    await _set_autonomy(agents_db, "fully-autonomous")
    monkeypatch.setattr(get_settings(), "fully_autonomous_eval_gate_enabled", False)
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    assert await opt.apply_suggestion("t1", "a1", "exp-1", {"system_prompt": "new prompt"})
    assert await _agent_row(agents_db) == ("new prompt", "fully-autonomous")


async def test_apply_never_writes_the_candidates_stale_autonomy_mode(agents_db: Any) -> None:
    # The experiment snapshot said fully-autonomous; the agent has since been
    # demoted. Applying the winner must not silently re-promote it.
    opt = SelfOptimizerV2(_Redis(), agents_db, lambda: None)
    ok = await opt.apply_suggestion(
        "t1", "a1", "exp-1", {"system_prompt": "new prompt", "autonomy_mode": "fully-autonomous"}
    )
    assert ok is True
    assert await _agent_row(agents_db) == ("new prompt", "bounded-autonomous")
