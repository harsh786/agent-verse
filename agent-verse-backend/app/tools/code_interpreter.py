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
import os
import tempfile
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

# Docker is optional -- detected at runtime
_DOCKER_AVAILABLE = False
try:
    import docker as _docker_module
    _docker_module.from_env()
    _DOCKER_AVAILABLE = True
except Exception:
    pass


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
        """Return True if Docker is available on this host."""
        return _DOCKER_AVAILABLE

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

        if not _DOCKER_AVAILABLE:
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
        ext = _FILE_EXTENSIONS[language]
        suffix = f".{ext}"

        container_path = f"/sandbox/code{suffix}"
        cmd = {
            "python": ["python3", container_path],
            "javascript": ["node", container_path],
            "bash": ["sh", container_path],
        }.get(language, ["python3", container_path])

        t0 = time.monotonic()

        # Write code to a host-side temp file; volume-mount it read-only.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            client = docker.from_env()

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.containers.run(
                    image,
                    command=cmd,
                    volumes={tmp_path: {"bind": container_path, "mode": "ro"}},
                    remove=True,
                    network_mode="none",
                    mem_limit=self._memory_limit,
                    cpu_quota=self._cpu_quota,
                    read_only=True,
                    tmpfs={"/tmp": "size=64m,noexec=off"},
                    user="1000:1000",
                    environment={"PYTHONDONTWRITEBYTECODE": "1"},
                    stdout=True,
                    stderr=True,
                    timeout=effective_timeout,
                    detach=False,
                ),
            )
            elapsed = (time.monotonic() - t0) * 1000
            if isinstance(result, bytes):
                stdout = result.decode("utf-8", errors="replace")
                stderr = ""
                exit_code = 0
            else:
                stdout = ""
                stderr = str(result)
                exit_code = 1

            return CodeResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=False,
                execution_time_ms=elapsed,
            )

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            error_str = str(exc)
            timed_out = "timeout" in error_str.lower() or "timed out" in error_str.lower()
            return CodeResult(
                stdout="",
                stderr=error_str,
                exit_code=1,
                timed_out=timed_out,
                execution_time_ms=elapsed,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

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

        if os.getenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "false").lower() != "true":
            return CodeResult(
                stdout="",
                stderr=(
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

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=f".{ext}", delete=False
        ) as f:
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

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=float(effective_timeout)
                )
                return CodeResult(
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
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
            try:
                os.unlink(tmpfile)
            except Exception:
                pass


def get_interpreter(timeout: int = 30) -> CodeInterpreter:
    """Return a default-configured CodeInterpreter instance."""
    return CodeInterpreter(default_timeout=timeout)
