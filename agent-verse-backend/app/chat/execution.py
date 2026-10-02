"""Inline code execution for chat sessions — always inside the code sandbox.

This used to run the snippet with ``subprocess.run([python, "-c", code])`` /
``bash -c`` directly on the API host, inheriting the API process's environment
(database credentials, the vault master key, provider API keys), and any tenant
key — even a viewer's — could call it: remote code execution on the control
plane. Snippets now run through :class:`app.tools.code_interpreter.CodeInterpreter`
(a throw-away Docker container with no network, memory/CPU limits and no
persistent filesystem). Without Docker it fails closed, except for the explicit
development-only opt-in ``AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true`` (never in
production), which also runs with a scrubbed environment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.governance.audit import AuditLog
    from app.tenancy.context import TenantContext

SUPPORTED_LANGUAGES = {"python", "javascript", "bash"}

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
    """Execute short code snippets in the sandboxed code interpreter."""

    async def execute(
        self,
        code: str,
        language: str,
        session_id: str,
        timeout: int = TIMEOUT_SECONDS,
        *,
        tenant_ctx: TenantContext,
        audit_log: AuditLog | None = None,
        redis: Any = None,
    ) -> ExecutionResult:
        """Run *code* in the sandbox through the governed entrypoint (durably audited).

        Raises :class:`app.governance.audit.AuditPersistenceError` when the audit
        row cannot be committed; the route answers 503.
        """
        lang = LANGUAGE_ALIASES.get(language.lower(), language.lower())
        if lang not in SUPPORTED_LANGUAGES:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"Unsupported language: {language}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
                ),
                language=language,
                duration_ms=0,
                error="unsupported_language",
            )

        from app.tools.code_execution import (
            AuditPersistenceError,
            CodeExecutionBusyError,
            CodeExecutionContext,
            execute_governed,
        )

        start = time.monotonic()
        try:
            result = await execute_governed(
                code,
                lang,
                timeout,
                ctx=CodeExecutionContext(
                    tenant_ctx=tenant_ctx, source="chat.execute", ref_id=session_id
                ),
                audit_log=audit_log,
                redis=redis,
            )
        except (AuditPersistenceError, CodeExecutionBusyError):
            raise
        except RuntimeError as exc:
            # No sandbox (production without Docker): refuse, never run on the host.
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=str(exc),
                language=lang,
                duration_ms=(time.monotonic() - start) * 1000,
                error="sandbox_unavailable",
            )
        stdout, stderr = result.stdout or "", result.stderr or ""
        truncated = len(stdout) + len(stderr) > MAX_OUTPUT_CHARS
        if truncated:
            stdout = stdout[:MAX_OUTPUT_CHARS]
        return ExecutionResult(
            exit_code=124 if result.timed_out else result.exit_code,
            stdout=stdout,
            stderr=stderr,
            language=lang,
            duration_ms=round(result.execution_time_ms or (time.monotonic() - start) * 1000, 2),
            truncated=truncated,
            error="timeout" if result.timed_out else None,
        )
