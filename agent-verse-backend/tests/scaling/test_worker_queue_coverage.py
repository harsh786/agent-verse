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

from app.scaling.celery_app import PLAN_QUEUE_MAP, SUBGOAL_QUEUE_MAP, celery_app

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
    # Supervisor sub-goals: CeleryGoalTaskQueue(subgoal=True) → SUBGOAL_QUEUE_MAP.
    queues |= set(SUBGOAL_QUEUE_MAP.values())
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
    base = yaml.safe_load((chart / "values.yaml").read_text()) or {}
    for values_file in sorted(chart.glob("values*.yaml")):
        values = yaml.safe_load(values_file.read_text()) or {}
        queues = (values.get("worker") or {}).get("queues")
        sub_queues = (values.get("subgoalWorker") or {}).get("queues")
        if values_file.name != "values.yaml" and queues is None and sub_queues is None:
            continue  # inherits the base values.yaml lists
        queues = queues if queues is not None else base["worker"]["queues"]
        if sub_queues is None:
            sub_queues = base["subgoalWorker"]["queues"]
        consumed = {
            q.strip() for q in f"{queues},{sub_queues}".split(",") if q.strip()
        }
        missing = sorted(_required_queues() - consumed)
        assert missing == [], f"helm {values_file.name}: worker.queues lacks {missing}"


# ── CORE-09: supervisor sub-goals have their own worker pool ──────────────────
# A worker-run supervisor parent holds its slot while it waits for its sub-goals.
# If the pool that runs parents also consumed the sub-goal queues, a pool whose
# slots are all held by waiting parents would never run their children.

_SUBGOAL_QUEUES = set(SUBGOAL_QUEUE_MAP.values())
_MAIN_GOAL_QUEUES = set(PLAN_QUEUE_MAP.values())


def _assert_separate_pools(pools: list[set[str]], where: str) -> None:
    main = [q for q in pools if q & _MAIN_GOAL_QUEUES]
    sub = [q for q in pools if q & _SUBGOAL_QUEUES]
    assert main, f"{where}: no worker consumes the main goal queues"
    for queues in main:
        leaked = sorted(queues & _SUBGOAL_QUEUES)
        assert leaked == [], f"{where}: the main goal pool also consumes {leaked}"
    assert sub, f"{where}: no dedicated worker consumes the sub-goal queues"
    for queues in sub:
        missing = sorted(_SUBGOAL_QUEUES - queues)
        assert missing == [], f"{where}: sub-goal pool lacks {missing}"


def _compose_pools(path: Path) -> list[set[str]]:
    doc = yaml.safe_load(path.read_text())
    pools = []
    for svc in (doc.get("services") or {}).values():
        argv = _argv(svc.get("command"))
        if "celery" in argv and "worker" in argv:
            pools.append(_queues_from_argv(argv))
    return pools


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_runs_subgoals_on_a_dedicated_pool(compose: str) -> None:
    _assert_separate_pools(_compose_pools(INFRA / compose), compose)


def test_dev_compose_subgoal_worker_mirrors_the_goal_worker() -> None:
    doc = yaml.safe_load((INFRA / "docker-compose.yml").read_text())
    worker, sub = doc["services"]["worker"], doc["services"]["subgoal-worker"]
    assert sub["build"] == worker["build"]
    # Code steps run in sandbox containers on the host daemon, like the goal worker.
    assert "/var/run/docker.sock:/var/run/docker.sock" in sub["volumes"]
    assert sub["group_add"] == worker["group_add"]


def test_k8s_runs_subgoals_on_a_dedicated_pool() -> None:
    pools = []
    for doc in yaml.safe_load_all((INFRA / "k8s" / "worker-deployment.yaml").read_text()):
        if not doc:
            continue
        for c in doc["spec"]["template"]["spec"]["containers"]:
            pools.append(_queues_from_argv(_argv(c.get("command")) + _argv(c.get("args"))))
    _assert_separate_pools(pools, "k8s worker-deployment")


def test_maintained_helm_chart_runs_subgoals_on_a_dedicated_pool() -> None:
    chart = INFRA / "helm" / "agentverse"
    template = (chart / "templates" / "app-workloads.yaml").read_text()
    assert "{{- if .Values.subgoalWorker.enabled }}" in template
    assert "-Q\", {{ .Values.subgoalWorker.queues | quote }}" in template
    values = yaml.safe_load((chart / "values.yaml").read_text())
    pools = [
        {q.strip() for q in str(values[k]["queues"]).split(",") if q.strip()}
        for k in ("worker", "subgoalWorker")
    ]
    _assert_separate_pools(pools, "helm infra/helm/agentverse")
    for values_file in sorted(chart.glob("values-*.yaml")):
        overlay = yaml.safe_load(values_file.read_text()) or {}
        worker_on = (overlay.get("worker") or {}).get("enabled", True)
        sub_on = (overlay.get("subgoalWorker") or {}).get("enabled", True)
        assert worker_on == sub_on, f"{values_file.name}: sub-goal pool must follow the worker"
        repo = ((overlay.get("worker") or {}).get("image") or {}).get("repository")
        if repo:
            sub_repo = ((overlay.get("subgoalWorker") or {}).get("image") or {}).get("repository")
            assert sub_repo == repo, f"{values_file.name}: sub-goal worker image differs"


def test_legacy_helm_chart_runs_subgoals_on_a_dedicated_pool() -> None:
    chart = Path(__file__).resolve().parents[2] / "helm" / "agentverse"
    values = yaml.safe_load((chart / "values.yaml").read_text())
    pools = [
        {q.strip() for q in str(values[k]["queues"]).split(",") if q.strip()}
        for k in ("worker", "subgoalWorker")
    ]
    _assert_separate_pools(pools, "helm helm/agentverse")
    template = (chart / "templates" / "subgoal-worker-deployment.yaml").read_text()
    assert "{{ .Values.subgoalWorker.queues }}" in template
