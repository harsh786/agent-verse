"""B7-4: a feedback lesson written to long-term memory publishes ``memory.created``.

``_store_feedback_lesson`` built a bare ``LongTermMemoryStore()`` with no event
Redis, so the lessons the daily feedback task writes (from the Celery worker)
never fired memory_created triggers.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis

from app.core.config import get_settings
from app.evals.self_improvement_engine import _store_feedback_lesson, feedback_lesson_id

S = get_settings()


async def test_feedback_lesson_publishes_memory_created() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    with patch("app.memory.long_term._GUARDRAILS_AVAILABLE", True):
        mid = await _store_feedback_lesson(
            None, tenant_id="t-fb", feedback_id="fb-1", goal_id="g-fb",
            lesson="always confirm the region first", event_redis=redis,
        )
    entries = [
        (f["channel"], json.loads(f["data"]))
        for _, f in await redis.xrange(S.trigger_bus_stream_memory)
    ]
    assert [c for c, _ in entries] == ["memory.created"]
    data = entries[0][1]
    assert data["memory_id"] == mid == feedback_lesson_id("t-fb", "fb-1")
    assert data["source_goal_id"] == "g-fb" and data["memory_type"] == "failure_pattern"


async def test_feedback_task_hands_the_engine_an_event_redis() -> None:
    from app.scaling import tasks

    result = MagicMock()
    result.fetchall.return_value = [("t-fb",)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    event_redis = MagicMock()
    event_redis.aclose = AsyncMock()
    seen: dict[str, Any] = {}

    async def _batch(**kw: Any) -> dict[str, int]:
        seen.update(kw)
        return {"processed": 1, "actions_derived": 1}

    with (
        patch("app.db.rls.system_session") as sys_ctx,
        patch.object(tasks, "_worker_async_redis", return_value=event_redis),
        patch(
            "app.evals.self_improvement_engine.SelfImprovementEngine.process_feedback_batch",
            side_effect=_batch,
        ),
        patch("app.providers.embedder_factory.build_query_embedder", return_value=None),
    ):
        sys_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        sys_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        out = await tasks._process_feedback_batch_async(db=MagicMock(), system_db=lambda: session)
    assert out == {"processed": 1, "actions_derived": 1}
    assert seen["event_redis"] is event_redis
    event_redis.aclose.assert_awaited_once()
