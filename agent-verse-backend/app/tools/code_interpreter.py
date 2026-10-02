"""Sandboxed code execution via Docker.

Execution constraints:
- No network access (--network none)
- No filesystem writes outside /tmp (read-only root, tmpfs on /tmp)
- 256MB memory limit
- 1 CPU maximum
- 30 second default timeout
- Runs as non-root user (uid=1000)

Supported languages:
- python (python:3.12-slim)
- javascript (node:20-alpine)
- bash (alpine:latest)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CodeResult:
    """Result of a code execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    execution_time_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "success": self.success,
            "timed_out": self.timed_out,
            "execution_time_ms": round(self.execution_time_ms, 2),
        }


_DOCKER_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "javascript": "node:20-alpine",
    "bash": "alpine:latest",
}

_LANGUAGE_COMMANDS: dict[str, list[str]] = {
    "python": ["python3", "/tmp/code.py"],
    "javascript": ["node", "/tmp/code.js"],
    "bash": ["sh", "/tmp/code.sh"],
}

_FILE_EXTENSIONS: dict[str, str] = {
    "python": "py",
    "javascript": "js",
    "bash": "sh",
}

_PIDS_LIMIT = 64
# Per-stream output cap returned to the caller (the API used to return whatever
# the program printed — a ``print('x' * 10**9)`` was buffered whole into memory).
_MAX_OUTPUT_CHARS = 1_000_000


