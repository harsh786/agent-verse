"""P1b-1 (live, MinIO): a cancel the API records reaches the worker.

Live finding (2026-10-05): after one manual sync every later POST /sources/{id}/sync
answered ``already_running`` until the API restarted, and a cancel never reached
the worker — the trackers had no shared Redis. Main's TG-12 lock is the single
implementation now; the API -> worker -> API "second sync starts" flow is pinned on
real Redis + Postgres in test_sync_lock_integration.py. This keeps the cancel half
(the scenario SRC-OBJ-PAGINATION cancels a running sync) with the API's
decode_responses=True client.
"""

from __future__ import annotations

import fakeredis.aioredis

from app.ingestion.job_tracker import IngestionJobTracker


async def test_a_cancel_set_by_the_api_reaches_the_worker_tracker() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    api, worker = IngestionJobTracker(redis=redis), IngestionJobTracker(redis=redis)
    token = await api.acquire_lock("src-1", "t1")
    assert token
    assert await api.request_cancel("src-1", "t1") == token
    assert await worker.is_cancel_requested("t1", str(token)) is True
