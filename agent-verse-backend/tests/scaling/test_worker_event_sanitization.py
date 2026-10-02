"""CORE-34: worker-level goal events are sanitized before Redis publish / event store.

run_goal's append_submitted_goal_event published and persisted its own events
(``worker_failed`` with ``reason=str(exc)``, ...) without ``sanitize_event``, so
credentials in exception text reached SSE and the goal's event log, and the
goal row's ``error_message`` stored the raw text too.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

_SECRET = "sk_" + "live_" + "ABCDEFGHIJKLMNOPQRST1234"  # split: secret scanners


class _DeniedGraph:
    def __init__(self, **kwargs: Any) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        raise PermissionError(f"denied by upstream: Authorization: Bearer {_SECRET}")


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, data))

    # A readable, empty control plane: no emergency stop, no cancel flag
    # (run_goal fails closed on an unreadable stop state, WF-16).
    def get(self, key: str) -> None:
        return None

    def smembers(self, key: str) -> set[str]:
        return set()

    def scan_iter(self, *a: Any, **k: Any) -> Any:
        return iter(())

    def __getattr__(self, name: str) -> Any:  # xadd etc. used by other publishers
        return lambda *a, **k: None


def test_worker_failed_event_and_error_message_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    redis = _Redis()
    statuses: list[dict[str, Any]] = []

    async def _record_status(self: Any, goal_id: str, tenant_id: str, status: str,
                             **kw: Any) -> None:
        statuses.append({"status": status, **kw})

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(_graph_mod, "AgentGraph", _DeniedGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    monkeypatch.setattr(tasks, "_finalize_owning_mission", _noop)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _noop)
    monkeypatch.setattr(GoalService, "_db_update_goal_status", _record_status)

    tasks.run_goal.push_request(retries=0, called_directly=True)
    try:
        out = tasks.run_goal.run("goal-redact-1", "tenant-1", "do the thing")
    finally:
        tasks.run_goal.pop_request()

    payloads = [json.loads(d) for _, d in redis.published]
    failed = [p for p in payloads if isinstance(p, dict) and p.get("type") == "worker_failed"]
    assert failed, payloads
    assert _SECRET not in json.dumps(payloads)
    assert "[REDACTED]" in failed[0]["payload"]["reason"]
    assert failed[0]["payload"]["reason"].startswith("PermissionError")
    errors = [s.get("error_message", "") for s in statuses if s["status"] == "failed"]
    assert errors and all(_SECRET not in e for e in errors)
    assert _SECRET not in json.dumps(out)
