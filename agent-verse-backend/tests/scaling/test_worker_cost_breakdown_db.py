"""A Celery-executed goal must record its cost breakdown to Postgres, not worker memory.

Regression: only the API lifespan wired cost-breakdown persistence, so a goal run
by the worker recorded planner/executor/verifier costs into the worker process's
dict and ``GET /goals/{id}/cost-metrics`` on the API always showed nothing.
"""

from __future__ import annotations

from typing import Any

import pytest


class _State:
    class Status:
        value = "complete"

    status = Status()
    iterations = 1


def test_run_goal_binds_the_durable_cost_breakdown_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent.graph as graph_mod
    import app.db.session as session_mod
    from app.observability import cost_breakdown as cb
    from app.scaling import tasks

    cb.reset_db()
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setenv("ENVIRONMENT", "development")

    bound_during_run: list[Any] = []

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> _State:
            bound_during_run.append(cb._db)
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    tasks.run_goal.run("goal-cb-db", "tenant-1", "g", "normal", False)

    assert bound_during_run and bound_during_run[0] is not None, (
        "worker ran the graph with cost breakdowns in process memory only"
    )

    # The binding resolves the CURRENT engine each time (the worker disposes the
    # engine after every task), rather than capturing a stale sessionmaker.
    sentinel = object()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: lambda: sentinel)
    assert bound_during_run[0]() is sentinel
    cb.reset_db()
