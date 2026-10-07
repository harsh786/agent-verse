"""AgentVerse code-sandbox runner: a small HTTP service that executes untrusted code.

Workflow ``code`` steps, ``POST /tools/execute-code`` and chat code blocks run
tenant code. The workers used to need a Docker daemon for that (each execution
in a throw-away container); the shipped deployments have none — and mounting the
host Docker socket into an app container would hand every tenant a host-root
escape. This runner is the production path instead: its own container, reached
over the internal network with a shared secret, with nothing of the platform in
reach (no database, no Redis, no provider keys, no egress).

Standard library only, never imports the rest of the app, so it runs from the
slim image (``Dockerfile.sandbox``: ``python:3.12-slim`` + this file) or from the
backend image as ``python -m app.sandbox.runner``.

API
  ``GET /healthz``      liveness/readiness (no auth): isolation mode, languages.
  ``POST /v1/execute``  ``Authorization: Bearer <CODE_SANDBOX_TOKEN>``; body
                        ``{"language": "python", "code": "...", "timeout_seconds": 30}``;
                        answers ``{stdout, stderr, exit_code, timed_out,
                        output_truncated, duration_ms, isolation}``.
                        401 bad/missing token, 400 bad request, 413 too large,
                        429 every execution slot busy.

Per execution
  * a fresh process (``python -I`` bootstrap; ``bash -s`` / ``node -`` exec'd from
    it), started in its own session so the whole group is killed afterwards;
  * a minimal environment built from scratch — nothing the runner was started
    with (its token included) is inherited;
  * resource rlimits applied before the program is read: CPU seconds, address
    space (memory), largest file, open files, no core dumps, and (UID mode)
    processes — a fork bomb is bounded per execution;
  * a wall-clock timeout (the whole process group is SIGKILLed) and a byte cap
    per output stream (the program is killed at the first byte over it);
  * a private ``0700`` working directory on the tmpfs, removed afterwards.

Isolation modes
  ``uid`` (the runner runs as root in a container with only SETUID/SETGID/KILL):
      every concurrent execution slot has its own unprivileged UID/GID, so a
      program cannot read another execution's files or /proc entries, signal it,
      or signal the runner; after each run every process of that UID is killed
      (also ones that escaped the session) before the UID is reused.
  ``shared`` (the runner is not root — local development and tests): programs
      run as the runner's own user; the runner marks itself non-dumpable so its
      /proc entries (environment) are unreadable, but concurrent programs share a
      UID. Not for multi-tenant production.

The network boundary is the deployment's: compose puts the runner on an
``internal: true`` network only, and the Helm charts give it a NetworkPolicy
that denies all egress. The runner itself opens no connection to anything.
"""

from __future__ import annotations

import contextlib
import ctypes
import hmac
import json
import logging
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import IO, Any, cast

__all__ = [
    "EXECUTE_PATH",
    "HEALTH_PATH",
    "ExecutionResult",
    "RunnerConfig",
    "SandboxBusyError",
    "SandboxExecutor",
    "SandboxServer",
    "main",
]

log = logging.getLogger("agentverse.code_sandbox")

EXECUTE_PATH = "/v1/execute"
HEALTH_PATH = "/healthz"
_PR_SET_DUMPABLE = 4
_MIN_TOKEN_CHARS = 16
_CHUNK = 64 * 1024
_READER_JOIN_S = 5.0
_CLEANUP_TIMEOUT_S = 15.0
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

# Runs as the execution's own (unprivileged) user, before any untrusted byte is
# read: private workdir, rlimits (soft == hard, so the program cannot raise
# them), non-dumpable (its /proc entries are not readable by other programs),
# then the program — python in this interpreter, other languages exec'd.
_BOOTSTRAP = r"""
import json, os, resource, sys
cfg = json.loads(sys.argv[1])
os.umask(0o077)
os.mkdir(cfg["workdir"], 0o700)
os.chdir(cfg["workdir"])
for name, value in cfg["limits"].items():
    try:
        resource.setrlimit(getattr(resource, name), (value, value))
    except (OSError, ValueError, AttributeError) as exc:
        if cfg["strict"]:
            sys.stderr.write("sandbox: cannot apply %s: %s\n" % (name, exc))
            sys.exit(126)
if sys.platform.startswith("linux"):
    try:
        import ctypes
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    except Exception:
        pass
if cfg["lang"] == "python":
    src = sys.stdin.read()
    sys.stdin.close()
    sys.stdin = open(os.devnull)
    sys.argv = ["main"]
    del cfg
    import traceback
    _globals = {"__name__": "__main__", "__builtins__": __builtins__}
    try:
        exec(compile(src, "<code>", "exec"), _globals)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(1)
else:
    os.execv(cfg["argv"][0], cfg["argv"])
"""

