"""Out-of-process Celery workers for e2e tests: always stopped, never leaked (USR-7).

Every e2e helper that starts a real ``celery ... worker`` goes through
:func:`worker_process` / :func:`start_worker_process` + :func:`stop_worker_process`:

* the worker runs in its own session / process group, recorded in a registry;
* stopping signals the **whole group** — whether or not the main process is
  still alive (a crashed main used to leave its prefork pool child running,
  holding Postgres, Redis and the log file open) — escalates SIGTERM → SIGKILL
  after a grace period, and waits until the group is empty;
* a failed start (worker died, never became ready) stops what it started before
  raising;
* :func:`surviving_worker_groups` / :func:`reap_surviving_workers` back the
  session-end check in ``tests/conftest.py``: a worker that outlives the session
  is killed and fails the run.

Worker node names carry a per-session token (:func:`node_name`), so the session
check also finds a worker started outside this registry.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

__all__ = [
    "SESSION_TOKEN",
    "WorkerHandle",
    "node_name",
    "reap_surviving_workers",
    "start_worker_process",
    "stop_worker_process",
    "surviving_worker_groups",
    "worker_process",
]

# Embedded in every worker's node name (``-n``): lets the session check find
# this session's workers in the process table even if one escaped the registry.
SESSION_TOKEN = f"avtest{os.getpid()}x{uuid.uuid4().hex[:8]}"

_DEFAULT_GRACE_SECONDS = 20.0
_KILL_WAIT_SECONDS = 10.0

_lock = threading.Lock()
_registry: dict[int, WorkerHandle] = {}


@dataclass
class WorkerHandle:
    proc: subprocess.Popen[bytes]
    pgid: int
    log_path: Path
    log_file: IO[str]
    name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The ``{"proc", "log_path"}`` shape the e2e tests already consume."""
        return {"proc": self.proc, "log_path": self.log_path, "handle": self, **self.extra}


def node_name(name: str) -> str:
    """A Celery ``-n`` node name tagged with this session's token."""
    return f"{name}-{SESSION_TOKEN}@%h"


def _grace_seconds(grace_seconds: float | None) -> float:
    if grace_seconds is not None:
        return grace_seconds
    try:
        return float(os.getenv("E2E_WORKER_STOP_GRACE_SECONDS", _DEFAULT_GRACE_SECONDS))
    except ValueError:
        return _DEFAULT_GRACE_SECONDS


