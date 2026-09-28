"""Regression: an agent permission rule's ``daily_limit`` is enforced.

``daily_limit`` was loaded from ``agent_permissions`` but never checked — only
``per_goal_limit`` was — so a "3 calls/day" rule allowed unlimited calls as long
as each goal stayed under its per-goal limit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest

from app.agent.state import AgentState
from app.governance import agent_permissions as ap
from tests.governance._router_app import tenant
from tests.governance.test_agent_permissions_enforced import _DB, _executor


@pytest.fixture(autouse=True)
def _clear() -> Any:
    ap._CACHE.clear()
    ap._LOCAL_DAILY.clear()
    yield
    ap._LOCAL_DAILY.clear()


async def _call(ex: Any) -> str | None:
    # A fresh goal each time: the per-goal counter never trips.
    state = AgentState(goal="g", goal_id="goal-x", tenant_ctx=tenant("t-p"))
    return await ex._agent_permission_gate(  # type: ignore[no-any-return]
        state=state, tenant_ctx=tenant("t-p"), tool_name="web_search", step="s"
    )


@pytest.mark.asyncio
async def test_daily_limit_applies_across_goals_in_process() -> None:
    ex = _executor(_DB(rows=[("web_search", "allow", 2, None, None)]))
    assert await _call(ex) is None
    assert await _call(ex) is None
    denial = await _call(ex)
    assert denial is not None and "daily_limit" in denial


@pytest.mark.asyncio
async def test_daily_limit_is_shared_through_redis() -> None:
    redis = fakeredis.FakeAsyncRedis()
    ex_a = _executor(_DB(rows=[("web_search", "allow", 1, None, None)]))
    ex_b = _executor(_DB(rows=[("web_search", "allow", 1, None, None)]))
    for ex in (ex_a, ex_b):
        ex._app_state = SimpleNamespace(state=SimpleNamespace(_redis=redis))
    assert await _call(ex_a) is None
    ap._CACHE.clear()
    assert await _call(ex_b) is not None  # another replica sees the same count


@pytest.mark.asyncio
async def test_daily_limit_counter_error_fails_closed() -> None:
    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=ConnectionError("down"))
    ex = _executor(_DB(rows=[("web_search", "allow", 5, None, None)]))
    ex._app_state = SimpleNamespace(state=SimpleNamespace(_redis=redis))
    assert "failing closed" in (await _call(ex) or "")
