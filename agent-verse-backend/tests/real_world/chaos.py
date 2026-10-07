"""Fault injection against REAL containers of the live stack (CHAOS-* scenarios).

Every fault is a real Docker action on a named container — ``stop`` / ``kill`` /
``pause`` / ``restart`` — never a fake dependency. Each one is undone in a
``finally`` (``docker start`` / ``docker unpause``) by :func:`fault`, and the
container is waited back to running (and healthy, when it has a healthcheck)
before the scenario asserts anything. A process-wide ledger undoes anything still
injected at interpreter exit as a last resort.

Opt-in only: ``RW_CHAOS=1`` plus the container-name variable each scenario needs
(:func:`require_container`). Container names are never guessed for destructive
actions — an unset variable SKIPS the scenario with the variable's name.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx

from tests.real_world.helpers import BASE_URL, mask

CONTAINER_VARS = ("RW_WORKER_CONTAINER", "RW_WORKFLOW_WORKER_CONTAINER",
                  "RW_SCHEDULE_WORKER_CONTAINER", "RW_BACKEND_CONTAINER", "RW_REDIS_CONTAINER",
                  "RW_PG_CONTAINER", "RW_PGBOUNCER_CONTAINER", "RW_MONGO_CONTAINER")
UNDO = {"stop": "start", "kill": "start", "pause": "unpause", "restart": None}

_LEDGER: dict[str, str] = {}  # container -> undo action still owed
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.getenv("RW_CHAOS") == "1"


def require_chaos() -> None:
    import pytest

    if not enabled():
        pytest.skip("opt-in: set RW_CHAOS=1 (injects real faults: stops / pauses containers "
                    "of the live stack, each undone in a finally)")


def require_container(var: str) -> str:
    """The container named by ``var`` (must exist), else SKIP naming the variable."""
    import pytest

    require_chaos()
    name = os.getenv(var, "").strip()
    if not name:
        pytest.skip(f"needs {var}: the exact container name to inject the fault into "
                    "(never guessed for a destructive action)")
    state = container_state(name)
    if state.get("error"):
        pytest.skip(f"{var}={name}: {state['error']}")
    return name


def docker(*args: str, timeout: float = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, mask((proc.stdout + proc.stderr).strip())


def container_state(name: str) -> dict[str, Any]:
    rc, out = docker("inspect", "--format", "{{json .State}}", name, timeout=30)
    if rc != 0:
        return {"error": f"docker inspect failed: {out[:200]}"}
    try:
        state = json.loads(out)
    except ValueError:
        return {"error": f"unreadable docker state: {out[:200]}"}
    health = (state.get("Health") or {}).get("Status")
    return {"status": state.get("Status"), "running": bool(state.get("Running")),
            "paused": bool(state.get("Paused")), "health": health,
            "restarts": state.get("RestartCount"), "started_at": state.get("StartedAt")}


def wait_container(name: str, timeout: float = 240) -> dict[str, Any]:
    """Wait until ``name`` runs (not paused) and, with a healthcheck, is healthy."""
    deadline = time.monotonic() + timeout
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = container_state(name)
        if state.get("running") and not state.get("paused") and state.get("health") in (
                None, "healthy"):
            return state
        time.sleep(2)
    raise AssertionError(f"container {name} not back within {timeout:.0f}s: {state}")


def wait_api(timeout: float = 300, path: str = "/health") -> float:
    """Seconds until the API answers 200 on ``path`` again (no key needed)."""
    started = time.monotonic()
    deadline = started + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}{path}", timeout=5)
            if r.status_code == 200:
                return round(time.monotonic() - started, 1)
            last = f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(2)
    raise AssertionError(f"API {path} not healthy within {timeout:.0f}s (last: {last})")


def _owe(name: str, undo: str | None) -> None:
    with _LOCK:
        if undo:
            _LEDGER[name] = undo
        else:
            _LEDGER.pop(name, None)


def undo_all() -> list[str]:
    """Undo every fault still owed (start / unpause); returns what was done."""
    done = []
    with _LOCK:
        owed = dict(_LEDGER)
    for name, action in owed.items():
        rc, out = docker(action, name, timeout=120)
        if action == "unpause" and rc != 0:
            docker("start", name, timeout=120)
        done.append(f"{action} {name}: rc={rc}")
        _owe(name, None)
    return done


atexit.register(undo_all)


def inject(action: str, name: str) -> dict[str, Any]:
    """Inject one fault now; the undo is owed until :func:`recover` runs."""
    if action not in UNDO:
        raise ValueError(f"unknown fault {action}")
    _owe(name, UNDO[action])
    started = time.monotonic()
    args = ["kill", "--signal", "KILL", name] if action == "kill" else [action, name]
    rc, out = docker(*args, timeout=180)
    return {"action": action, "container": name, "rc": rc, "out": out[:200],
            "s": round(time.monotonic() - started, 1), "at": time.time()}


def recover(name: str, timeout: float = 240) -> dict[str, Any]:
    """Undo the owed fault on ``name`` and wait for it to run / be healthy."""
    with _LOCK:
        action = _LEDGER.get(name)
    out = ""
    if action:
        rc, out = docker(action, name, timeout=180)
        if action == "unpause" and rc != 0:  # paused then stopped by someone: start it
            docker("start", name, timeout=180)
        _owe(name, None)
    state = container_state(name)
    if not state.get("running"):
        docker("start", name, timeout=180)
    return {"undo": action, "out": out[:200], "state": wait_container(name, timeout)}


@contextlib.contextmanager
def fault(action: str, name: str, *, hold_s: float = 0.0,
          evidence: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """``with fault("pause", redis, hold_s=12):`` — inject, hold, ALWAYS undo + wait."""
    record = inject(action, name)
    if evidence is not None:
        evidence.setdefault("faults", []).append(record)
    try:
        if hold_s:
            time.sleep(hold_s)
        yield record
    finally:
        rec = recover(name)
        record["recovered"] = rec
        record["outage_s"] = round(time.time() - record["at"], 1)


def probe_during(fn: Any, seconds: float, interval: float = 1.0) -> list[dict[str, Any]]:
    """Call ``fn()`` repeatedly for ``seconds`` (each result timestamped)."""
    out: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            res = fn()
        except Exception as exc:  # a transport error IS an observation here
            res = {"error": type(exc).__name__}
        out.append({"t": round(time.monotonic() - started, 2), **(
            res if isinstance(res, dict) else {"value": res})})
        time.sleep(interval)
    return out


def classify_outage_answers(codes: list[Any]) -> dict[str, int]:
    """Tally answers seen during an outage: ``503``, ``2xx``, ``500``, ``other``, ``transport``."""
    tally = {"503": 0, "2xx": 0, "500": 0, "other": 0, "transport": 0}
    for c in codes:
        if not isinstance(c, int):
            tally["transport"] += 1
        elif c == 503:
            tally["503"] += 1
        elif 200 <= c < 300:
            tally["2xx"] += 1
        elif c == 500:
            tally["500"] += 1
        else:
            tally["other"] += 1
    return tally