def _group_alive(pgid: int) -> bool:
    """True while any non-zombie process of the group exists."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - a recycled pgid that is not ours
        return False
    # Zombies answer killpg(0) until reaped; they run nothing and hold no sockets.
    out = subprocess.run(
        ["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True, check=False
    )
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == str(pgid) and not parts[1].startswith("Z"):
            return True
    return False


def _wait_group_gone(pgid: int, proc: subprocess.Popen[bytes] | None, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if proc is not None:
            proc.poll()  # reap the main process so it does not linger as a zombie
        if not _group_alive(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def _terminate_group(
    pgid: int,
    proc: subprocess.Popen[bytes] | None,
    *,
    grace_seconds: float,
    kill: bool = False,
) -> bool:
    """SIGTERM the group, SIGKILL whatever is left after the grace (``kill``:
    SIGKILL at once, e.g. to simulate a crashed worker). True if it was alive."""
    was_alive = _group_alive(pgid)
    if was_alive:
        _signal_group(pgid, signal.SIGKILL if kill else signal.SIGTERM)
        if not _wait_group_gone(pgid, proc, _KILL_WAIT_SECONDS if kill else grace_seconds):
            _signal_group(pgid, signal.SIGKILL)
            if not _wait_group_gone(pgid, proc, _KILL_WAIT_SECONDS):
                raise RuntimeError(f"worker process group {pgid} survived SIGKILL")
    if proc is not None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_KILL_WAIT_SECONDS)
    return was_alive


def stop_worker_process(
    handle: WorkerHandle, *, grace_seconds: float | None = None, kill: bool = False
) -> None:
    """Stop the worker's whole process group (always — even if its main already exited).

    ``kill`` sends SIGKILL straight away (a test simulating a dead worker).
    Idempotent: stopping an already stopped worker is a no-op.
    """
    try:
        _terminate_group(
            handle.pgid, handle.proc, grace_seconds=_grace_seconds(grace_seconds), kill=kill
        )
    finally:
        with _lock:
            _registry.pop(handle.pgid, None)
        with contextlib.suppress(Exception):
            handle.log_file.close()


def start_worker_process(
    cmd: Sequence[str],
    *,
    cwd: Path | str,
    env: dict[str, str],
    log_path: Path,
    ready_marker: str = "ready.",
    ready_timeout: float = 300.0,
    name: str = "",
) -> WorkerHandle:
    """Start ``cmd`` in its own process group and wait for ``ready_marker`` in its log.

    On any failure to become ready the group is stopped before the error is raised.
    """
    log_file = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own session + process group: signal the whole tree
        )
    except BaseException:
        log_file.close()
        raise
    handle = WorkerHandle(proc=proc, pgid=proc.pid, log_path=log_path, log_file=log_file, name=name)
    with _lock:
        _registry[handle.pgid] = handle
    try:
        deadline = time.monotonic() + ready_timeout
        while True:
            with contextlib.suppress(OSError):
                if ready_marker in log_path.read_text():
                    return handle
            if proc.poll() is not None:
                raise RuntimeError(
                    f"worker {name or cmd[0]!r} exited during startup (rc={proc.returncode}).\n"
                    f"--- worker log tail ---\n{_tail(log_path)}"
                )
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"worker {name or cmd[0]!r} did not become ready in {ready_timeout:.0f}s.\n"
                    f"--- worker log tail ---\n{_tail(log_path)}"
                )
            time.sleep(0.25)
    except BaseException:
        stop_worker_process(handle, grace_seconds=5)
        raise


@contextlib.contextmanager
def worker_process(
    cmd: Sequence[str],
    *,
    cwd: Path | str,
    env: dict[str, str],
    log_path: Path,
    ready_marker: str = "ready.",
    ready_timeout: float = 300.0,
    name: str = "",
    grace_seconds: float | None = None,
) -> Iterator[WorkerHandle]:
    """:func:`start_worker_process` … :func:`stop_worker_process`, stopped on any exit."""
    handle = start_worker_process(
        cmd,
        cwd=cwd,
        env=env,
        log_path=log_path,
        ready_marker=ready_marker,
        ready_timeout=ready_timeout,
        name=name,
    )
    try:
        yield handle
    finally:
        stop_worker_process(handle, grace_seconds=grace_seconds)


def _tail(log_path: Path, size: int = 3000) -> str:
    try:
        return log_path.read_text()[-size:]
    except OSError:
        return ""


def _token_groups() -> set[int]:
    """Process groups of any live process whose command line carries the session token."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,pgid=,stat=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    groups: set[int] = set()
    for line in out.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and SESSION_TOKEN in parts[3] and not parts[2].startswith("Z"):
            with contextlib.suppress(ValueError):
                groups.add(int(parts[1]))
    groups.discard(os.getpgid(0))  # never this pytest process's own group
    return groups


def surviving_worker_groups() -> list[int]:
    """Process groups of this session's workers that are still running."""
    with _lock:
        registered = set(_registry)
    alive = {pgid for pgid in registered if _group_alive(pgid)}
    return sorted(alive | _token_groups())


def reap_surviving_workers(*, grace_seconds: float = 5.0) -> list[int]:
    """Kill every surviving worker group of this session; return the groups that were alive."""
    leaked = surviving_worker_groups()
    for pgid in leaked:
        with _lock:
            handle = _registry.pop(pgid, None)
        _terminate_group(pgid, handle.proc if handle else None, grace_seconds=grace_seconds)
        if handle is not None:
            with contextlib.suppress(Exception):
                handle.log_file.close()
    with _lock:  # registered groups that already exited on their own
        for pgid in [p for p in _registry if not _group_alive(p)]:
            handle = _registry.pop(pgid)
            with contextlib.suppress(Exception):
                handle.log_file.close()
    return leaked
