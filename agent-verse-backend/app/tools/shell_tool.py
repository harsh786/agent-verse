"""Sandboxed shell command execution tool.

Uses Docker for isolation when available. Requires explicit opt-in via
AGENTVERSE_ALLOW_SHELL_EXEC=true. Blocked by default in production.

Docker image: python:3.12-slim (same as backend). Network disabled.
"""
from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass

from app.observability.logging import get_logger

logger = get_logger(__name__)

_ALLOWED_COMMANDS = frozenset({
    "echo", "cat", "ls", "grep", "awk", "sed", "sort", "uniq", "wc",
    "head", "tail", "find", "date", "env", "pwd", "which", "python3",
    "node", "git", "npm", "pip", "curl",  # extend as needed
})

_MAX_OUTPUT = 64 * 1024  # 64KB


@dataclass
class ShellResult:
    command: str
    stdout: str
    stderr: str
    returncode: int
    success: bool
    error: str | None = None


class ShellTool:
    """Execute shell commands in a sandboxed environment.

    Requires: AGENTVERSE_ALLOW_SHELL_EXEC=true
    Uses Docker when available for network-isolated execution.
    """

    name = "shell_exec"
    description = "Execute shell commands (git, python scripts, system commands). Sandboxed."

    def __init__(self) -> None:
        self._allowed = os.getenv("AGENTVERSE_ALLOW_SHELL_EXEC", "false").lower() == "true"
        self._docker_image = os.getenv("SHELL_SANDBOX_IMAGE", "python:3.12-slim")

    async def execute(self, *, command: str, working_dir: str = "/tmp") -> dict:
        if not self._allowed:
            return {
                "error": (
                    "Shell execution is disabled. "
                    "Set AGENTVERSE_ALLOW_SHELL_EXEC=true to enable."
                )
            }

        # Validate first token against allowlist
        tokens = shlex.split(command)
        if not tokens:
            return {"error": "Empty command"}
        base_cmd = os.path.basename(tokens[0])
        if base_cmd not in _ALLOWED_COMMANDS:
            return {"error": f"Command '{base_cmd}' not in allowed list: {sorted(_ALLOWED_COMMANDS)}"}

        # Try Docker sandbox first
        try:
            import docker  # type: ignore[import]
            client = docker.from_env()
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.containers.run(
                    self._docker_image,
                    command=["sh", "-c", command],
                    working_dir="/workspace",
                    mem_limit="256m",
                    network_disabled=True,
                    read_only=False,
                    tmpfs={"/workspace": "size=128m", "/tmp": "size=64m"},
                    remove=True,
                    stdout=True,
                    stderr=True,
                    timeout=30,
                ),
            )
            output = result.decode("utf-8", errors="replace") if isinstance(result, bytes) else str(result)
            return ShellResult(command=command, stdout=output[:_MAX_OUTPUT], stderr="", returncode=0, success=True).__dict__
        except ImportError:
            pass  # Docker not available
        except Exception as docker_err:
            logger.warning("shell_docker_failed", error=str(docker_err))

        # Subprocess fallback (only when AGENTVERSE_ALLOW_SHELL_EXEC=true)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            return ShellResult(
                command=command,
                stdout=stdout.decode("utf-8", errors="replace")[:_MAX_OUTPUT],
                stderr=stderr.decode("utf-8", errors="replace")[:_MAX_OUTPUT],
                returncode=proc.returncode or 0,
                success=(proc.returncode == 0),
            ).__dict__
        except asyncio.TimeoutError:
            return {"error": "Command timed out after 30s", "command": command}
        except Exception as exc:
            return {"error": str(exc), "command": command}

    def to_tool_def(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "working_dir": {"type": "string", "default": "/tmp"},
                },
                "required": ["command"],
            },
        }
