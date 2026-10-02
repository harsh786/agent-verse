"""EVAL-GOLDEN and EVAL-GOLDEN-VERSIONING.

EVAL-GOLDEN: a versioned golden dataset (10 deterministic dispatch tasks, each with
checks) is run against an agent through the AI-Ops eval runner (every case is a
real goal). Asserts one result per task with its goal id and status, re-checks
every task's output independently against its checks, requires the platform's
gate decision to exist and agree with its own scores, and requires the dataset
version to be recorded on the run.

EVAL-GOLDEN-VERSIONING: editing a task must produce a new dataset version, and a
rerun must report against that version (with the edited task's new answer).
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from tests.real_world import goals
from tests.real_world.helpers import LiveAPI, env_float, mask, tag, wait_until
from tests.real_world.metrics import norm, record

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "eval_golden_v1.json")
EVAL_TIMEOUT = float(os.getenv("RW_EVAL_TIMEOUT", "1500"))
PASS_MIN = env_float("RW_EVAL_PASS_MIN", 0.7)


def load_golden() -> dict[str, Any]:
    with open(FIXTURE, encoding="utf-8") as fh:
        return dict(json.load(fh))


def check_output(task: dict[str, Any], actual: str) -> bool:
    """The task's own checks: every must_contain present (case-folded unless flagged)."""
    if task.get("case_sensitive"):
        return all(m in actual for m in task["must_contain"])
    return all(norm(m) in norm(actual) for m in task["must_contain"])


def _create_dataset(api: LiveAPI, golden: dict[str, Any]) -> str:
    body = api.json_ok("POST", "/ai-ops/datasets", json={
        "name": f"rw-{golden['name']}-{tag()}", "description": golden["description"],
        "golden_tasks": golden["golden_tasks"]})
    assert body.get("task_count") == len(golden["golden_tasks"]), body
    return str(body["dataset_id"])


def _run(api: LiveAPI, dataset_id: str, agent_id: str) -> dict[str, Any]:
    started = api.json_ok("POST", f"/ai-ops/datasets/{dataset_id}/run",
                          json={"agent_id": agent_id})
    rid = str(started["result_id"])
    return dict(wait_until(lambda: api.json_ok("GET", f"/ai-ops/eval-results/{rid}"),
                           timeout=EVAL_TIMEOUT, interval=10, desc=f"eval run {rid}",
                           done=lambda r: r.get("status") in ("completed", "failed",
                                                              "abandoned")))


def _dataset(api: LiveAPI, dataset_id: str) -> dict[str, Any]:
    listed = api.json_ok("GET", "/ai-ops/datasets").get("datasets") or []
    return next((d for d in listed if str(d.get("dataset_id")) == dataset_id), {})


def _cleanup_goals(api: LiveAPI, result: dict[str, Any]) -> None:
    for c in result.get("cases") or []:
        if c.get("goal_id"):
            api.post(f"/goals/{c['goal_id']}/cancel")


@pytest.fixture
def eval_agent(api: LiveAPI, cleanup: Any) -> str:
    return goals.create_agent(api, cleanup, "rw-dispatch-assistant",
                              system_prompt="Answer exactly as asked, with no extra words.",
                              max_iterations=4)