# Runs as the slot's user after every execution: kill every process of that
# user (also ones that left the session), then remove the workdir.
_CLEANUP = r"""
import os, shutil, signal, sys
try:
    os.kill(-1, signal.SIGKILL)
except OSError:
    pass
shutil.rmtree(sys.argv[1], ignore_errors=True)
sys.exit(1 if os.path.exists(sys.argv[1]) else 0)
"""


def _env_int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number, got {raw!r}")
    return value


@dataclass(frozen=True)
class RunnerConfig:
    """Runner settings (``CODE_SANDBOX_*`` environment variables)."""

    token: str = field(repr=False)
    # The container's own interface; the NetworkPolicy / internal network gates it.
    host: str = "0.0.0.0"
    port: int = 8080
    workdir_base: str = "/sandbox"
    max_concurrency: int = 4
    queue_timeout_s: float = 10.0
    default_timeout_s: float = 30.0
    max_timeout_s: float = 120.0
    memory_mb: int = 256
    max_output_bytes: int = 1_000_000
    max_code_bytes: int = 1_000_000
    max_file_mb: int = 16
    max_open_files: int = 256
    max_processes: int = 64
    uid_base: int = 20000

    def __post_init__(self) -> None:
        if len(self.token or "") < _MIN_TOKEN_CHARS:
            raise ValueError(
                f"CODE_SANDBOX_TOKEN must be set (at least {_MIN_TOKEN_CHARS} characters): "
                "every execution request is authenticated with it"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunnerConfig:
        e = os.environ if env is None else env
        return cls(
            token=(e.get("CODE_SANDBOX_TOKEN") or "").strip(),
            host=(e.get("CODE_SANDBOX_HOST") or "0.0.0.0").strip(),
            port=_env_int(e, "CODE_SANDBOX_PORT", 8080, minimum=0),
            workdir_base=(e.get("CODE_SANDBOX_WORKDIR") or "/sandbox").strip(),
            max_concurrency=_env_int(e, "CODE_SANDBOX_MAX_CONCURRENCY", 4),
            queue_timeout_s=_env_float(e, "CODE_SANDBOX_QUEUE_TIMEOUT_SECONDS", 10.0),
            default_timeout_s=_env_float(e, "CODE_SANDBOX_DEFAULT_TIMEOUT_SECONDS", 30.0),
            max_timeout_s=_env_float(e, "CODE_SANDBOX_MAX_TIMEOUT_SECONDS", 120.0),
            memory_mb=_env_int(e, "CODE_SANDBOX_MEMORY_MB", 256, minimum=32),
            max_output_bytes=_env_int(e, "CODE_SANDBOX_MAX_OUTPUT_BYTES", 1_000_000),
            max_code_bytes=_env_int(e, "CODE_SANDBOX_MAX_CODE_BYTES", 1_000_000),
            max_file_mb=_env_int(e, "CODE_SANDBOX_MAX_FILE_MB", 16),
            max_open_files=_env_int(e, "CODE_SANDBOX_MAX_OPEN_FILES", 256, minimum=16),
            max_processes=_env_int(e, "CODE_SANDBOX_MAX_PROCESSES", 64, minimum=4),
            uid_base=_env_int(e, "CODE_SANDBOX_UID_BASE", 20000, minimum=1000),
        )


class SandboxBusyError(Exception):
    """Every execution slot stayed busy for the whole queue timeout."""


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    output_truncated: bool
    duration_ms: float
    isolation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "duration_ms": round(self.duration_ms, 2),
            "isolation": self.isolation,
        }


