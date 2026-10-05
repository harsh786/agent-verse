"""P8-3: the real-world suite's harness matches the API it drives (offline).

GOV-BUDGET-CAP raised AttributeError after 0.0 s — ``LiveAPI`` had no ``put`` —
and SCHED-FIRES-GOAL timed out on a schedule that fired correctly, because it
read ``events``/``items`` from ``GET /schedules/{id}/history``, which returns
``{"runs": [...]}``. These run without the live stack.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from tests.real_world.helpers import LiveAPI, schedule_history_runs


def test_live_api_put_sends_a_put_with_the_json_body() -> None:
    seen: list[tuple[str, str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"per_goal_usd": 0.0001})

    api = LiveAPI("k", base_url="http://agentverse.test")
    api.client = httpx.Client(base_url="http://agentverse.test",
                              transport=httpx.MockTransport(_handler))
    try:
        resp = api.put("/governance/budget", json={"per_goal_usd": 0.0001})
    finally:
        api.close()
    assert resp.status_code == 200
    assert seen == [("PUT", "/governance/budget", {"per_goal_usd": 0.0001})]


def test_schedule_history_runs_reads_the_runs_the_api_returns() -> None:
    # The shape app/api/schedules.py:get_schedule_history returns.
    body = {
        "runs": [
            {"run_id": "e2", "goal_id": None, "status": "skipped"},
            {"run_id": "e1", "goal_id": "g-1", "status": "success"},
        ],
        "total": 2,
        "schedule_id": "s1",
    }
    runs = schedule_history_runs(body)
    assert [r.get("goal_id") for r in runs] == [None, "g-1"]
    assert any(r.get("goal_id") for r in runs)  # the SCHED-FIRES-GOAL done-predicate
    assert schedule_history_runs({"runs": [], "total": 0}) == []
    # Older shapes stay readable.
    assert schedule_history_runs([{"goal_id": "g"}]) == [{"goal_id": "g"}]
    assert schedule_history_runs({"items": [{"goal_id": "g"}]}) == [{"goal_id": "g"}]
    assert schedule_history_runs(None) == []
