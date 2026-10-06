"""B7 open item 5 (worker path): run_goal honours the agent's ``timeout_seconds``.

The Celery worker capped a goal at the plan's ``goal_timeout_seconds`` only; the
agent query did not even select ``timeout_seconds``. The budget is now
min(plan, agent) — see ``app.tenancy.limits.effective_goal_timeout`` and
``tests/services/test_agent_timeout_honoured.py`` for the in-process runner.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest


# ── Celery worker (run_goal) ──────────────────────────────────────────────────


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.guardrails_v2.engine import guardrails_engine
    from app.scaling import tasks

    seen: dict[str, Any] = {"lookups": []}

    class _HangingGraph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> Any:
            await asyncio.sleep(10)

    class _NoTenantRules:
        async def load(self, tenant_id: str) -> list[Any]:
            return []

    async def _agent_config(db_factory: Any, agent_id: str, tenant_id: str) -> Any:
        seen["lookups"].append((agent_id, tenant_id))
        return tasks._WorkerAgentConfig(
            "bounded-autonomous", None, "", [], "", seen.get("agent_timeout")
        )

    guardrails_engine.bind_repository(_NoTenantRules())  # restored by tests/conftest.py
    monkeypatch.setattr(graph_mod, "AgentGraph", _HangingGraph)
    monkeypatch.setattr(tasks, "_lookup_worker_agent_config", _agent_config)
    monkeypatch.setattr(tasks, "_worker_reflexion_service", lambda *a: None)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_worker_goal_is_stopped_at_the_agent_timeout(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    worker["agent_timeout"] = 1  # plan "free" -> 1800 s; the agent's 1 s binds
    result = tasks.run_goal.run(
        "gat1", "tenant-at", "hang forever", "normal", False, agent_id="agent-t"
    )

    assert worker["lookups"] == [("agent-t", "tenant-at")]
    assert result["status"] == "failed"
    assert result["reason"] == "timeout after 1s (agent timeout_seconds)"


def test_worker_lookup_selects_the_agent_timeout_column() -> None:
    """The agent query reads timeout_seconds with an explicit tenant predicate."""
    from app.scaling import tasks

    rows = [("fully-autonomous", 7, "sys", ["c1"], "m1", 90)]

    class _Result:
        def fetchone(self) -> Any:
            return rows[0]

    class _Session:
        sql = ""
        params: dict[str, Any] = {}

        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
            text = str(stmt)
            if "FROM agents" in text:
                _Session.sql, _Session.params = text, dict(params or {})
            return _Result()

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    cfg = asyncio.run(tasks._lookup_worker_agent_config(_Session, "agent-x", "tenant-x"))

    assert "timeout_seconds FROM agents" in _Session.sql
    assert "tenant_id = :tid" in _Session.sql
    assert _Session.params == {"aid": "agent-x", "tid": "tenant-x"}
    assert cfg.timeout_seconds == 90
    assert cfg.max_iterations == 7
    assert cfg.model_override == "m1"


def test_worker_lookup_without_a_row_has_no_agent_limit() -> None:
    from app.scaling import tasks

    class _Result:
        def fetchone(self) -> Any:
            return None

    class _Session:
        async def execute(self, stmt: Any, params: Any = None) -> _Result:
            return _Result()

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    cfg = asyncio.run(tasks._lookup_worker_agent_config(_Session, "a", "t"))
    assert cfg.timeout_seconds is None
    assert cfg.autonomy_mode == "bounded-autonomous"
    assert SimpleNamespace(**cfg._asdict()).collection_ids == []