def _cap_output(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    dropped = len(text) - _MAX_OUTPUT_CHARS
    return text[:_MAX_OUTPUT_CHARS] + f"\n[output truncated: {dropped} more characters]"


# Docker is optional -- detected at RUNTIME, never cached as "unavailable".
#
# This used to probe once at import time and keep the answer for the life of
# the process. A worker whose first import happened while the daemon was briefly
# unreachable (a VM resuming from sleep, a socket not mounted yet) was pinned to
# the subprocess fallback — which is disabled — so its code steps failed with
# "Subprocess execution is disabled" while other workers ran them fine. Now a
# successful probe is remembered (the daemon is there), a failed one only for
# _DOCKER_RETRY_SECONDS, so the sandbox is used as soon as Docker is reachable.
_DOCKER_RETRY_SECONDS = 2.0
_docker_lock = threading.Lock()
_docker_ok = False
_docker_checked_at = 0.0
_docker_error = "not probed yet"


def _probe_docker() -> tuple[bool, str]:
    """Ping the daemon. (ok, reason-if-not)."""
    try:
        import docker

        client = docker.from_env()
        try:
            client.ping()
        finally:
            with contextlib.suppress(Exception):
                client.close()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _docker_available() -> bool:
    """Whether the Docker sandbox can be used now (blocking; see module note)."""
    global _docker_ok, _docker_checked_at, _docker_error
    with _docker_lock:
        if _docker_ok:
            return True
        now = time.monotonic()
        if _docker_checked_at and now - _docker_checked_at < _DOCKER_RETRY_SECONDS:
            return False
        ok, reason = _probe_docker()
        _docker_ok, _docker_checked_at, _docker_error = ok, time.monotonic(), reason
        return ok


def _docker_unavailable_reason() -> str:
    return _docker_error


class CodeInterpreter:
    """Sandboxed code execution via Docker containers.

    Each execution spawns a fresh container, executes the code, and removes
    the container immediately after completion. Containers have no network access
    and no persistent filesystem.

    Falls back to subprocess execution (unsandboxed) when Docker is unavailable.
    Subprocess requires AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true (blocked by default).
    """

    def __init__(
        self,
        default_timeout: int = 30,
        timeout: int | None = None,  # alias for default_timeout
        memory_limit: str = "256m",
        cpu_quota: int = 100000,  # 1 CPU in Docker CPU quota units
    ) -> None:
        self._timeout = timeout if timeout is not None else default_timeout
        self._memory_limit = memory_limit
        self._cpu_quota = cpu_quota

    @staticmethod
    def _check_docker() -> bool:
        """Return True if Docker is available on this host (probed live)."""
        return _docker_available()

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        *,
        tenant_id: str | None = None,  # for audit/scoping; not used in sandbox
    ) -> CodeResult:
        """Execute code in a sandboxed Docker container.

        Falls back to restricted subprocess execution if Docker unavailable.
        """
        if language not in _DOCKER_IMAGES:
            return CodeResult(
                stdout="",
                stderr=f"Unsupported language: {language!r}. Supported: {list(_DOCKER_IMAGES)}",
                exit_code=1,
            )

        if not await asyncio.to_thread(_docker_available):
            return await self._execute_subprocess_fallback(code, language, timeout)

        return await self._execute_docker(code, language, timeout)

    async def _execute_docker(
        self,
        code: str,
        language: str,
        timeout: int | None,
    ) -> CodeResult:
        """Execute code in Docker container with strict isolation.

        Writes code to a host temp file, then mounts it read-only into the
        container using volumes= (the correct docker-py API).
        """
        import time

        import docker

        effective_timeout = timeout if timeout is not None else self._timeout
        image = _DOCKER_IMAGES[language]
        # The program is fed on stdin, not bind-mounted from a host temp file.
        # A bind mount only works when this process and the Docker daemon share
        # a filesystem; when the API runs in a container talking to the host
        # daemon over the socket (the normal deployment) or through a VM, the
        # temp path does not exist daemon-side and Docker silently mounts an
        # empty DIRECTORY in its place — every program failed with "can't find
        # '__main__' module". stdin has no such dependency and no size limit.
        cmd = {
            "python": ["python3", "-"],
            "javascript": ["node", "-"],
            "bash": ["sh", "-s"],
        }.get(language, ["python3", "-"])

        t0 = time.monotonic()

        def _run_in_container() -> CodeResult:
            """Blocking container lifecycle, run in a worker thread.

            docker-py's ``containers.run()`` has no ``timeout`` argument — passing
            one raised TypeError on every call, so the real (isolated) sandbox
            path never executed anything. And with ``detach=False`` there is no
            way to bound wall-clock time at all: a ``while True:`` would pin this
            thread forever. So: start detached, ``wait()`` with the deadline, kill
            on expiry, read stdout/stderr separately (a non-zero exit used to
            raise ContainerError and lose the program's own stderr), and always
            remove the container.
            """
            import requests

            client = docker.from_env()
            # create + start (not run): run() creates then starts, and a failed
            # start leaves the created container behind with nothing to remove it.
            container = client.containers.create(
                image,
                command=cmd,
                # stdin_open without detach makes docker-py set StdinOnce, so
                # closing our write side delivers EOF to the program.
                stdin_open=True,
                network_mode="none",
                mem_limit=self._memory_limit,
                cpu_quota=self._cpu_quota,
                read_only=True,
                # "noexec=off" (the previous value) is not a mount option, so the
                # daemon refused to start every container. "exec" is what it
                # meant; nosuid/nodev are plain hardening.
                tmpfs={"/tmp": "size=64m,exec,nosuid,nodev"},
                user="1000:1000",
                environment={"PYTHONDONTWRITEBYTECODE": "1"},
                # A fork bomb used to be bounded only by memory; no process cap,
                # full default capability set, privilege escalation allowed.
                pids_limit=_PIDS_LIMIT,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
            timed_out = False
            exit_code = 1
            try:
                import socket as _socket

                attached = container.attach_socket(params={"stdin": 1, "stream": 1})
                container.start()
                raw = getattr(attached, "_sock", attached)
                try:
                    raw.sendall(code.encode("utf-8"))
                    with contextlib.suppress(OSError):
                        raw.shutdown(_socket.SHUT_WR)
                finally:
                    with contextlib.suppress(Exception):
                        attached.close()
                try:
                    status = container.wait(timeout=effective_timeout)
                    exit_code = int(status.get("StatusCode", 1))
                except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                    timed_out = True
                    with contextlib.suppress(Exception):
                        container.kill()
                out = _cap_output(
                    container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                )
                err = _cap_output(
                    container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
                )
            finally:
                with contextlib.suppress(Exception):
                    container.remove(force=True)
            if timed_out:
                err = (err + f"\nExecution exceeded {effective_timeout}s and was killed.").strip()
            return CodeResult(
                stdout=out,
                stderr=err,
                exit_code=exit_code if not timed_out else 124,
                timed_out=timed_out,
                execution_time_ms=(time.monotonic() - t0) * 1000,
            )

        try:
            return await asyncio.get_running_loop().run_in_executor(None, _run_in_container)
        except Exception as exc:
            # Infrastructure failure (image pull, daemon error) — NOT a timeout.
            # The old heuristic pattern-matched "timeout" in the message and so
            # reported the TypeError above as timed_out=True.
            return CodeResult(
                stdout="",
                stderr=f"sandbox error: {exc}",
                exit_code=1,
                timed_out=False,
                execution_time_ms=(time.monotonic() - t0) * 1000,
            )

    async def _execute_subprocess_fallback(
        self,
        code: str,
        language: str,
        timeout: int | None,
    ) -> CodeResult:
        """Fallback subprocess execution when Docker unavailable (testing only).

        WARNING: This is NOT sandboxed. Only use in tests.
        Requires AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true -- blocked by default.
        """
        import time

        if os.getenv("ENVIRONMENT", "development") == "production":
            raise RuntimeError(
                f"Docker sandbox unavailable ({_docker_unavailable_reason()}); "
                "unsandboxed subprocess execution is disabled in production. "
                "Start the Docker sandbox (colima start + docker pull python:3.12-slim)."
            )

        if os.getenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "false").lower() != "true":
            return CodeResult(
                stdout="",
                stderr=(
                    f"Docker sandbox unavailable ({_docker_unavailable_reason()}). "
                    "Subprocess execution is disabled. "
                    "Set AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true to enable "
                    "(testing/development only -- not sandboxed)."
                ),
                exit_code=1,
                timed_out=False,
            )

        effective_timeout = timeout if timeout is not None else self._timeout
        ext = _FILE_EXTENSIONS[language]
        t0 = time.monotonic()

        with tempfile.NamedTemporaryFile(mode="w", suffix=f".{ext}", delete=False) as f:
            f.write(code)
            tmpfile = f.name

        try:
            if language == "python":
                cmd = ["python3", tmpfile]
            elif language == "javascript":
                cmd = ["node", tmpfile]
            elif language == "bash":
                cmd = ["sh", tmpfile]
            else:
                return CodeResult("", "Unsupported language", 1)

            # Scrubbed environment: the child must not inherit the API process's
            # secrets (DB URLs, vault master key, provider keys).
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    "PATH": os.getenv("PATH", "/usr/bin:/bin"),
                    "HOME": tempfile.gettempdir(),
                    "LANG": "C.UTF-8",
                },
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=float(effective_timeout)
                )
                return CodeResult(
                    stdout=_cap_output(stdout_bytes.decode("utf-8", errors="replace")),
                    stderr=_cap_output(stderr_bytes.decode("utf-8", errors="replace")),
                    exit_code=proc.returncode or 0,
                    timed_out=False,
                    execution_time_ms=(time.monotonic() - t0) * 1000,
                )
            except TimeoutError:
                proc.kill()
                return CodeResult(
                    stdout="",
                    stderr=f"Execution timed out after {effective_timeout}s",
                    exit_code=1,
                    timed_out=True,
                    execution_time_ms=(time.monotonic() - t0) * 1000,
                )
        finally:
            with contextlib.suppress(Exception):
                os.unlink(tmpfile)


def get_interpreter(timeout: int = 30) -> CodeInterpreter:
    """Return a default-configured CodeInterpreter instance."""
    return CodeInterpreter(default_timeout=timeout)
