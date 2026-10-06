"""No run_goal exit may leave its heartbeat thread running.

The heartbeat thread used to start before several early returns (dry run,
unusable BYOK key, graph assembly failure) that sit outside the try/finally that
stops it. With the database unreachable the thread never reads the goal as
terminal, so it outlived the run and later logged into a closed stream — an
unhandled thread exception pytest attributed to an unrelated test.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")


def _heartbeat_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("goal-heartbeat-hb-leak")]


@pytest.fixture(autouse=True)
def _force_heartbeat_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    real = tasks._start_goal_heartbeat

    def _always(goal_id: str, tenant_id: str, lock: Any, *, enabled: bool) -> Any:
        return real(goal_id, tenant_id, lock, enabled=True)

    monkeypatch.setattr(tasks, "_start_goal_heartbeat", _always)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setenv("ENVIRONMENT", "development")


def test_graph_assembly_failure_leaves_no_heartbeat_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    class _Broken:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("graph unavailable")

    monkeypatch.setattr(graph_mod, "AgentGraph", _Broken)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    result = tasks.run_goal.run("hb-leak-graph", "tenant-1", "g", "normal", False)
    assert result["reason"] == "agentgraph_assembly_failed"
    assert _heartbeat_threads() == []


def test_dry_run_leaves_no_heartbeat_thread() -> None:
    from app.scaling import tasks

    result = tasks.run_goal.run("hb-leak-dry", "tenant-1", "g", "normal", True)
    assert result["dry_run"] is True
    assert _heartbeat_threads() == []
