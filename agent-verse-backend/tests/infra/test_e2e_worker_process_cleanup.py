"""USR-7: e2e Celery workers are always stopped, and none outlives the session.

The e2e_full worker fixtures (``tests/e2e_full/_wf_worker.workflow_worker`` and
the per-test copies) stopped a worker only ``if proc.poll() is None``: when the
worker's main process had already exited — crashed, or killed by the test — its
prefork pool child kept running in the process group, holding the database,
Redis and the log file open, and blocked cleanup. Nothing checked that a worker
was gone afterwards.

A stand-in "worker" reproduces it without Celery: it prints ``ready.``, starts a
pool child that ignores SIGTERM in its own process group, and (``crash`` mode)
exits its main process.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

_REAL_POPEN = subprocess.Popen

_FAKE_WORKER = textwrap.dedent(
    """
    import subprocess, sys, time
    child = subprocess.Popen([sys.executable, "-c",
        "import signal, time\\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
        "time.sleep(600)\\n"])
    print(f"pool-child {child.pid}", flush=True)
    print("fake@host ready.", flush=True)
    if sys.argv[1] == "crash":
        time.sleep(0.5)
        sys.exit(3)  # the main process dies; its pool child is left running
    time.sleep(600)
    """
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - not ours
        return True
    # A zombie still answers kill(0); it holds no resources and is not running.
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(out.stdout.strip()) and not out.stdout.strip().startswith("Z")


def _child_pid(log_path: Path) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for line in log_path.read_text().splitlines():
            if line.startswith("pool-child "):
                return int(line.split()[1])
        time.sleep(0.1)
    raise AssertionError("fake worker never reported its pool child")


def _fake_popen(script: Path, mode: str) -> Any:
    def _popen(cmd: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, list) and "celery" in cmd:
            return _REAL_POPEN([sys.executable, str(script), mode], **kwargs)
        return _REAL_POPEN(cmd, **kwargs)

    return _popen


@pytest.fixture
def fake_worker_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_worker.py"
    script.write_text(_FAKE_WORKER)
    return script


@pytest.fixture
def _cleanup_pids() -> Any:
    pids: list[int] = []
    yield pids
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_workflow_worker_stops_the_pool_child_after_the_main_process_died(
    tmp_path: Path, fake_worker_script: Path, _cleanup_pids: list[int]
) -> None:
    from tests.e2e_full._wf_worker import workflow_worker

    with (
        patch("subprocess.Popen", side_effect=_fake_popen(fake_worker_script, "crash")),
        patch.dict(os.environ, {"E2E_WORKER_STOP_GRACE_SECONDS": "1"}),
    ):
        with workflow_worker(tmp_path, name="usr7") as worker:
            child = _child_pid(worker["log_path"])
            _cleanup_pids.append(child)
            worker["proc"].wait(timeout=10)  # main process gone, child still running
            assert _alive(child)
    assert not _alive(child), "the worker's pool child outlived the fixture"


def test_workflow_worker_stops_a_child_that_ignores_sigterm_when_the_test_fails(
    tmp_path: Path, fake_worker_script: Path, _cleanup_pids: list[int]
) -> None:
    from tests.e2e_full._wf_worker import workflow_worker

    with (
        patch("subprocess.Popen", side_effect=_fake_popen(fake_worker_script, "run")),
        patch.dict(os.environ, {"E2E_WORKER_STOP_GRACE_SECONDS": "1"}),
        pytest.raises(RuntimeError, match="test body failed"),
        workflow_worker(tmp_path, name="usr7b") as worker,
    ):
        child = _child_pid(worker["log_path"])
        _cleanup_pids.append(child)
        raise RuntimeError("test body failed")
    assert not _alive(child)
    assert not _alive(worker["proc"].pid)


def test_no_registered_worker_survives_and_leaks_are_reported(
    tmp_path: Path, fake_worker_script: Path, _cleanup_pids: list[int]
) -> None:
    from tests import _worker_procs

    handle = _worker_procs.start_worker_process(
        [sys.executable, str(fake_worker_script), "run"],
        cwd=tmp_path,
        env=dict(os.environ),
        log_path=tmp_path / "leak.log",
        ready_timeout=30,
    )
    child = _child_pid(handle.log_path)
    _cleanup_pids.extend([child, handle.proc.pid])
    # A fixture that forgot to stop it: the session check sees the group alive...
    assert handle.pgid in _worker_procs.surviving_worker_groups()
    # ...and the session-end reaper kills the whole group and reports it.
    leaked = _worker_procs.reap_surviving_workers(grace_seconds=1)
    assert handle.pgid in leaked
    assert not _alive(child) and not _alive(handle.proc.pid)
    assert _worker_procs.surviving_worker_groups() == []
