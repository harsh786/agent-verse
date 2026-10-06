"""B7-2: every path that fails a goal publishes ``goal.failed`` for goal_failed triggers.

Only the API's GoalService and the run_goal worker's event path published it.
The maintenance paths that fail goals out-of-band — the stuck-goal detector, the
stale-runner watchdog, the HITL-expiry sweep and the Celery DLQ — wrote
``status='failed'`` and nothing else, so "when a goal fails, run X" never fired
for exactly the failures an operator most wants to react to. A worker run that
ended ``failed`` without the graph's own ``goal_failed`` event was missed too.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

from app.core.config import get_settings
from app.triggers.consumers.chain import goal_failed_chain_event

S = get_settings()


def _goal_entries(redis: Any) -> list[dict[str, Any]]:
    return [
        json.loads(f["data"])
        for _, f in redis.xrange(S.trigger_bus_stream_goal)
        if f["channel"] == "goal.failed"
    ]


def test_goal_failed_event_carries_the_goals_lineage() -> None:
    ev = json.loads(
        goal_failed_chain_event(
            tenant_id="t", goal_id="g", agent_id="a",
            execution_context={"trigger_chain_depth": 2, "source_trigger_id": "tr"},
        )
    )
    assert ev["goal_id"] == "g" and ev["agent_id"] == "a" and ev["status"] == "failed"
    assert ev["trigger_chain_depth"] == 2 and ev["source_trigger_id"] == "tr"
    assert ev["completion_event_id"] == "g:goal.failed"


def test_worker_helper_publishes_to_the_goal_stream_and_skips_dry_runs() -> None:
    from app.scaling import tasks

    r = fakeredis.FakeRedis(decode_responses=True)
    with patch.object(tasks, "_get_sync_redis", return_value=r):
        assert tasks._publish_goal_failed_chain(
            {"tenant_id": "t", "goal_id": "g1", "agent_id": "", "execution_context": {}}
        )
        assert not tasks._publish_goal_failed_chain(
            {"tenant_id": "t", "goal_id": "g2", "dry_run": True, "execution_context": {}}
        )
    assert [e["goal_id"] for e in _goal_entries(r)] == ["g1"]


def _session_returning(rows: list[tuple[Any, ...]]) -> Any:
    result = MagicMock()
    result.fetchall.return_value = rows
    result.first.return_value = rows[0] if rows else None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    return session


async def test_stuck_goal_detector_publishes_goal_failed() -> None:
    from app.scaling import tasks

    session = _session_returning(
        [("g-stuck", "t1", "agent-1", {"trigger_chain_depth": 1}, False)]
    )
    published: list[dict[str, Any]] = []
    with (
        patch("app.db.session.get_system_session_factory", return_value=lambda: session),
        patch("app.db.rls.system_session") as sys_ctx,
        patch.object(tasks, "_publish_goal_failed_chain", side_effect=published.append),
    ):
        sys_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        sys_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        out = await tasks._find_and_fail_stuck_goals()
    assert out["stuck_goals_failed"] == 1
    assert published == [
        {
            "tenant_id": "t1",
            "goal_id": "g-stuck",
            "agent_id": "agent-1",
            "execution_context": {"trigger_chain_depth": 1},
            "dry_run": False,
        }
    ]


async def test_dead_lettered_goal_publishes_goal_failed() -> None:
    from app.scaling import tasks

    session = _session_returning([("agent-9", {"source_trigger_id": "tr"}, False)])
    published: list[dict[str, Any]] = []
    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.db.rls.sqlalchemy_rls_context") as rls,
        patch.object(tasks, "_publish_goal_failed_chain", side_effect=published.append),
    ):
        rls.return_value.__aenter__ = AsyncMock(return_value=session)
        rls.return_value.__aexit__ = AsyncMock(return_value=False)
        await tasks._update_goal_dlq("g-dlq", "t1", "boom")
    assert published == [
        {
            "tenant_id": "t1",
            "goal_id": "g-dlq",
            "agent_id": "agent-9",
            "execution_context": {"source_trigger_id": "tr"},
            "dry_run": False,
        }
    ]


async def test_dead_letter_of_an_already_finished_goal_publishes_nothing() -> None:
    from app.scaling import tasks

    session = _session_returning([])
    with (
        patch("app.db.session.get_session_factory", return_value=lambda: session),
        patch("app.db.rls.sqlalchemy_rls_context") as rls,
        patch.object(tasks, "_publish_goal_failed_chain") as pub,
    ):
        rls.return_value.__aenter__ = AsyncMock(return_value=session)
        rls.return_value.__aexit__ = AsyncMock(return_value=False)
        await tasks._update_goal_dlq("g-done", "t1", "boom")
    pub.assert_not_called()


async def test_parked_goal_failures_announce_goal_failed() -> None:
    from app.governance import hitl_expiry

    failed = [
        {
            "goal_id": "g-park", "tenant_id": "t1", "request_id": "r1", "reason": "x",
            "agent_id": "agent-2", "dry_run": False, "execution_context": {},
        }
    ]
    seen: list[dict[str, Any]] = []
    with patch("app.services.event_store.EventStore") as store_cls:
        store_cls.return_value.append_event = AsyncMock(return_value=1)
        await hitl_expiry.announce_parked_goal_failures(
            MagicMock(), failed, on_failed=seen.append
        )
    assert [g["goal_id"] for g in seen] == ["g-park"]


@pytest.mark.parametrize(
    ("status", "channel"), [("failed", "goal.failed"), ("complete", "goal.completed")]
)
def test_worker_terminal_status_maps_to_a_chain_channel(status: str, channel: str) -> None:
    from app.scaling.tasks import _chain_channel_for_worker_event

    assert _chain_channel_for_worker_event({"type": "worker_complete", "status": status}) == channel
    assert _chain_channel_for_worker_event({"type": "worker_complete", "status": "cancelled"}) is None
    assert _chain_channel_for_worker_event({"type": "goal_failed"}) == "goal.failed"
    assert _chain_channel_for_worker_event({"type": "tool_call"}) is None
