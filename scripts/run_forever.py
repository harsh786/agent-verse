#!/usr/bin/env python3
"""run_forever.py - keep the AgentVerse system awake and running, forever.

This is a small supervisor daemon. It does two things for as long as it lives:

  1. Holds macOS power assertions so the machine never idle-sleeps out from
     under the service (reuses the IOKit / caffeinate logic in keep_awake.py).
  2. Runs the AgentVerse runtime and restarts each part every time it exits -
     crash, OOM, kill, clean exit, anything - with capped exponential backoff so
     a process that dies instantly doesn't spin the CPU.

By default it supervises the FULL runtime, each part independently:

    api    : uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    worker : uv run celery -A app.scaling.celery_app:celery_app worker -Q <all queues>
    beat   : uv run celery -A app.scaling.celery_app:celery_app beat

The worker is not optional in practice: the API only *enqueues* goals/workflows;
without a worker draining the Celery queues every submitted goal sticks in
PLANNING forever, and without beat nothing time-based (schedules, HITL expiry,
maintenance) ever fires. All three launch from agent-verse-backend/.

One fleet only: this talks to the same Redis/Postgres as the docker compose stack
(project ``agentverse-backend``, override with AGENTVERSE_COMPOSE_PROJECT). While
that stack's worker/beat containers are running, the local worker/beat stand by
(checked via ``docker ps`` every 60 s; ours stop if compose's come up later), so
two fleets never consume the same queues and beat never double-schedules.
``--force-workers`` or AGENTVERSE_FORCE_WORKERS=1 overrides.

    ./run_forever.py                       # foreground: awake + api+worker+beat, forever
    ./run_forever.py --no-beat             # api + worker only (no periodic tasks)
    ./run_forever.py --no-worker           # api only (goals will NOT execute)
    ./run_forever.py --force-workers       # run worker/beat even if compose runs them
    ./run_forever.py -- uv run celery ...  # override: supervise exactly this one cmd
    ./run_forever.py --port 9000           # backend on a different port
    ./run_forever.py start                 # background (detached), logs to a file
    ./run_forever.py --log-file F          # foreground, rotating log file F
    ./run_forever.py status                # is it up? what's the supervisor pid?
    ./run_forever.py stop                  # stop supervisor + children, release awake
    ./run_forever.py restart               # stop then start
    ./run_forever.py install               # LaunchAgent: start at login, survive logout
    ./run_forever.py uninstall             # remove the LaunchAgent

Logs: ``start`` and ``install`` write the supervisor's own messages AND every
child's stdout/stderr (piped through the supervisor, one ``[api]``/``[worker]``/
``[beat]`` prefixed line each) to ``run_forever.log``, rotated by size: 50 MB x 5
backups by default (``--log-max-mb`` / ``--log-backups``, env
AGENTVERSE_RUN_FOREVER_LOG_MAX_MB / AGENTVERSE_RUN_FOREVER_LOG_BACKUPS). The
children used to inherit launchd's StandardOutPath and the file grew forever
(2.5 GB). An older plist that still points launchd at run_forever.log is detected
at start-up and handled the same way; a pre-existing log larger than the limit is
set aside once as ``run_forever.log.<timestamp>.old`` (never deleted).

"Forever" means: while this process runs it will not let the service stay down
and will not let the Mac idle-sleep. `install` extends that across logins by
handing supervision to launchd (which also restarts *this* script if it dies).
Nothing needs sudo; stopping the supervisor releases the machine back to normal.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the awake backend (IOKit assertions + caffeinate fallback) from the
# sibling script rather than reimplementing it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import keep_awake
from keep_awake import ASSERT_DISPLAY, ASSERT_SYSTEM, make_backend

_console_log = keep_awake.log  # plain stdout; ``log`` below may route to a file

APP_NAME = "agentverse_run_forever"
LABEL = "com.local.agentverse.runforever"
STATE_DIR = Path.home() / ".local" / "state" / APP_NAME
PID_FILE = STATE_DIR / "supervisor.pid"
LOG_FILE = STATE_DIR / "run_forever.log"
# Where a detached / launchd supervisor's own stdout+stderr go: only what bypasses
# the rotating log (a crash before it is set up, an interpreter traceback).
STDIO_LOG = STATE_DIR / "run_forever.stdio.log"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "agent-verse-backend"

# Backoff bounds for the restart loop.
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
# If the child stays up at least this long, the restart is treated as "healthy"
# and backoff resets - so a stable service that gets killed once recovers fast.
HEALTHY_UPTIME = 30.0


# --------------------------------------------------------------------------- #
# logging: console by default, a size-rotated file for start / install
# --------------------------------------------------------------------------- #
LOG_MAX_MB_ENV = "AGENTVERSE_RUN_FOREVER_LOG_MAX_MB"
LOG_BACKUPS_ENV = "AGENTVERSE_RUN_FOREVER_LOG_BACKUPS"
DEFAULT_LOG_MAX_MB = 50.0
DEFAULT_LOG_BACKUPS = 5
# A child line longer than this is split, so a child writing without newlines
# cannot grow the supervisor's memory.
_MAX_LINE_BYTES = 64 * 1024
_LOGGER = logging.getLogger("agentverse.run_forever")
_LOGGER.propagate = False


@dataclass(frozen=True)
class LogRotation:
    max_bytes: int
    backups: int


def log_rotation_settings(
    args: argparse.Namespace, environ: dict[str, str] | None = None
) -> LogRotation:
    """Rotation limits: CLI flag, else env, else 50 MB x 5.

    backups is at least 1: RotatingFileHandler with backupCount=0 never rotates.
    """
    env = os.environ if environ is None else environ

    def pick(flag: object, name: str, default: float) -> float:
        if flag is not None:
            return float(flag)  # type: ignore[arg-type]
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            raise SystemExit(f"{name} must be a number, got {raw!r}") from None

    max_mb = pick(getattr(args, "log_max_mb", None), LOG_MAX_MB_ENV, DEFAULT_LOG_MAX_MB)
    backups = pick(getattr(args, "log_backups", None), LOG_BACKUPS_ENV, DEFAULT_LOG_BACKUPS)
    if max_mb <= 0:
        raise SystemExit(f"log max size must be > 0 MB, got {max_mb}")
    if backups < 1 or backups != int(backups):
        raise SystemExit(f"log backups must be a whole number >= 1, got {backups}")
    return LogRotation(max_bytes=int(max_mb * 1024 * 1024), backups=int(backups))


def log(message: str) -> None:
    """The supervisor's log line: the rotating file once configured, else stdout."""
    if _LOGGER.handlers:
        _LOGGER.info(message)
    else:
        _console_log(message)


