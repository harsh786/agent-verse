"""a05-F092-01: worker-verified goals persist their verifier verdicts.

Only the API lifespan bound the module-default ``VerifierCalibrationStore`` to
the database. The Celery worker — which runs every queued goal — passed that
unbound default to its graph, so worker verdicts were never written: feedback
on those goals updated 0 rows and ``/intelligence/calibration`` left them out.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.intelligence.verifier_calibration import (
    VerifierCalibrationStore,
    _default_calibration_store,
    calibration_store_for,
)


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {}

    class _State:
        class Status:
            value = "complete"

        status = Status()
        iterations = 1

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None
            seen["graph_kwargs"] = kwargs

        async def run(self, **kwargs: Any) -> Any:
            return _State()

    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_worker_graph_records_verdicts_through_a_db_bound_store(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db.session as session_mod
    from app.scaling import tasks

    real_factory = session_mod.get_session_factory
    factories: list[Any] = []

    def _tracking_factory() -> Any:
        factory = real_factory()
        factories.append(factory)
        return factory

    monkeypatch.setattr(session_mod, "get_session_factory", _tracking_factory)
    assert _default_calibration_store._db is None  # never bound outside the API lifespan

    result = tasks.run_goal.run("g-f092", "t-f092", "summarise the report", "normal", False)

    assert result["status"] == "complete", result
    store = worker["graph_kwargs"].get("calibration_store")
    assert isinstance(store, VerifierCalibrationStore)
    assert store._db is not None, "worker verdicts must be persisted"
    assert store._db in factories


def test_calibration_store_for_binds_to_the_factory() -> None:
    def factory() -> Any:  # pragma: no cover - never opened here
        raise AssertionError

    store = calibration_store_for(factory)
    assert store._db is factory
    assert calibration_store_for(None) is _default_calibration_store