@pytest.mark.scenario("EVAL-GOLDEN")
def test_eval_golden_dataset(api: LiveAPI, eval_agent: str, evidence: dict[str, Any]) -> None:
    golden = load_golden()
    dataset_id = _create_dataset(api, golden)
    evidence["dataset_id"] = dataset_id
    result = _run(api, dataset_id, eval_agent)
    _cleanup_goals(api, result)
    cases = list(result.get("cases") or [])
    evidence.update(result_id=result.get("result_id"), status=result.get("status"),
                    gate_passed=result.get("passed"), avg_score=result.get("avg_score"),
                    scoring=result.get("scoring"))
    assert result.get("status") == "completed", f"eval run {result.get('status')}: " \
        f"{mask(result.get('error'))}"
    assert len(cases) == len(golden["golden_tasks"]), (
        f"{len(cases)} case results for {len(golden['golden_tasks'])} tasks"
    )
    per_task: dict[str, dict[str, Any]] = {}
    for c in cases:
        task = golden["golden_tasks"][int(c["index"])]
        per_task[task["id"]] = {"status": c.get("status"), "goal_id": c.get("goal_id"),
                                "platform_pass": c.get("passed"),
                                "own_check": check_output(task, str(c.get("actual") or "")),
                                "actual": str(c.get("actual") or "")[:80]}
    evidence["per_task"] = per_task
    missing_goal = [t for t, r in per_task.items() if not r["goal_id"]]
    assert not missing_goal, f"tasks executed without a goal: {missing_goal}"
    own_rate = sum(1 for r in per_task.values() if r["own_check"]) / len(per_task)
    agree = sum(1 for r in per_task.values() if bool(r["platform_pass"]) == r["own_check"])
    record(evidence, own_pass_rate=own_rate, platform_task_pass_rate=sum(
        1 for r in per_task.values() if r["platform_pass"]) / len(per_task),
        verdict_agreement=agree / len(per_task))
    soft: list[str] = []
    if not isinstance(result.get("passed"), bool):
        soft.append(f"no gate decision on the run (passed={result.get('passed')!r})")
    if own_rate < PASS_MIN:
        soft.append(f"only {own_rate:.0%} of golden tasks pass their checks (min {PASS_MIN:.0%})")
    disagreements = {t: r for t, r in per_task.items()
                     if bool(r["platform_pass"]) != r["own_check"]}
    if len(disagreements) > 2:
        soft.append(f"the platform's per-task verdicts disagree with the checks on "
                    f"{len(disagreements)} tasks: {sorted(disagreements)}")
    gate_should = own_rate >= PASS_MIN
    if isinstance(result.get("passed"), bool) and result["passed"] != gate_should and \
            not disagreements:
        soft.append(f"gate decision {result['passed']} contradicts the task results "
                    f"({own_rate:.0%} passing)")
    ds = _dataset(api, dataset_id)
    evidence["dataset_version"] = ds.get("version")
    if ds.get("version") != 1:
        soft.append(f"new dataset has version {ds.get('version')!r}, expected 1")
    recorded = result.get("dataset_version") or (result.get("dataset") or {}).get("version")
    evidence["run_dataset_version"] = recorded
    if recorded is None:
        soft.append("the eval run does not record which dataset version it ran")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("EVAL-GOLDEN-VERSIONING")
def test_eval_golden_edit_creates_version(api: LiveAPI, eval_agent: str,
                                          evidence: dict[str, Any]) -> None:
    golden = load_golden()
    dataset_id = _create_dataset(api, golden)
    evidence["dataset_id"] = dataset_id
    edit = golden["edit_for_v2"]
    tasks = [edit if t["id"] == edit["id"] else t for t in golden["golden_tasks"]]
    attempts = {}
    updated = None
    for method in ("PATCH", "PUT"):
        resp = api.request(method, f"/ai-ops/datasets/{dataset_id}",
                           json={"golden_tasks": tasks, "description": golden["description"]})
        attempts[method] = resp.status_code
        if resp.status_code in (200, 201):
            updated = resp.json()
            break
    evidence["edit_attempts"] = attempts
    assert updated is not None, (
        f"no API edits a golden task (PATCH/PUT /ai-ops/datasets/{{id}} -> {attempts}); a "
        "versioned dataset cannot be maintained"
    )
    ds = _dataset(api, dataset_id)
    evidence["version_after_edit"] = ds.get("version")
    assert ds.get("version") == 2, f"editing a task left the dataset at version " \
        f"{ds.get('version')!r}"
    result = _run(api, dataset_id, eval_agent)
    _cleanup_goals(api, result)
    recorded = result.get("dataset_version") or (result.get("dataset") or {}).get("version")
    evidence["run_dataset_version"] = recorded
    assert recorded == 2, f"rerun reports dataset version {recorded!r}, not 2"
    first = next((c for c in result.get("cases") or [] if int(c.get("index", -1)) == 0), {})
    evidence["edited_case"] = {"expected": first.get("expected"), "actual": str(
        first.get("actual"))[:60]}
    assert first.get("expected") == edit["expected_output"], "rerun used the old task text"
