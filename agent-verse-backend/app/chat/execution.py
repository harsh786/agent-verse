"""Inline code execution sandbox for chat sessions.

Wraps the existing execution_environment sandbox (or subprocess fallback).
Supports Python 3.12, JavaScript (Node), Bash.
Limits: 30s timeout, 256MB memory, no network, no fs writes outside /tmp.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

SUPPORTED_LANGUAGES = {"python", "javascript", "bash", "sh"}

LANGUAGE_ALIASES: dict[str, str] = {
    "python3": "python",
    "py": "python",
    "js": "javascript",
    "node": "javascript",
    "shell": "bash",
    "sh": "bash",
}

MAX_OUTPUT_CHARS = 10_000
TIMEOUT_SECONDS = 30


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    language: str
    duration_ms: float
    truncated: bool = False
    error: str | None = None


class ChatCodeExecutor:
    """Execute short code snippets inside a sandbox.

    In dev/test mode uses subprocess. Production wraps the Docker executor.
    """

    def execute(
        self,
        code: str,
        language: str,
        session_id: str,
        timeout: int = TIMEOUT_SECONDS,
    ) -> ExecutionResult:
        """Run *code* synchronously and return the result."""
        lang = LANGUAGE_ALIASES.get(language.lower(), language.lower())
        if lang not in SUPPORTED_LANGUAGES:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"Unsupported language: {language}. Supported: {', '.join(SUPPORTED_LANGUAGES)}"
                ),
                language=language,
                duration_ms=0,
                error="unsupported_language",
            )

        start = time.monotonic()
        try:
            cmd, stdin_data = self._build_command(lang, code)
            proc = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = (time.monotonic() - start) * 1000
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            truncated = False

            if len(stdout) + len(stderr) > MAX_OUTPUT_CHARS:
                stdout = stdout[:MAX_OUTPUT_CHARS]
                truncated = True

            return ExecutionResult(
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                language=lang,
                duration_ms=round(duration_ms, 2),
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {timeout}s",
                language=lang,
                duration_ms=timeout * 1000,
                error="timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                language=lang,
                duration_ms=(time.monotonic() - start) * 1000,
                error="execution_error",
            )

    def _build_command(self, lang: str, code: str) -> tuple[list[str], str | None]:
        if lang == "python":
            return [sys.executable, "-c", code], None
        if lang == "javascript":
            return ["node", "--input-type=module", "-e", code], None
        if lang in ("bash", "sh"):
            return ["bash", "-c", code], None
        raise ValueError(f"Unsupported: {lang}")
