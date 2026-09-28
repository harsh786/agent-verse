"""SelfOptimizerV2 must start an experiment with the factory the app actually wires.

``create_app`` passes ``llm_provider_factory=lambda: _app_provider`` — a
*synchronous* factory. ``_generate_suggestion`` used to ``await`` it, which
raised ``TypeError`` (a provider is not awaitable), was swallowed by the broad
``except``, and returned ``None`` — so no experiment was ever created, no
matter how many goals completed. The existing tests only used ``AsyncMock``
factories, which hid the bug.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.intelligence.self_optimizer_v2 import DEFAULT_MIN_GOALS, SelfOptimizerV2


def _redis() -> MagicMock:
    store: dict[str, str] = {}
    r = MagicMock()

    async def _get(k: str) -> str | None:
        return store.get(k)

    async def _setex(k: str, _ttl: int, v: str) -> None:
        store[k] = v

    r.get = AsyncMock(side_effect=_get)
    r.setex = AsyncMock(side_effect=_setex)
    return r


class _Session:
    """Fake AsyncSession that answers each optimiser query by its SQL text."""

    def __init__(self, sql_log: list[str]) -> None:
        self._log = sql_log

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        self._log.append(sql)
        res = MagicMock()
        if "SELECT config FROM agents" in sql:
            res.fetchone.return_value = ({"system_prompt": "You are helpful."},)
        else:
            res.fetchone.return_value = None
        return res

    async def commit(self) -> None:
        return None


def _provider() -> MagicMock:
    suggestion = {
        "suggested_change": {"field": "system_prompt", "new_value": "You are precise."},
        "rationale": "tighter prompt",
    }
    p = MagicMock()
    p.complete = AsyncMock(return_value=MagicMock(content=json.dumps(suggestion)))
    return p


@pytest.mark.asyncio
async def test_experiment_created_after_min_goals_with_sync_factory() -> None:
    sql_log: list[str] = []
    provider = _provider()
    opt = SelfOptimizerV2(
        redis=_redis(),
        db_factory=lambda: _Session(sql_log),
        llm_provider_factory=lambda: provider,  # exactly how create_app wires it
    )

    for i in range(DEFAULT_MIN_GOALS):
        await opt.on_goal_completed(
            tenant_id="t1",
            agent_id="a1",
            goal_id=f"g{i}",
            eval_score=0.5,
            cost_usd=0.01,
            latency_ms=100,
        )

    provider.complete.assert_awaited()
    assert any("INSERT INTO improvement_experiments" in s for s in sql_log)
    state = await opt._state.get("t1", "a1")
    assert state["current_experiment_id"]


@pytest.mark.asyncio
async def test_async_factory_still_supported() -> None:
    provider = _provider()
    opt = SelfOptimizerV2(
        redis=_redis(),
        db_factory=lambda: _Session([]),
        llm_provider_factory=AsyncMock(return_value=provider),
    )
    out = await opt._generate_suggestion({"system_prompt": "x"}, {}, "eval_score")
    assert out is not None
    assert out["suggested_change"]["field"] == "system_prompt"


@pytest.mark.asyncio
async def test_no_provider_returns_none_without_crash() -> None:
    opt = SelfOptimizerV2(
        redis=_redis(), db_factory=lambda: _Session([]), llm_provider_factory=lambda: None
    )
    assert await opt._generate_suggestion({"system_prompt": "x"}, {}, "eval_score") is None
