"""SelfOptimizerV2: every improvement_* / agent_optimization_history statement is
tenant-scoped twice — by the RLS GUC and by an explicit ``tenant_id`` predicate.

Several statements addressed ``improvement_experiments`` by ``id`` alone
(rollback's read + update, the arm split, the candidate-config read, the
conclude update, the apply bookkeeping update). Under FORCE RLS the GUC keeps
them inside the tenant, but the explicit predicate is the defense in depth that
still holds if a policy is ever dropped or the code runs on a BYPASSRLS
connection. The conclude aggregate also joins ``improvement_results`` only on
the same tenant's rows.
"""

from __future__ import annotations

import json
from typing import Any

from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-opt-a"
AGENT = "agent-1"
EXP = "exp-1"


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self._kv[key] = value


def _optimizer(db: RlsRecordingDb, *, auto_apply: bool = False) -> SelfOptimizerV2:
    return SelfOptimizerV2(
        redis=_FakeRedis(), db_factory=db, llm_provider_factory=None, auto_apply=auto_apply
    )


async def test_rollback_is_tenant_scoped() -> None:
    def rows_for(sql: str, _p: dict[str, Any]) -> list[Any]:
        if "SELECT control_config FROM improvement_experiments" in sql:
            return [({"system_prompt": "old"},)]
        if "UPDATE agents" in sql:
            return [(1,)]  # one agent row updated
        return []

    db = RlsRecordingDb(rows_for=rows_for)
    assert await _optimizer(db).rollback(TENANT, AGENT, EXP, "regressed") is True

    stmts = assert_tenant_scoped(db, "improvement_experiments", TENANT, min_statements=2)
    assert all("AND tenant_id = :tenant_id" in s.sql for s in stmts)
    assert_tenant_scoped(db, "UPDATE agents", TENANT)


def _agent_rows(sql: str, _p: dict[str, Any]) -> list[Any]:
    # The agents table's real config columns (there is no agents.config).
    if "FROM agents" in sql:
        return [("old", "", "", "bounded-autonomous", 15, 300)]
    if "UPDATE agents" in sql:
        return [(1,)]
    return []


async def test_apply_suggestion_bookkeeping_is_tenant_scoped() -> None:
    db = RlsRecordingDb(rows_for=_agent_rows)
    ok = await _optimizer(db).apply_suggestion(TENANT, AGENT, EXP, {"system_prompt": "new"})

    assert ok is True
    assert_tenant_scoped(db, "agent_optimization_history", TENANT)
    (update,) = assert_tenant_scoped(db, "UPDATE improvement_experiments", TENANT)
    assert "WHERE id = :exp_id AND tenant_id = :tenant_id" in update.sql


async def test_conclude_experiment_is_tenant_scoped() -> None:
    def rows_for(sql: str, _p: dict[str, Any]) -> list[Any]:
        if "FROM improvement_experiments e" in sql:
            # min_n, threshold, metric, agent_id, candidate_config, ctrl_n, cand_n, means
            return [(1, 0.95, "eval_score", AGENT, json.dumps({"x": 1}), 5, 5, 0.5, 0.9)]
        return []

    db = RlsRecordingDb(rows_for=rows_for)
    await _optimizer(db, auto_apply=False)._maybe_conclude_experiment(TENANT, EXP)

    select, update = assert_tenant_scoped(db, "improvement_experiments", TENANT, min_statements=2)
    assert "r.tenant_id = e.tenant_id" in select.sql
    assert "WHERE id = :exp_id AND tenant_id = :tenant_id" in update.sql


async def test_arm_config_reads_are_tenant_scoped() -> None:
    def rows_for(sql: str, _p: dict[str, Any]) -> list[Any]:
        if "SELECT traffic_split_pct" in sql:
            return [(100,)]  # every goal lands in the candidate arm
        if "SELECT candidate_config" in sql:
            return [({"system_prompt": "candidate"},)]
        return []

    db = RlsRecordingDb(rows_for=rows_for)
    opt = _optimizer(db)
    await opt._state.update(TENANT, AGENT, {"current_experiment_id": EXP})

    cfg = await opt.get_arm_config(TENANT, AGENT, "goal-1")

    assert cfg == {"system_prompt": "candidate"}
    stmts = assert_tenant_scoped(db, "improvement_experiments", TENANT, min_statements=2)
    assert all("AND tenant_id = :tenant_id" in s.sql for s in stmts)
