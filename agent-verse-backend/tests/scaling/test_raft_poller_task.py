"""The RAFT fine-tune status poller is a registered, scheduled maintenance task."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.rag.raft import RAFTService
from app.scaling.celery_app import celery_app

TASK_NAME = "app.scaling.raft_tasks.poll_raft_fine_tune_jobs"


def test_poller_task_is_registered_on_the_maintenance_queue() -> None:
    import app.scaling.raft_tasks  # noqa: F401  (worker imports it via celery include)

    task = celery_app.tasks.get(TASK_NAME)
    assert task is not None
    assert task.queue == "maintenance"
    assert "app.scaling.raft_tasks" in celery_app.conf.include
    routes = cast(Mapping[str, Mapping[str, str]], celery_app.conf.task_routes)
    assert routes[TASK_NAME]["queue"] == "maintenance"


def test_poller_is_wired_into_the_beat_schedule() -> None:
    beat_schedule = cast(Mapping[str, Mapping[str, Any]], celery_app.conf.beat_schedule)
    entry = beat_schedule["poll-raft-fine-tune-jobs"]

    assert entry["task"] == TASK_NAME
    assert cast(Mapping[str, str], entry["options"])["queue"] == "maintenance"
    assert float(entry["schedule"]) <= 300.0


def test_poller_task_runs_one_bounded_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import raft_tasks

    calls: list[dict[str, Any]] = []

    async def fake_poll(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"status": "ok", "scanned": 0}

    monkeypatch.setattr("app.rag.raft_wiring.poll_raft_jobs_once", fake_poll)

    assert raft_tasks.poll_raft_fine_tune_jobs.run() == {"status": "ok", "scanned": 0}
    assert calls == [{}]


def test_worker_retrieval_gateway_gets_a_raft_service() -> None:
    from app.scaling.tasks import _build_worker_raft_service

    settings = SimpleNamespace(
        openai_api_key="sk-worker",
        raft_max_training_chunks=100,
        raft_chunk_page_size=10,
        raft_max_eval_examples=5,
    )

    def factory() -> Any:
        raise AssertionError("not opened during construction")

    service = _build_worker_raft_service(settings, factory)

    assert isinstance(service, RAFTService)
    assert service.supported_provider_ids == frozenset({"openai"})