def _same_file(fd: int, path: Path) -> bool:
    try:
        a, b = os.fstat(fd), path.stat()
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def configure_log_file(
    path: Path, rotation: LogRotation, *, stdio_path: Path | None = None
) -> logging.handlers.RotatingFileHandler:
    """Send every supervisor line (and, via ``log``, every child line) to *path*,
    rotated at ``rotation.max_bytes`` keeping ``rotation.backups`` old files.

    * A pre-existing file already over the limit (the unrotated legacy log) is
      renamed once to ``<name>.<timestamp>.old`` so rotation never deletes it.
    * When our own stdout/stderr IS that file (an older launchd plist points
      StandardOutPath at it), they are re-pointed at *stdio_path*: launchd's
      descriptor would otherwise keep appending to whichever file it opened,
      outside the rotation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stdio_hijacked = [fd for fd in (1, 2) if _same_file(fd, path)]
    with contextlib.suppress(FileNotFoundError):
        if path.stat().st_size > rotation.max_bytes:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            archived = path.with_name(f"{path.name}.{stamp}.old")
            path.rename(archived)
            _console_log(f"set aside the oversized log as {archived} (not deleted)")
    if stdio_hijacked:
        target = stdio_path or path.with_name(f"{path.stem}.stdio.log")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            for std in stdio_hijacked:
                (sys.stdout if std == 1 else sys.stderr).flush()
                os.dup2(fd, std)
        finally:
            os.close(fd)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=rotation.max_bytes, backupCount=rotation.backups, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    for old in list(_LOGGER.handlers):
        _LOGGER.removeHandler(old)
        old.close()
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    # keep_awake's helpers (power assertions, caffeinate) log through it too.
    keep_awake.log = log  # type: ignore[assignment]
    return handler


def capturing_child_output() -> bool:
    """Children are piped through the supervisor only when it logs to a file."""
    return bool(_LOGGER.handlers)


def _pump_output(name: str, stream: object) -> None:
    """Copy one child's combined stdout/stderr into the log, line by line."""
    readline = stream.readline  # type: ignore[attr-defined]
    try:
        while True:
            raw = readline(_MAX_LINE_BYTES)
            if not raw:
                break
            log(f"[{name}] {raw.decode('utf-8', 'replace').rstrip()}")
    except (OSError, ValueError):
        pass  # the pipe closed under us (shutdown)
    finally:
        with contextlib.suppress(OSError):
            stream.close()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# process helpers (mirrors keep_awake.py so the two scripts behave the same)
