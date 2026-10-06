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


@pytest.mark.asyncio
async def test_local_daily_counters_of_past_days_are_evicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a03-F061-04: the in-process counter keeps only today's keys."""
    for i in range(50):
        ap._LOCAL_DAILY[("t", f"a{i}", "web_search", "2020-01-01")] = 3
    assert await ap.reserve_daily_call(None, "t", "a", "web_search", 5) is True
    assert all(k[3] != "2020-01-01" for k in ap._LOCAL_DAILY)
    assert len(ap._LOCAL_DAILY) == 1


@pytest.mark.asyncio
async def test_local_daily_counter_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "_LOCAL_DAILY_MAX_KEYS", 10)
    for i in range(25):
        assert await ap.reserve_daily_call(None, "t", f"agent-{i}", "web_search", 5) is True
    assert len(ap._LOCAL_DAILY) <= 10
    # The newest counter is kept and still counts.
    assert await ap.reserve_daily_call(None, "t", "agent-24", "web_search", 2) is True
    assert await ap.reserve_daily_call(None, "t", "agent-24", "web_search", 2) is False
