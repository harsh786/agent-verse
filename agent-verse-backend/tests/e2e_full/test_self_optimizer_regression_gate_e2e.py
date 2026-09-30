"""e2e_full: SelfOptimizerV2 must not auto-apply a winner that regresses cost/latency.

The experiment winner used to be decided on the success metric alone, so a
candidate that scored a little higher while costing 5x as much was written
back to the agent automatically. Conclusion now also runs the RegressionGate
cost / p95-latency check over the per-arm results — computed in SQL
(``AVG(...) FILTER`` / ``percentile_cont ... FILTER``), which is why this runs
against real Postgres on the application role under tenant RLS.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _seed(
    owner: Any, *, tenant_id: str, agent_id: str, cand_cost: float, cand_latency: int
) -> str:
    from sqlalchemy import text

    exp_id = uuid.uuid4().hex
    async with owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'selfopt', :e) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"t": tenant_id, "e": f"{tenant_id}@selfopt.test"},
        )
        await s.execute(
            text(
                "INSERT INTO agents (id, tenant_id, name) VALUES (:a, :t, 'opt-agent') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"a": agent_id, "t": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO improvement_experiments (id, tenant_id, agent_id, name, "
                "control_config, candidate_config, suggestion_rationale, min_samples_per_arm) "
                "VALUES (:id, :t, :a, 'exp', CAST(:ctl AS jsonb), CAST(:cand AS jsonb), "
                "'shorter prompt', 20)"
            ),
            {
                "id": exp_id,
                "t": tenant_id,
                "a": agent_id,
                "ctl": json.dumps({"system_prompt": "v1"}),
                "cand": json.dumps({"system_prompt": "v2"}),
            },
        )
        for arm, metric, cost, latency in (
            ("control", 0.70, 0.010, 1000),
            ("candidate", 0.95, cand_cost, cand_latency),
        ):
            for i in range(25):
                # Scored goals: since MEM-27 an unscored completion is recorded
                # with eval_score NULL and the conclusion samples scored rows only.
                await s.execute(
                    text(
                        "INSERT INTO improvement_results (experiment_id, tenant_id, arm, "
                        "metric_value, metric_name, eval_score, cost_usd, latency_ms) "
                        "VALUES (:e, :t, :arm, :m, 'eval_score', :m, :c, :l)"
                    ),
                    # Small spread so the posterior is decisive but not degenerate.
                    {"e": exp_id, "t": tenant_id, "arm": arm, "m": metric + (i % 3) * 0.001,
                     "c": cost, "l": latency + i},
                )
    return exp_id


async def _experiment(owner: Any, exp_id: str) -> tuple[str, str, str]:
    from sqlalchemy import text

    async with owner() as s:
        row = (
            await s.execute(
                text(
                    "SELECT winner, status, suggestion_rationale "
                    "FROM improvement_experiments WHERE id = :id"
                ),
                {"id": exp_id},
            )
        ).one()
    return str(row[0]), str(row[1]), str(row[2])


async def _conclude(app_factory: Any, tenant_id: str, exp_id: str) -> list[str]:
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2

    applied: list[str] = []
    opt = SelfOptimizerV2(AsyncMock(), app_factory, AsyncMock(), auto_apply=True)

    async def _spy(tid: str, aid: str, eid: str, cfg: dict[str, Any]) -> bool:
        applied.append(eid)
        return True

    opt.apply_suggestion = _spy  # type: ignore[method-assign]
    await opt._maybe_conclude_experiment(tenant_id, exp_id)
    return applied


async def test_costlier_winner_is_held_and_not_applied(
    _backends: tuple[str, str], _migrated_backends: tuple[str, str]
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    owner_engine = create_async_engine(_backends[0])
    app_engine = create_async_engine(_migrated_backends[0])
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        tenant_id = uuid.uuid4().hex
        pricey = await _seed(
            owner, tenant_id=tenant_id, agent_id=uuid.uuid4().hex, cand_cost=0.05,
            cand_latency=1000,
        )
        slow = await _seed(
            owner, tenant_id=tenant_id, agent_id=uuid.uuid4().hex, cand_cost=0.010,
            cand_latency=3000,
        )
        fine = await _seed(
            owner, tenant_id=tenant_id, agent_id=uuid.uuid4().hex, cand_cost=0.0105,
            cand_latency=1050,
        )

        assert await _conclude(app_factory, tenant_id, pricey) == []
        winner, status, rationale = await _experiment(owner, pricey)
        assert (winner, status) == ("control", "completed")
        assert "cost_regression" in rationale and rationale.startswith("shorter prompt")

        assert await _conclude(app_factory, tenant_id, slow) == []
        winner, _, rationale = await _experiment(owner, slow)
        assert winner == "control" and "latency_regression" in rationale

        # Within the 10% cost / 15% p95 budget: the better candidate still wins.
        assert await _conclude(app_factory, tenant_id, fine) == [fine]
        winner, _, rationale = await _experiment(owner, fine)
        assert winner == "candidate" and "held" not in rationale
    finally:
        await owner_engine.dispose()
        await app_engine.dispose()