# --------------------------------------------------------------------------- #
def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def read_pid_file() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if not pid_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return None
    return pid


def write_pid_file() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()}\n")


def default_command(args: argparse.Namespace) -> list[str]:
    return [
        "uv",
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]


# All Celery queues the worker must drain for the platform to actually *do* work.
# The API enqueues goals/workflows here; without a worker consuming them every
# submitted goal sticks in PLANNING forever (the queue just grows in Redis).
_CELERY_QUEUES = (
    "goals,goals.free,goals.starter,goals.professional,goals.enterprise,goals_dlq,"
    "schedules,triggers.poll,maintenance,"
    "workflows.run,workflows.free,workflows.starter,workflows.professional,"
    "workflows.enterprise,workflows.maintenance"
)


def worker_command() -> list[str]:
    """Celery worker draining every goal/workflow/schedule/maintenance queue."""
    return [
        "uv", "run", "celery", "-A", "app.scaling.celery_app:celery_app", "worker",
        "-Q", _CELERY_QUEUES,
        "--concurrency", "2", "--loglevel", "info", "-n", "worker@%h",
    ]


def beat_command() -> list[str]:
    """Celery beat — fires the periodic schedule (due triggers, HITL expiry,
    maintenance, memory consolidation). Without it, nothing time-based runs."""
    return [
        "uv", "run", "celery", "-A", "app.scaling.celery_app:celery_app", "beat",
        "--loglevel", "info",
    ]


# --------------------------------------------------------------------------- #
# one Celery fleet only: defer to the docker compose stack's worker/beat
# --------------------------------------------------------------------------- #
# This supervisor talks to the SAME Redis/Postgres as the docker compose stack.
# If both run a worker, two fleets (possibly from different code) consume the
# same queues; if both run beat, every periodic task is scheduled twice. So by
# default the local worker/beat only run while compose's are NOT running.
COMPOSE_PROJECT_DEFAULT = "agentverse-backend"
FORCE_WORKERS_ENV = "AGENTVERSE_FORCE_WORKERS"
COMPOSE_PROJECT_ENV = "AGENTVERSE_COMPOSE_PROJECT"
FLEET_RECHECK_SECONDS = 60.0


def _docker_binary() -> str:
    """The docker CLI, also under launchd: its PATH is only /usr/bin:/bin:..., so a
    bare ``docker`` fails with "could not query docker" and both fleets ran."""
    import shutil

    found = shutil.which("docker")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/docker", "/usr/local/bin/docker"):
        if os.path.exists(candidate):
            return candidate
    return "docker"


