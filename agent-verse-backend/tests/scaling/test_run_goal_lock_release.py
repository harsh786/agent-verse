"""WF-19: run_goal's early failure returns release the per-goal lock.

Old bug: a BYOK failure or an AgentGraph assembly failure returned without
releasing ``goal_lock:<id>``, so a retry/resubmission of that goal was refused
as "already executing" for the plan's goal timeout + 5 minutes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")


class _SyncRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, px: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def eval(self, script: str, numkeys: int, key: str, value: str) -> int:
        if self.store.get(key) == value:
            del self.store[key]
            return 1
        return 0

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def __getattr__(self, name: str) -> Any:  # anything else: harmless no-op
        return lambda *a, **k: None


@pytest.fixture
def lock_redis(monkeypatch: pytest.MonkeyPatch) -> _SyncRedis:
    from app.scaling import tasks

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://fake:6379/0")

    async def _claimed(goal_id: str, tenant_id: str) -> str:
        return "claimed"

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    def _no_db() -> Any:
        # Never reach a real database from this unit test (a developer's local
        # stack may be listening on the default DSN); run_goal's bookkeeping
        # writes are non-fatal.
        raise ConnectionError("no database in this test")

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "get_session_factory", _no_db)
    monkeypatch.setattr(db_session, "get_system_session_factory", _no_db)
    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claimed)
    monkeypatch.setattr(tasks, "_decrement_after_completion", _noop)
    return _SyncRedis()


def test_byok_failure_releases_the_goal_lock(
    lock_redis: _SyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.providers.tenant_provider import TenantProviderError
    from app.scaling import tasks

    def _broken(tenant_id: str) -> Any:
        raise TenantProviderError("tenant key cannot be decrypted")

    monkeypatch.setattr(tasks, "_get_llm_provider", _broken)
    with patch("redis.from_url", return_value=lock_redis):
        result = tasks.run_goal.run("goal-lock-1", "tenant-l", "a goal", "normal", False)

    assert result["reason"] == "tenant_llm_provider_unavailable"
    assert "goal_lock:goal-lock-1" not in lock_redis.store


def test_agentgraph_assembly_failure_releases_the_goal_lock(
    lock_redis: _SyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    class _BrokenGraph:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("graph unavailable")

    monkeypatch.setattr(graph_mod, "AgentGraph", _BrokenGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    with patch("redis.from_url", return_value=lock_redis):
        result = tasks.run_goal.run("goal-lock-2", "tenant-l", "a goal", "normal", False)

    assert result["reason"] == "agentgraph_assembly_failed"
    assert "goal_lock:goal-lock-2" not in lock_redis.store
