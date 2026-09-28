"""Regression: every beat task is registered and every routed queue is consumed.

The prod compose / k8s workers listened only on ``goals,schedules,maintenance``
while ``run_goal`` is routed to ``goals.{plan}`` and workflow runs to
``workflows.*`` — scheduled/API goals and workflow runs sat in Redis forever.
Beat also scheduled ``ingestion.dispatch_due_sources`` / ``retry_dlq_entries``,
which the worker never registered (``app.ingestion.scheduler`` was not in the
Celery ``include`` list), so every tick raised an unregistered-task error.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.scaling.celery_app import PLAN_QUEUE_MAP, celery_app

INFRA = Path(__file__).resolve().parents[2] / "infra"
_PLANS = ("free", "starter", "professional", "enterprise")


def _loaded_app() -> Any:
    celery_app.loader.import_default_modules()
    return celery_app


def _effective_queue(app: Any, name: str) -> str:
    default = app.conf.task_default_queue or "celery"
    q = app.amqp.router.route({}, name).get("queue")
    qn = getattr(q, "name", q)
    if qn and qn != default:
        return str(qn)
    task_q = getattr(app.tasks.get(name), "queue", None)
    return str(task_q or qn or default)


def _required_queues() -> set[str]:
    app = _loaded_app()
    queues = {app.conf.task_default_queue or "celery"}
    for name in app.tasks:
        if not name.startswith("celery."):
            queues.add(_effective_queue(app, name))
    for entry in app.conf.beat_schedule.values():
        queues.add(entry.get("options", {}).get("queue") or _effective_queue(app, entry["task"]))
    # Dynamic apply_async(queue=...) targets: CeleryGoalTaskQueue → PLAN_QUEUE_MAP,
    # WorkflowRunner → workflows.{plan_tier}.
    queues |= set(PLAN_QUEUE_MAP.values())
    queues |= {f"workflows.{p}" for p in _PLANS}
    return queues


def _queues_from_argv(argv: list[str]) -> set[str]:
    if "worker" not in argv:
        return set()
    for flag in ("-Q", "--queues"):
        if flag in argv:
            return {q.strip() for q in argv[argv.index(flag) + 1].split(",") if q.strip()}
    return {"celery"}  # a worker with no -Q consumes only the default queue


def _argv(command: Any) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(c) for c in (command or [])]


def _compose_worker_queues(path: Path) -> set[str]:
    doc = yaml.safe_load(path.read_text())
    consumed: set[str] = set()
    for svc in (doc.get("services") or {}).values():
        argv = _argv(svc.get("command"))
        if "celery" in argv:
            consumed |= _queues_from_argv(argv)
    return consumed


def _k8s_worker_queues(path: Path) -> set[str]:
    consumed: set[str] = set()
    for doc in yaml.safe_load_all(path.read_text()):
        if not doc:
            continue
        for c in doc["spec"]["template"]["spec"]["containers"]:
            consumed |= _queues_from_argv(_argv(c.get("command")) + _argv(c.get("args")))
    return consumed


def test_every_beat_task_is_registered() -> None:
    app = _loaded_app()
    missing = sorted(
        {e["task"] for e in app.conf.beat_schedule.values()} - set(app.tasks.keys())
    )
    assert missing == [], f"beat schedules unregistered tasks: {missing}"


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_workers_consume_every_routed_queue(compose: str) -> None:
    missing = sorted(_required_queues() - _compose_worker_queues(INFRA / compose))
    assert missing == [], f"{compose}: no worker consumes {missing}"


def test_k8s_worker_consumes_every_routed_queue() -> None:
    consumed = _k8s_worker_queues(INFRA / "k8s" / "worker-deployment.yaml")
    missing = sorted(_required_queues() - consumed)
    assert missing == [], f"k8s worker-deployment: no worker consumes {missing}"


def test_helm_worker_consumes_every_routed_queue() -> None:
    chart = INFRA / "helm" / "agentverse"
    template = (chart / "templates" / "app-workloads.yaml").read_text()
    assert "-Q\", {{ .Values.worker.queues | quote }}" in template
    for values_file in sorted(chart.glob("values*.yaml")):
        values = yaml.safe_load(values_file.read_text()) or {}
        queues = (values.get("worker") or {}).get("queues")
        if values_file.name != "values.yaml" and queues is None:
            continue  # inherits the base values.yaml list
        consumed = {q.strip() for q in str(queues or "").split(",") if q.strip()}
        missing = sorted(_required_queues() - consumed)
        assert missing == [], f"helm {values_file.name}: worker.queues lacks {missing}"
