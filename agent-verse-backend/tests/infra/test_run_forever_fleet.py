"""scripts/run_forever.py must not start a second Celery fleet next to compose's.

The developer's launchd job ran run_forever.py (API + worker + beat from the
repo .venv) against the SAME Redis/Postgres as the Docker compose stack: two
worker fleets from possibly different code consumed the same queues and two
beats scheduled every periodic task twice. By default it now skips its own
worker/beat while the compose stack's are running (re-checked periodically),
and ``--force-workers`` / ``AGENTVERSE_FORCE_WORKERS=1`` overrides.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_forever.py"


@pytest.fixture(scope="module")
def rf() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("run_forever_under_test", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses resolve their module by name
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT.parent))


def _docker_ps(stdout: str, returncode: int = 0) -> Any:
    calls: list[list[str]] = []

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


# ── detection ────────────────────────────────────────────────────────────────


def test_detects_running_compose_worker_and_beat(rf: ModuleType) -> None:
    run = _docker_ps("backend\nworker\nbeat\nworkflow-worker\nredis\n")
    services = rf.compose_fleet_services(project="agentverse-backend", runner=run)
    assert services == {"backend", "worker", "beat", "workflow-worker"}
    cmd = run.calls[0]
    assert cmd[:2] == ["docker", "ps"]
    assert "label=com.docker.compose.project=agentverse-backend" in cmd


def test_docker_unavailable_means_no_compose_fleet(rf: ModuleType) -> None:
    def boom(cmd: list[str], **_: Any) -> Any:
        raise FileNotFoundError("docker")

    assert rf.compose_fleet_services(project="p", runner=boom) is None
    assert rf.compose_fleet_services(project="p", runner=_docker_ps("", returncode=1)) is None


# ── decision ─────────────────────────────────────────────────────────────────


def test_skips_own_worker_and_beat_when_compose_runs_them(rf: ModuleType) -> None:
    d = rf.decide_fleet(want_worker=True, want_beat=True, force=False, compose={"worker", "beat"})
    assert (d.run_worker, d.run_beat) == (False, False)
    assert "compose" in d.worker_reason and "compose" in d.beat_reason


def test_any_compose_worker_service_counts_as_a_worker_fleet(rf: ModuleType) -> None:
    d = rf.decide_fleet(want_worker=True, want_beat=True, force=False, compose={"subgoal-worker"})
    assert (d.run_worker, d.run_beat) == (False, True)


def test_runs_own_fleet_when_compose_is_not_running(rf: ModuleType) -> None:
    assert rf.decide_fleet(want_worker=True, want_beat=True, force=False, compose=set()).run_worker
    unknown = rf.decide_fleet(want_worker=True, want_beat=True, force=False, compose=None)
    assert (unknown.run_worker, unknown.run_beat) == (True, True)
    assert "could not" in unknown.worker_reason


def test_force_overrides_detection(rf: ModuleType) -> None:
    d = rf.decide_fleet(want_worker=True, want_beat=True, force=True, compose={"worker", "beat"})
    assert (d.run_worker, d.run_beat) == (True, True)


def test_no_worker_flag_still_wins(rf: ModuleType) -> None:
    d = rf.decide_fleet(want_worker=False, want_beat=True, force=True, compose=set())
    assert (d.run_worker, d.run_beat) == (False, True)


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["run"], {}, False),
        (["run", "--force-workers"], {}, True),
        (["run"], {"AGENTVERSE_FORCE_WORKERS": "1"}, True),
        (["run"], {"AGENTVERSE_FORCE_WORKERS": "true"}, True),
        (["run"], {"AGENTVERSE_FORCE_WORKERS": "0"}, False),
    ],
)
def test_force_flag_and_env(
    rf: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    env: dict[str, str],
    expected: bool,
) -> None:
    monkeypatch.delenv("AGENTVERSE_FORCE_WORKERS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    args = rf.build_parser().parse_args(argv)
    assert rf.force_workers(args) is expected


def test_force_flag_is_forwarded_to_background_runs(rf: ModuleType) -> None:
    args = rf.build_parser().parse_args(["start", "--force-workers"])
    assert "--force-workers" in rf._forwarded_run_args(args)


# ── periodic gate ────────────────────────────────────────────────────────────


def test_gate_rechecks_and_logs_only_on_change(rf: ModuleType) -> None:
    states = [{"worker"}, {"worker"}, set()]
    logs: list[str] = []
    clock = [0.0]

    probe = rf.ComposeFleetProbe(
        project="agentverse-backend",
        ttl=10.0,
        detect=lambda: states.pop(0),
        clock=lambda: clock[0],
    )
    gate = rf.FleetGate(probe, role="worker", want=True, force=False, log=logs.append)

    assert gate.allowed() is False
    assert gate.allowed() is False  # cached within ttl: no re-detect, no new log
    assert len(states) == 2 and len(logs) == 1
    clock[0] = 11.0
    assert gate.allowed() is False  # re-detected, unchanged: still one log
    assert len(logs) == 1
    clock[0] = 22.0
    assert gate.allowed() is True  # compose worker gone: start ours, log it
    assert len(logs) == 2


def test_local_api_stands_down_while_the_compose_backend_serves_8000(rf: ModuleType) -> None:
    states = [{"backend", "worker"}, set()]
    logs: list[str] = []
    clock = [0.0]
    probe = rf.ComposeFleetProbe(
        project="agentverse-backend", ttl=10.0, detect=lambda: states.pop(0),
        clock=lambda: clock[0],
    )
    gate = rf.FleetGate(probe, role="api", want=True, force=False, log=logs.append)
    assert gate.allowed() is False
    assert "compose backend is running" in logs[-1]
    clock[0] = 11.0
    assert gate.allowed() is True  # compose backend gone: run the local API
    assert len(logs) == 2


def test_local_api_runs_when_forced_or_docker_unknown(rf: ModuleType) -> None:
    forced = rf.FleetGate(
        rf.ComposeFleetProbe(project="p", detect=lambda: {"backend"}),
        role="api", want=True, force=True, log=lambda _m: None,
    )
    assert forced.allowed() is True
    unknown = rf.FleetGate(
        rf.ComposeFleetProbe(project="p", detect=lambda: None),
        role="api", want=True, force=False, log=lambda _m: None,
    )
    assert unknown.allowed() is True