class _CappedReader:
    """Drain one pipe, keeping at most ``cap`` bytes; the first byte over calls
    ``on_overflow`` (the program is killed) and the rest is discarded."""

    def __init__(self, pipe: IO[bytes], cap: int, on_overflow: Callable[[], None]) -> None:
        self._pipe = pipe
        self._cap = cap
        self._on_overflow = on_overflow
        self.buf = bytearray()
        self.overflowed = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            while True:
                chunk = self._pipe.read1(_CHUNK) if hasattr(self._pipe, "read1") else b""
                if not chunk:
                    return
                if self.overflowed:
                    continue
                room = self._cap - len(self.buf)
                if len(chunk) > room:
                    self.buf.extend(chunk[:room])
                    self.overflowed = True
                    with contextlib.suppress(Exception):
                        self._on_overflow()
                    continue
                self.buf.extend(chunk)
        except (OSError, ValueError):
            return

    def text(self) -> str:
        return self.buf.decode("utf-8", errors="replace")


def _write_stdin(pipe: IO[bytes], data: bytes) -> None:
    try:
        pipe.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass  # the program exited (or was killed) before reading all of it
    finally:
        with contextlib.suppress(OSError, ValueError):
            pipe.close()


class SandboxExecutor:
    """Runs one program per call in a fresh, limited process (see module doc)."""

    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        self.uid_isolation = hasattr(os, "geteuid") and os.geteuid() == 0
        self.isolation = "uid" if self.uid_isolation else "shared"
        self.languages = self._discover_languages()
        self._slots: queue.Queue[int] = queue.Queue()
        for slot in range(config.max_concurrency):
            self._slots.put(slot)
        self._quarantined: set[int] = set()
        self._lock = threading.Lock()
        # Children this executor waits for itself (pid -> count); see reap_orphans.
        self._children: dict[int, int] = {}
        self._children_lock = threading.Lock()
        self._prepare_workdir_base()
        # As PID 1 of its container the runner adopts every orphaned process
        # (a program's children once the cleanup has killed them); nobody else
        # would reap them, and zombies would pile up until the PID limit.
        self._reaps_orphans = sys.platform.startswith("linux") and os.getpid() == 1
        if self._reaps_orphans:
            threading.Thread(target=self._reaper_loop, daemon=True).start()

    # ── setup ─────────────────────────────────────────────────────────────

    def _discover_languages(self) -> dict[str, list[str]]:
        langs: dict[str, list[str]] = {"python": []}  # runs inside the bootstrap
        shell = shutil.which("bash", path=_SAFE_PATH) or shutil.which("sh", path=_SAFE_PATH)
        if shell:
            langs["bash"] = [shell, "-s"]
        node = shutil.which("node", path=_SAFE_PATH)
        if node:
            langs["javascript"] = [node, f"--max-old-space-size={self.config.memory_mb}", "-"]
        return langs

    def _prepare_workdir_base(self) -> None:
        base = self.config.workdir_base
        os.makedirs(base, exist_ok=True)
        # UID mode: every slot user may create its own entry, nobody may list
        # the directory or remove another user's entry (sticky). Shared mode:
        # private to the runner's user.
        os.chmod(base, 0o1733 if self.uid_isolation else 0o700)

    # ── health ────────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        with self._lock:
            quarantined = len(self._quarantined)
        usable = self.config.max_concurrency - quarantined
        return {
            "status": "ok" if usable > 0 else "degraded",
            "isolation": self.isolation,
            "languages": sorted(self.languages),
            "slots": self.config.max_concurrency,
            "slots_quarantined": quarantined,
        }

    # ── child processes ───────────────────────────────────────────────────

    def _spawn(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        # Registered under the lock the reaper scans with, so it never reaps a
        # child this executor is about to wait for.
        with self._children_lock:
            proc: subprocess.Popen[bytes] = subprocess.Popen(argv, **kwargs)
            self._children[proc.pid] = self._children.get(proc.pid, 0) + 1
        return proc

    def _forget(self, proc: subprocess.Popen[bytes]) -> None:
        with self._children_lock:
            left = self._children.get(proc.pid, 0) - 1
            if left > 0:
                self._children[proc.pid] = left
            else:
                self._children.pop(proc.pid, None)

    def reap_orphans(self) -> int:
        """Reap zombie processes this (PID 1) runner adopted; returns how many."""
        if not self._reaps_orphans:
            return 0
        me = os.getpid()
        reaped = 0
        with self._children_lock:
            tracked = set(self._children)
            try:
                entries = os.listdir("/proc")
            except OSError:
                return 0
            for name in entries:
                if not name.isdigit() or int(name) in tracked or int(name) == me:
                    continue
                try:
                    with open(f"/proc/{name}/stat", "rb") as fh:
                        fields = fh.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()
                    if fields[0] != "Z" or int(fields[1]) != me:
                        continue
                    os.waitpid(int(name), os.WNOHANG)
                    reaped += 1
                except (OSError, ValueError, IndexError):
                    continue
        return reaped

    def _reaper_loop(self) -> None:
        while True:
            time.sleep(1.0)
            with contextlib.suppress(Exception):
                self.reap_orphans()

    # ── execution ─────────────────────────────────────────────────────────

    def run(self, language: str, code: str, timeout_s: float) -> ExecutionResult:
        if language not in self.languages:
            raise ValueError(
                f"unsupported language {language!r} (this sandbox runs: "
                f"{', '.join(sorted(self.languages))})"
            )
        try:
            slot = self._slots.get(timeout=self.config.queue_timeout_s)
        except queue.Empty:
            raise SandboxBusyError(
                f"all {self.config.max_concurrency} execution slots are busy"
            ) from None
        clean = False
        try:
            result, clean = self._run_in_slot(slot, language, code, timeout_s)
            return result
        finally:
            if clean:
                self._slots.put(slot)
            else:
                # A UID whose processes/files could not be cleared is never
                # handed to another execution (it could read what is left).
                with self._lock:
                    self._quarantined.add(slot)
                log.error("code_sandbox_slot_quarantined slot=%s", slot)

    def _limits(self, language: str, timeout_s: float) -> dict[str, int]:
        cfg = self.config
        limits = {
            "RLIMIT_CPU": math.ceil(timeout_s) + 1,
            "RLIMIT_FSIZE": cfg.max_file_mb * 1024 * 1024,
            "RLIMIT_NOFILE": cfg.max_open_files,
            "RLIMIT_CORE": 0,
        }
        if language != "javascript":  # V8 reserves far more address space than it uses
            limits["RLIMIT_AS"] = cfg.memory_mb * 1024 * 1024
        if self.uid_isolation:
            # Per user — meaningful only when the user is this slot's alone.
            limits["RLIMIT_NPROC"] = cfg.max_processes
        return limits

    def _child_env(self, workdir: str) -> dict[str, str]:
        return {
            "PATH": _SAFE_PATH,
            "HOME": workdir,
            "TMPDIR": workdir,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }

    def _user_kwargs(self, uid: int | None) -> dict[str, Any]:
        if uid is None:
            return {}
        return {"user": uid, "group": uid, "extra_groups": [], "umask": 0o077}

    @staticmethod
    def _wait_exit(proc: subprocess.Popen[bytes], timeout_s: float | None) -> bool:
        """Wait for *proc* to exit WITHOUT reaping it (True = exited).

        While the unreaped leader exists its PID — the process group id — cannot
        be reused, so killing the group afterwards can never hit another
        execution that happened to get the same number. Platforms without
        ``waitid`` (macOS: development only) fall back to a reaping wait.
        """
        if not hasattr(os, "waitid"):
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                return False
            return True
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            try:
                info = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return True
            if info is not None:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def _run_in_slot(
        self, slot: int, language: str, code: str, timeout_s: float
    ) -> tuple[ExecutionResult, bool]:
        cfg = self.config
        uid = cfg.uid_base + slot if self.uid_isolation else None
        workdir = os.path.join(cfg.workdir_base, f"run-{uuid.uuid4().hex}")
        boot = {
            "workdir": workdir,
            "lang": language,
            "argv": self.languages[language],
            "limits": self._limits(language, timeout_s),
            "strict": sys.platform.startswith("linux"),
        }
        t0 = time.monotonic()
        proc = self._spawn(  # the program itself arrives on stdin
            [sys.executable, "-I", "-c", _BOOTSTRAP, json.dumps(boot)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._child_env(workdir),
            cwd="/",
            close_fds=True,
            start_new_session=True,
            **self._user_kwargs(uid),
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        reap_lock = threading.Lock()
        reaped = False

        def kill_group() -> None:
            # Never after the leader is reaped: its PID (the group id) may be reused.
            with reap_lock:
                if not reaped:
                    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(proc.pid, signal.SIGKILL)

        out = _CappedReader(proc.stdout, cfg.max_output_bytes, kill_group)
        err = _CappedReader(proc.stderr, cfg.max_output_bytes, kill_group)
        out.thread.start()
        err.thread.start()
        writer = threading.Thread(
            target=_write_stdin, args=(proc.stdin, code.encode("utf-8")), daemon=True
        )
        writer.start()
        timed_out = not self._wait_exit(proc, timeout_s)
        if timed_out:
            kill_group()
            self._wait_exit(proc, None)
        # Background processes the program left in its session (the leader is
        # still unreaped here, so the group id is still this execution's).
        kill_group()
        with reap_lock:
            proc.wait()
            reaped = True
        self._forget(proc)
        clean = self._cleanup(uid, workdir)
        self.reap_orphans()
        for reader in (out, err):
            reader.thread.join(timeout=_READER_JOIN_S)
        writer.join(timeout=1.0)
        for pipe in (proc.stdout, proc.stderr):
            with contextlib.suppress(OSError):
                pipe.close()
        duration_ms = (time.monotonic() - t0) * 1000

        stdout, stderr = out.text(), err.text()
        truncated = out.overflowed or err.overflowed
        code_rc = proc.returncode if proc.returncode is not None else 1
        exit_code = 128 - code_rc if code_rc < 0 else code_rc
        if truncated:
            stderr = (
                stderr + f"\nOutput exceeded {cfg.max_output_bytes} bytes; the program was stopped."
            ).strip()
        if timed_out:
            exit_code = 124
            stderr = (stderr + f"\nExecution exceeded {timeout_s:g}s and was killed.").strip()
        log.info(
            "code_sandbox_execution language=%s code_bytes=%d exit_code=%d timed_out=%s "
            "truncated=%s duration_ms=%.1f slot=%d",
            language,
            len(code.encode("utf-8")),
            exit_code,
            timed_out,
            truncated,
            duration_ms,
            slot,
        )
        result = ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            output_truncated=truncated,
            duration_ms=duration_ms,
            isolation=self.isolation,
        )
        return result, clean

    def _cleanup(self, uid: int | None, workdir: str) -> bool:
        if uid is None:
            shutil.rmtree(workdir, ignore_errors=True)
            return not os.path.exists(workdir)
        try:
            helper = self._spawn(
                [sys.executable, "-I", "-c", _CLEANUP, workdir],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env={"PATH": _SAFE_PATH},
                cwd="/",
                close_fds=True,
                **self._user_kwargs(uid),
            )
        except OSError as exc:
            log.error("code_sandbox_cleanup_failed uid=%s error=%s", uid, exc)
            return False
        try:
            _out, err = helper.communicate(timeout=_CLEANUP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            helper.kill()
            helper.communicate()
            log.error("code_sandbox_cleanup_failed uid=%s error=timeout", uid)
            return False
        finally:
            self._forget(helper)
        if helper.returncode != 0:
            log.error(
                "code_sandbox_cleanup_failed uid=%s rc=%s stderr=%s",
                uid,
                helper.returncode,
                (err or b"").decode("utf-8", errors="replace")[:300],
            )
            return False
        return True


# ── HTTP ──────────────────────────────────────────────────────────────────────


class SandboxServer(ThreadingHTTPServer):
    """The runner's HTTP server (one thread per connection)."""

    daemon_threads = True

    def __init__(self, config: RunnerConfig, executor: SandboxExecutor | None = None) -> None:
        self.config = config
        self.executor = executor or SandboxExecutor(config)
        self._token = config.token.encode("utf-8")
        # Connections beyond this are refused outright (each holds a thread).
        self.inflight = threading.BoundedSemaphore(max(8, config.max_concurrency * 4))
        super().__init__((config.host, config.port), _Handler)

    def token_ok(self, header: str | None) -> bool:
        if not header:
            return False
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        return hmac.compare_digest(value.strip().encode("utf-8"), self._token)


class _Handler(BaseHTTPRequestHandler):
    server_version = "agentverse-code-sandbox/1"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # seconds to deliver a request (slow clients hold no thread forever)

    @property
    def _srv(self) -> SandboxServer:
        return cast("SandboxServer", self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.info("code_sandbox_http %s %s", self.address_string(), format % args)

    def _send(self, status: HTTPStatus, payload: dict[str, Any], **headers: str) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != HEALTH_PATH:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        health = self._srv.executor.health()
        status = HTTPStatus.OK if health["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
        self._send(status, health)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != EXECUTE_PATH:
            self.close_connection = True
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._srv.token_ok(self.headers.get("Authorization")):
            self.close_connection = True
            self._send(
                HTTPStatus.UNAUTHORIZED,
                {"error": "missing or invalid sandbox token"},
                WWW_Authenticate="Bearer",
            )
            return
        if not self._srv.inflight.acquire(blocking=False):
            self.close_connection = True
            self._send(
                HTTPStatus.TOO_MANY_REQUESTS, {"error": "too many requests"}, Retry_After="5"
            )
            return
        try:
            self._execute()
        finally:
            self._srv.inflight.release()

    def _execute(self) -> None:
        cfg = self._srv.config
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self.close_connection = True
            self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
            return
        if length < 0 or length > cfg.max_code_bytes + 64 * 1024:
            self.close_connection = True
            self._send(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"request exceeds {cfg.max_code_bytes} bytes of code"},
            )
            return
        try:
            body = json.loads(self.rfile.read(length) or b"null")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            language = body.get("language")
            code = body.get("code")
            timeout = body.get("timeout_seconds", cfg.default_timeout_s)
            if not isinstance(language, str) or not isinstance(code, str):
                raise ValueError("'language' and 'code' must be strings")
            if len(code.encode("utf-8")) > cfg.max_code_bytes:
                raise OverflowError
            if isinstance(timeout, bool) or not isinstance(timeout, int | float):
                raise ValueError("'timeout_seconds' must be a number")
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("'timeout_seconds' must be positive")
            timeout_s = min(float(timeout), cfg.max_timeout_s)
        except OverflowError:
            self._send(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"code exceeds {cfg.max_code_bytes} bytes"},
            )
            return
        except (ValueError, UnicodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": f"invalid request: {exc}"})
            return
        try:
            result = self._srv.executor.run(language, code, timeout_s)
        except ValueError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except SandboxBusyError as exc:
            self._send(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": str(exc), "limit": cfg.max_concurrency},
                Retry_After="5",
            )
            return
        except Exception as exc:  # never leak a traceback to the client
            log.exception("code_sandbox_execution_error")
            self._send(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"sandbox failed to run the program ({type(exc).__name__})"},
            )
            return
        self._send(HTTPStatus.OK, result.to_dict())


def _set_non_dumpable() -> bool:
    """Make this process's /proc entries (environment, memory) root-only.

    In shared mode the programs run as this user and could otherwise read the
    runner's environment through /proc/<pid>/environ.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        return int(ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)) == 0
    except Exception:
        return False


def main() -> int:
    logging.basicConfig(
        level=os.getenv("CODE_SANDBOX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = RunnerConfig.from_env()
    except ValueError as exc:
        log.error("code_sandbox_config_invalid %s", exc)
        return 2
    os.environ.pop("CODE_SANDBOX_TOKEN", None)
    non_dumpable = _set_non_dumpable()
    server = SandboxServer(config)
    executor = server.executor
    log.info(
        "code_sandbox_started address=%s:%s isolation=%s languages=%s non_dumpable=%s "
        "slots=%d memory_mb=%d max_timeout_s=%g",
        config.host,
        server.server_address[1],
        executor.isolation,
        ",".join(sorted(executor.languages)),
        non_dumpable,
        config.max_concurrency,
        config.memory_mb,
        config.max_timeout_s,
    )
    if not executor.uid_isolation:
        log.warning(
            "code_sandbox_shared_uid: the runner is not root, so every execution runs as "
            "its own user (no per-execution UID isolation). Development only."
        )

    def _stop(signum: int, _frame: Any) -> None:
        log.info("code_sandbox_stopping signal=%s", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