def compose_fleet_services(
    project: str = COMPOSE_PROJECT_DEFAULT, runner=subprocess.run
) -> set[str] | None:
    """Running compose worker/beat service names, or None when docker can't say.

    Any service named ``worker``/``beat`` or ending in ``-worker`` (workflow-,
    subgoal-worker) counts - each of those consumes this platform's queues.
    """
    cmd = [
        _docker_binary(), "ps",
        "--filter", f"label=com.docker.compose.project={project}",
        "--format", '{{.Label "com.docker.compose.service"}}',
    ]
    try:
        # Generous: right after boot or under load `docker ps` can take >10 s, and a
        # timeout reads as "unknown" (which starts the local fleet).
        result = runner(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"docker probe failed: {type(exc).__name__}: {str(exc)[:200]}")
        return None
    if result.returncode != 0:
        log(f"docker probe failed (rc={result.returncode}): {(result.stderr or '')[:200]}")
        return None
    names = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
    # ``backend`` too: the compose API publishes :8000, the same port as the local API.
    return {n for n in names if n in ("worker", "beat", "backend") or n.endswith("-worker")}


@dataclass(frozen=True)
class FleetDecision:
    run_worker: bool
    run_beat: bool
    worker_reason: str
    beat_reason: str


def decide_fleet(
    *, want_worker: bool, want_beat: bool, force: bool, compose: set[str] | None
) -> FleetDecision:
    """Whether to run the local worker / beat. ``--no-worker``/``--no-beat`` win."""

    def one(want: bool, compose_has: bool, role: str) -> tuple[bool, str]:
        if not want:
            return False, f"{role} disabled by --no-{role}"
        if force:
            return True, f"{role} forced (--force-workers / {FORCE_WORKERS_ENV})"
        if compose is None:
            return True, f"could not query docker; running the local {role}"
        if compose_has:
            return False, (
                f"docker compose {role} is running ({', '.join(sorted(compose))}); "
                f"not starting a second {role} against the same Redis/Postgres "
                f"(override: --force-workers or {FORCE_WORKERS_ENV}=1)"
            )
        return True, f"no docker compose {role} running; running the local {role}"

    compose_set = compose or set()
    has_worker = any(n == "worker" or n.endswith("-worker") for n in compose_set)
    run_worker, worker_reason = one(want_worker, has_worker, "worker")
    run_beat, beat_reason = one(want_beat, "beat" in compose_set, "beat")
    return FleetDecision(run_worker, run_beat, worker_reason, beat_reason)


def force_workers(args: argparse.Namespace) -> bool:
    if getattr(args, "force_workers", False):
        return True
    return os.environ.get(FORCE_WORKERS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class ComposeFleetProbe:
    """Thread-safe, TTL-cached ``compose_fleet_services`` shared by the gates."""

    def __init__(self, project, ttl=FLEET_RECHECK_SECONDS, detect=None, clock=None):
        self._detect = detect or (lambda: compose_fleet_services(project))
        self._clock = clock or time.monotonic
        self._ttl = ttl
        self._lock = threading.Lock()
        self._at: float | None = None
        self._value: set[str] | None = None

    def services(self) -> set[str] | None:
        with self._lock:
            now = self._clock()
            if self._at is None or now - self._at >= self._ttl:
                self._value = self._detect()
                self._at = now
            return self._value


class FleetGate:
    """Per-role (worker/beat) decision, re-evaluated on every call; logs changes."""

    def __init__(self, probe, *, role, want, force, log=log):
        self._probe = probe
        self._role = role
        self._want = want
        self._force = force
        self._log = log
        self._last: bool | None = None

    def allowed(self) -> bool:
        if self._role == "api":
            return self._api_allowed()
        decision = decide_fleet(
            want_worker=self._want,
            want_beat=self._want,
            force=self._force,
            compose=self._probe.services(),
        )
        if self._role == "worker":
            ok, reason = decision.run_worker, decision.worker_reason
        else:
            ok, reason = decision.run_beat, decision.beat_reason
        if ok != self._last:
            self._log(f"[{self._role}] {reason}")
            self._last = ok
        return ok

    def _api_allowed(self) -> bool:
        """The local API stands down while the compose backend runs: both serve
        :8000 against the same Redis/Postgres, and whichever binds the port first
        (after a reboot, often this stale local process) shadows the other."""
        compose = self._probe.services()
        if self._force:
            ok, reason = True, f"api forced (--force-workers / {FORCE_WORKERS_ENV})"
        elif compose is None:
            ok, reason = True, "could not query docker; running the local api"
        elif "backend" in compose:
            ok, reason = False, (
                "docker compose backend is running (it serves :8000); not starting "
                f"a second API (override: --force-workers or {FORCE_WORKERS_ENV}=1)"
            )
        else:
            ok, reason = True, "no docker compose backend running; running the local api"
        if ok != self._last:
            self._log(f"[api] {reason}")
            self._last = ok
        return ok


@dataclass
class _Service:
    """One supervised child process (API, worker, or beat)."""

    name: str
    command: list[str]
    cwd: Path
    child: subprocess.Popen[bytes] | None = field(default=None)
    # Re-checked before every (re)start and while running: False = stand down
    # because the docker compose stack runs this role (see FleetGate).
    gate: FleetGate | None = field(default=None)


@dataclass
class _RunState:
    """Shared supervision state across the per-service threads."""

    stopping: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def resolve_services(args: argparse.Namespace) -> list[_Service]:
    """Return the services to supervise.

    A trailing ``-- <cmd>`` overrides everything and supervises exactly that one
    command (worker/beat are not added). Otherwise the default runtime is the API
    plus the Celery worker and beat — the full stack needed for goals to execute
    and schedules to fire — each toggleable with ``--no-worker`` / ``--no-beat``.
    """
    if args.command:
        return [_Service("service", list(args.command), Path(args.cwd or REPO_ROOT))]
    cwd = Path(args.cwd or BACKEND_DIR)
    project = os.environ.get(COMPOSE_PROJECT_ENV, "").strip() or COMPOSE_PROJECT_DEFAULT
    probe = ComposeFleetProbe(project)
    force = force_workers(args)
    api_gate = FleetGate(probe, role="api", want=True, force=force)
    services = [_Service("api", default_command(args), cwd, gate=api_gate)]
    if getattr(args, "worker", True):
        gate = FleetGate(probe, role="worker", want=True, force=force)
        services.append(_Service("worker", worker_command(), cwd, gate=gate))
    if getattr(args, "beat", True):
        gate = FleetGate(probe, role="beat", want=True, force=force)
        services.append(_Service("beat", beat_command(), cwd, gate=gate))
    return services


# --------------------------------------------------------------------------- #
# the supervisor loop
# --------------------------------------------------------------------------- #
def _supervise_one(service: _Service, awake: object, state: _RunState) -> None:
    """Keep one service alive forever: spawn -> wait -> restart with backoff.

    This is the original single-child loop, now run in its own thread per service
    so the API, worker, and beat are each supervised independently — one crashing
    or restarting never takes the others down.
    """
    backoff = BACKOFF_START
    gate = service.gate
    while not state.stopping:
        if gate is not None and not gate.allowed():
            # The compose stack runs this role: stand by, re-check periodically.
            _interruptible_sleep(FLEET_RECHECK_SECONDS, lambda: state.stopping)
            continue
        started = time.monotonic()
        capture = capturing_child_output()
        # Logging to the rotating file: pipe the child through us instead of
        # letting it inherit (and grow, unrotated) whatever our stdout is.
        piped: dict[str, object] = (
            {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
            }
            if capture
            else {}
        )
        try:
            child = subprocess.Popen(  # type: ignore[call-overload]
                service.command,
                cwd=str(service.cwd),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                **piped,
            )
        except OSError as exc:
            log(f"[{service.name}] failed to launch: {exc}")
            if state.stopping:
                break
            _interruptible_sleep(backoff, lambda: state.stopping)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue

        with state.lock:
            service.child = child
        pump: threading.Thread | None = None
        if capture and child.stdout is not None:
            pump = threading.Thread(
                target=_pump_output,
                args=(service.name, child.stdout),
                name=f"output-{service.name}",
                daemon=True,
            )
            pump.start()
        log(f"[{service.name}] started (child pid {child.pid})")
        stand_down = [False]

        def stopping(gate=gate, stand_down=stand_down) -> bool:
            if state.stopping:
                return True
            # Compose's worker/beat came up while ours runs: stop ours.
            if gate is not None and not gate.allowed():
                stand_down[0] = True
                return True
            return False

        rc = _wait_child(child, ensure_awake=awake, stopping=stopping)
        with state.lock:
            service.child = None
        if pump is not None:
            # Drain what the child wrote before exiting; a grandchild still
            # holding the pipe must not block the restart.
            pump.join(timeout=5)
        uptime = time.monotonic() - started

        if state.stopping:
            log(f"[{service.name}] exited (rc={rc}) during shutdown")
            break
        if stand_down[0]:
            log(f"[{service.name}] stopped (rc={rc}): docker compose runs it now")
            backoff = BACKOFF_START
            continue

        if uptime >= HEALTHY_UPTIME:
            backoff = BACKOFF_START  # it was stable; recover quickly
        log(
            f"[{service.name}] exited (rc={rc}, up {uptime:.0f}s); "
            f"restarting in {backoff:.0f}s"
        )
        _interruptible_sleep(backoff, lambda: state.stopping)
        backoff = min(backoff * 2, BACKOFF_MAX)


def cmd_run(args: argparse.Namespace) -> int:
    log_file = getattr(args, "log_file", None)
    if not log_file and _same_file(1, LOG_FILE):
        # An older launchd plist sends our stdout straight into run_forever.log:
        # take it over as a rotating log instead of letting it grow forever.
        log_file = str(LOG_FILE)
    if log_file:
        configure_log_file(Path(log_file), log_rotation_settings(args), stdio_path=STDIO_LOG)
    existing = read_pid_file()
    if existing and existing != os.getpid() and not args.force:
        log(f"supervisor already running as pid {existing} (use --force to run anyway)")
        return 1

    services = resolve_services(args)
    for service in services:
        if not service.cwd.is_dir():
            log(f"working dir does not exist: {service.cwd}")
            return 1

    state = _RunState()

    def handle_signal(signum: int, _frame: object) -> None:
        state.stopping = True
        log(f"received {signal.Signals(signum).name}; shutting down services")
        # Break out of backoff sleeps and terminate every live child promptly.
        with state.lock:
            for service in services:
                if service.child is not None and service.child.poll() is None:
                    _terminate(service.child)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    write_pid_file()
    log(f"supervisor pid {os.getpid()}")
    for service in services:
        log(f"service [{service.name}]: {' '.join(service.command)} (cwd {service.cwd})")

    try:
        with make_backend("run_forever: keep AgentVerse service alive") as awake:
            kinds = [ASSERT_SYSTEM]  # no idle system sleep
            if args.display:
                kinds.append(ASSERT_DISPLAY)  # also keep the screen on
            awake.acquire(kinds)
            log(f"holding awake: {', '.join(awake.held)}")

            threads = [
                threading.Thread(
                    target=_supervise_one,
                    args=(service, awake, state),
                    name=f"supervise-{service.name}",
                    daemon=True,
                )
                for service in services
            ]
            for thread in threads:
                thread.start()

            # Main thread stays alive keeping the caffeinate fallback healthy until
            # a signal sets stopping; the per-service threads own their children.
            while not state.stopping:
                ensure = getattr(awake, "ensure_alive", None)
                if ensure is not None and ensure():
                    log("caffeinate died; restarted it")
                time.sleep(1)

            for thread in threads:
                thread.join(timeout=15)
    finally:
        if read_pid_file() == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    log("supervisor stopped; sleep re-enabled")
    return 0


def _wait_child(child, ensure_awake, stopping) -> int | None:
    """Wait for the child, keeping the caffeinate fallback alive meanwhile."""
    while True:
        try:
            return child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # If we fell back to a caffeinate subprocess, make sure it's alive.
            ensure = getattr(ensure_awake, "ensure_alive", None)
            if ensure is not None and ensure():
                log("caffeinate died; restarted it")
            if stopping() and child.poll() is None:
                _terminate(child)


def _terminate(child: subprocess.Popen[bytes]) -> None:
    """SIGTERM the child's process group, then SIGKILL if it lingers."""
    try:
        os.killpg(os.getpgid(child.pid), signal.SIGTERM)
    except OSError:
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        except OSError:
            child.kill()


def _interruptible_sleep(seconds: float, stopping) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stopping():
            return
        time.sleep(min(0.25, deadline - time.monotonic()))


# --------------------------------------------------------------------------- #
# start / stop / status / restart (background management)
# --------------------------------------------------------------------------- #
def _forwarded_run_args(args: argparse.Namespace) -> list[str]:
    forwarded = ["run", "--force", "--host", args.host, "--port", str(args.port)]
    if args.display:
        forwarded.append("--display")
    if not getattr(args, "worker", True):
        forwarded.append("--no-worker")
    if not getattr(args, "beat", True):
        forwarded.append("--no-beat")
    if getattr(args, "force_workers", False):
        forwarded.append("--force-workers")
    if args.cwd:
        forwarded += ["--cwd", args.cwd]
    # The detached / launchd supervisor always logs to the rotating LOG_FILE.
    forwarded += ["--log-file", str(getattr(args, "log_file", None) or LOG_FILE)]
    if getattr(args, "log_max_mb", None) is not None:
        forwarded += ["--log-max-mb", f"{args.log_max_mb:g}"]
    if getattr(args, "log_backups", None) is not None:
        forwarded += ["--log-backups", str(args.log_backups)]
    if args.command:
        forwarded += ["--", *args.command]
    return forwarded


def cmd_start(args: argparse.Namespace) -> int:
    existing = read_pid_file()
    if existing:
        log(f"already running as pid {existing}")
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # The supervisor writes LOG_FILE itself (rotated); its raw stdout/stderr only
    # catch what bypasses that (a crash before logging is set up).
    with STDIO_LOG.open("ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *_forwarded_run_args(args)],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.8)
    if proc.poll() is not None:
        log(f"failed to start; see {STDIO_LOG}")
        return 1
    log(f"started supervisor in background as pid {proc.pid}; log: {LOG_FILE}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    pid = read_pid_file()
    if not pid:
        log("not running")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(150):  # give the child up to ~15s to drain
        if not pid_alive(pid):
            log(f"stopped supervisor pid {pid}")
            return 0
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    PID_FILE.unlink(missing_ok=True)
    log(f"force-killed supervisor pid {pid}")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_stop(args)
    return cmd_start(args)


def cmd_status(_args: argparse.Namespace) -> int:
    pid = read_pid_file()
    if pid:
        log(f"supervisor running (pid {pid})")
    else:
        log("supervisor not running")
    if PLIST_PATH.exists():
        log(f"LaunchAgent installed: {PLIST_PATH}")
    if LOG_FILE.exists():
        log(f"log: {LOG_FILE}")
    return 0 if pid else 1


# --------------------------------------------------------------------------- #
# launchd persistence (start at login, restart the supervisor itself if it dies)
# --------------------------------------------------------------------------- #
PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def cmd_install(args: argparse.Namespace) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    argv = [sys.executable, os.path.abspath(__file__), *_forwarded_run_args(args)]
    # launchd runs jobs with a bare PATH, so the service command (e.g. `uv`)
    # won't be found. Bake a PATH that includes wherever `uv` actually lives.
    import shutil

    uv_path = shutil.which("uv")
    uv_dir = os.path.dirname(uv_path) if uv_path else str(Path.home() / ".local" / "bin")
    launch_path = ":".join(
        dict.fromkeys(  # de-dupe, preserve order
            [
                uv_dir,
                str(Path.home() / ".local" / "bin"),
                "/opt/homebrew/bin",
                "/opt/homebrew/sbin",
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            ]
        )
    )
    PLIST_PATH.write_text(
        PLIST_TEMPLATE.format(
            label=LABEL,
            args="\n".join(f"        <string>{a}</string>" for a in argv),
            path=launch_path,
            workdir=REPO_ROOT,
            # Not LOG_FILE: launchd never rotates its StandardOutPath, and the
            # children used to inherit it (2.5 GB). The supervisor writes and
            # rotates LOG_FILE itself (--log-file, see _forwarded_run_args).
            log=STDIO_LOG,
        )
    )
    subprocess.run(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
    )
    result = subprocess.run(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"launchctl bootstrap failed: {result.stderr.strip()}")
        return 1
    log(f"installed and loaded {LABEL}")
    log("it now starts at login and is restarted automatically if it dies")
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    subprocess.run(
        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
    )
    PLIST_PATH.unlink(missing_ok=True)
    log(f"removed {LABEL}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_forever.py",
        description="Keep the AgentVerse system awake and its service running forever.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Trailing `-- <cmd...>` overrides the default backend command.",
    )
    sub = parser.add_subparsers(dest="command_name")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default="0.0.0.0", help="uvicorn host (default 0.0.0.0)")
        p.add_argument("--port", type=int, default=8000, help="uvicorn port (default 8000)")
        p.add_argument("--cwd", help="working dir for the service command")
        p.add_argument(
            "--display",
            action="store_true",
            help="also keep the display awake (screen never sleeps)",
        )
        p.add_argument(
            "--no-worker",
            dest="worker",
            action="store_false",
            help="do not run the Celery worker (goals will not execute)",
        )
        p.add_argument(
            "--no-beat",
            dest="beat",
            action="store_false",
            help="do not run Celery beat (scheduled/periodic tasks will not fire)",
        )
        p.add_argument(
            "--force-workers",
            action="store_true",
            help=(
                "run the local worker/beat even while the docker compose stack's "
                f"are running (default: defer to compose; env {FORCE_WORKERS_ENV}=1)"
            ),
        )
        p.add_argument(
            "--log-file",
            help=(
                "write the supervisor's and the children's output to this file, "
                "rotated by size (start/install always use run_forever.log)"
            ),
        )
        p.add_argument(
            "--log-max-mb",
            type=float,
            help=(
                f"rotate the log at this size (default {DEFAULT_LOG_MAX_MB:g}; "
                f"env {LOG_MAX_MB_ENV})"
            ),
        )
        p.add_argument(
            "--log-backups",
            type=int,
            help=f"rotated logs to keep (default {DEFAULT_LOG_BACKUPS}; env {LOG_BACKUPS_ENV})",
        )
        p.set_defaults(worker=True, beat=True)
        p.add_argument(
            "command",
            nargs=argparse.REMAINDER,
            help="optional `-- <cmd...>` to supervise instead of the full stack",
        )

    run = sub.add_parser("run", help="foreground: hold awake + supervise (default)")
    add_common(run)
    run.add_argument("--force", action="store_true", help="run even if one is running")
    run.set_defaults(func=cmd_run)

    start = sub.add_parser("start", help="run in a detached background process")
    add_common(start)
    start.set_defaults(func=cmd_start)

    restart = sub.add_parser("restart", help="stop then start the background supervisor")
    add_common(restart)
    restart.set_defaults(func=cmd_restart)

    sub.add_parser("stop", help="stop the supervisor and its service").set_defaults(
        func=cmd_stop
    )
    sub.add_parser("status", help="show supervisor state").set_defaults(func=cmd_status)

    install = sub.add_parser(
        "install", help="install a LaunchAgent (start at login, auto-restart)"
    )
    add_common(install)
    install.set_defaults(func=cmd_install)

    sub.add_parser("uninstall", help="remove the LaunchAgent").set_defaults(
        func=cmd_uninstall
    )
    return parser


def _strip_remainder_dashes(args: argparse.Namespace) -> None:
    # argparse.REMAINDER keeps the leading `--`; drop it so command is clean.
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("run_forever.py targets macOS (uses IOKit power assertions)", file=sys.stderr)
        return 2

    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"run", "start", "stop", "restart", "status", "install", "uninstall"}
    if not argv or (argv[0] not in known and argv[0] not in {"-h", "--help"}):
        argv.insert(0, "run")  # bare invocation / bare flags default to `run`

    args = build_parser().parse_args(argv)
    _strip_remainder_dashes(args)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
