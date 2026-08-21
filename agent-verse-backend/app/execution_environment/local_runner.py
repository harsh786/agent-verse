"""Local subprocess runner — provides real OS-level process isolation.

Each goal execution spawns a fresh subprocess with an explicitly restricted
environment (no .env file, no host secrets, no Docker socket).  Resource
limits are enforced via the ``resource`` stdlib module in the worker
entrypoint.

This runner is gated behind ``ISOLATED_EXECUTION_LOCAL_RUNNER=true``.

Isolation properties
--------------------
- Separate process group and address space (new session via ``start_new_session=True``)
- Clean environment: only keys in ``_ALLOWED_ENV_KEYS`` are passed through
- **REDIS_URL and DATABASE_URL are NOT in the allowed list** — the runner
  injects only scoped credentials via ``_ISOLATED_WORKER_DB_URL`` and
  ``_ISOLATED_WORKER_REDIS_URL``.  This prevents the full-privilege host
  Redis/DB credentials from leaking into the subprocess.
- Wall-clock timeout enforced by ``asyncio.wait_for``
- **Process group kill** on timeout / output-limit breach (``os.killpg``)
  to prevent orphaned grandchild processes
- **stderr captured and logged** at WARNING level for observability

Security notes
--------------
- The ``scoped_llm_api_key`` is passed ONLY via ``_ISOLATED_WORKER_LLM_KEY``
  (not also inside the envelope blob) to minimise credential exposure in the
  subprocess environment.
- DB credentials: the caller is responsible for providing a scoped
  ``_ISOLATED_WORKER_DB_URL``.  Until a per-tenant DB role exists, the same
  URL as the control plane is passed but the worker enforces RLS via
  SET LOCAL app.tenant_id.  TODO: provision per-tenant read-only DB role.
- Redis scoping: ``_ISOLATED_WORKER_REDIS_URL`` is a standard Redis URL
  (not URL + query param); key-prefix isolation is the responsibility of
  the worker entrypoint which reads ``scoped_redis_prefix`` from the envelope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution_environment.health import HealthStatus, RunnerHealthCheck
from app.execution_environment.models import (
    CodeCancellationReceipt,
    ExecutionFailureReason,
    ExecutionKind,
    ExecutionRequest,
    ExecutionResult,
    RunnerType,
)
from app.execution_environment.runner_client import BaseRunner

logger = logging.getLogger(__name__)

# Environment variables explicitly passed to the subprocess.
# CRITICAL: This list must NOT include DATABASE_URL, REDIS_URL, any .env
# file path, SSH_AUTH_SOCK, DOCKER_HOST, or any cloud credential key names.
_ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "ENVIRONMENT",
        # Injected explicitly by the runner at dispatch time (never inherited):
        "_ISOLATED_WORKER_ENVELOPE",
        "_ISOLATED_WORKER_DB_URL",
        "_ISOLATED_WORKER_REDIS_URL",
        "_ISOLATED_WORKER_LLM_KEY",
        # Signing key so the worker can verify the envelope's HMAC
        "ISOLATED_EXECUTION_SIGNING_KEY",
    }
)


class LocalSubprocessHealthCheck(RunnerHealthCheck):
    """Health check: verify that the Python interpreter can import the entrypoint."""

    @property
    def runner_type(self) -> str:
        return RunnerType.LOCAL.value

    async def check(self) -> HealthStatus:
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import app.execution_environment.worker_entrypoint; print('ok')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return HealthStatus(
                    healthy=False,
                    runner_type=self.runner_type,
                    message="Worker entrypoint import timed out",
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
            healthy = proc.returncode == 0
            return HealthStatus(
                healthy=healthy,
                runner_type=self.runner_type,
                message="" if healthy else "Worker entrypoint not importable",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return HealthStatus(
                healthy=False,
                runner_type=self.runner_type,
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the entire process group to prevent orphaned grandchildren."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # already exited
    except Exception as exc:
        logger.warning("process_group_kill_failed pid=%s error=%s", proc.pid, exc)
        # Fall back to killing only the direct subprocess
        import contextlib

        with contextlib.suppress(Exception):
            proc.kill()


class LocalSubprocessRunner(BaseRunner):
    """Subprocess-based local runner for OS-level process isolation."""

    def __init__(self) -> None:
        self._health = LocalSubprocessHealthCheck()
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancellations: dict[str, CodeCancellationReceipt] = {}

    @property
    def runner_type(self) -> str:
        return RunnerType.LOCAL.value

    @property
    def health_check(self) -> RunnerHealthCheck:
        return self._health

    async def cancel(self, workload_id: str, reason: str) -> CodeCancellationReceipt:
        del reason
        cached = self._cancellations.get(workload_id)
        if cached is not None:
            return cached
        requested = datetime.now(UTC)
        proc = self._processes.get(workload_id)
        terminated = proc is None or proc.returncode is not None
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except TimeoutError:
                _kill_process_group(proc)
                await proc.wait()
            terminated = proc.returncode is not None
        receipt = CodeCancellationReceipt(
            workload_id=workload_id,
            requested_at=requested,
            acknowledged_at=datetime.now(UTC),
            process_group_terminated=terminated,
            cleanup_state="complete" if terminated else "quarantined",
        )
        self._cancellations[workload_id] = receipt
        return receipt

    async def run(
        self,
        request: ExecutionRequest,
        event_callback: Any | None = None,
    ) -> ExecutionResult:
        envelope = request.envelope
        capsule_id = f"local-{uuid.uuid4().hex[:12]}"
        t_start = time.monotonic()
        timeout = envelope.policy.resource_limits.wall_clock_seconds
        max_output = envelope.policy.resource_limits.output_bytes

        # --- Build clean, restricted environment ---
        clean_env: dict[str, str] = {}
        for key in _ALLOWED_ENV_KEYS:
            val = os.environ.get(key, "")
            if val:
                clean_env[key] = val

        # Serialise the envelope (without scoped credentials in the blob)
        # so the worker gets all policy/context data via the structured envelope.
        envelope_payload = envelope.to_dict()
        # The to_dict() already excludes scoped_* credentials.
        # We pass the LLM key separately via a dedicated env var.
        clean_env["_ISOLATED_WORKER_ENVELOPE"] = _encode_envelope(envelope_payload)

        # Scoped credentials — injected as named env vars only (not in blob)
        if envelope.scoped_db_url:
            clean_env["_ISOLATED_WORKER_DB_URL"] = envelope.scoped_db_url
        if envelope.scoped_redis_prefix:
            # Pass the base Redis URL + the prefix in the envelope blob only.
            # Do NOT read REDIS_URL from the parent env here — that would bypass
            # the allowlist.  The parent must provide the URL via scoped_db_url
            # or a similar dedicated mechanism.
            clean_env["_ISOLATED_WORKER_REDIS_URL"] = envelope.scoped_redis_prefix
        if envelope.scoped_llm_api_key:
            # Pass ONLY via the dedicated env var — do NOT also include in the
            # envelope JSON blob to minimise /proc/<pid>/environ exposure.
            clean_env["_ISOLATED_WORKER_LLM_KEY"] = envelope.scoped_llm_api_key

        module = (
            "app.execution_environment.code_worker"
            if envelope.execution_kind is ExecutionKind.CODE_INTERPRETER
            else "app.execution_environment.worker_entrypoint"
        )
        package_root = str(Path(__file__).resolve().parents[2])
        isolated_bootstrap = (
            "import runpy,sys;"
            f"sys.path.insert(0,{package_root!r});"
            f"runpy.run_module({module!r},run_name='__main__')"
        )
        cmd = [sys.executable, "-I", "-c", isolated_bootstrap]
        workload_id = (
            envelope.code_workload.workload_id
            if envelope.code_workload is not None
            else envelope.attempt_id
        )

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=clean_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # New session — process group leader; killed via killpg on timeout
                start_new_session=True,
            )
            self._processes[workload_id] = proc

            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []
            total_out = 0
            resource_limit_hit = False

            async def _read_stdout() -> None:
                nonlocal total_out, resource_limit_hit
                assert proc is not None
                assert proc.stdout is not None
                async for line in proc.stdout:
                    total_out += len(line)
                    if total_out > max_output:
                        resource_limit_hit = True
                        logger.warning(
                            "isolated_runner_output_limit_hit capsule=%s limit=%d",
                            capsule_id,
                            max_output,
                        )
                        _kill_process_group(proc)
                        return
                    stdout_chunks.append(line)
                    if event_callback is not None:
                        _try_forward_event(line, event_callback)

            async def _read_stderr() -> None:
                assert proc is not None
                assert proc.stderr is not None
                async for chunk in proc.stderr:
                    stderr_chunks.append(chunk)

            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr()),
                timeout=float(timeout) if timeout > 0 else None,
            )
            await proc.wait()
            exit_code = proc.returncode or 0

            # Log stderr for observability — critical for debugging worker failures
            stderr_text = b"".join(stderr_chunks).decode(errors="replace").strip()
            if stderr_text:
                logger.warning(
                    "isolated_worker_stderr capsule=%s exit=%d\n%s",
                    capsule_id,
                    exit_code,
                    stderr_text[:2000],
                )

            if resource_limit_hit:
                await proc.wait()
                return ExecutionResult(
                    goal_id=envelope.goal_id,
                    tenant_id=envelope.tenant_id,
                    attempt_id=envelope.attempt_id,
                    success=False,
                    status="failed",
                    failure_reason=ExecutionFailureReason.RESOURCE_LIMIT,
                    error_message=f"Output size limit ({max_output} bytes) exceeded.",
                    runner_type=self.runner_type,
                    capsule_id=capsule_id,
                    resource_limit_hit=True,
                    exit_code=exit_code,
                    execution_time_ms=(time.monotonic() - t_start) * 1000,
                )

        except TimeoutError:
            if proc is not None:
                _kill_process_group(proc)
                import contextlib

                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
            logger.warning(
                "isolated_runner_timeout capsule=%s timeout=%ds",
                capsule_id,
                timeout,
            )
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=False,
                status="failed",
                failure_reason=ExecutionFailureReason.TIMEOUT,
                error_message=f"Execution timed out after {timeout}s.",
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                timeout_hit=True,
                execution_time_ms=(time.monotonic() - t_start) * 1000,
            )
        except Exception as exc:
            logger.exception("isolated_runner_unexpected_error capsule=%s", capsule_id)
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=False,
                status="failed",
                failure_reason=ExecutionFailureReason.INTERNAL_ERROR,
                error_message=str(exc),
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                execution_time_ms=(time.monotonic() - t_start) * 1000,
            )

        # --- Parse final result from last _result line on stdout ---
        result_dict: dict[str, Any] = {}
        for line in reversed(stdout_chunks):
            stripped = line.strip()
            if stripped:
                try:
                    candidate = json.loads(stripped)
                    if isinstance(candidate, dict) and candidate.get("_result"):
                        result_dict = candidate
                        break
                except Exception:
                    pass

        success = bool(result_dict.get("success", exit_code == 0))
        return ExecutionResult(
            goal_id=envelope.goal_id,
            tenant_id=envelope.tenant_id,
            attempt_id=envelope.attempt_id,
            success=success,
            status=str(result_dict.get("status", "complete" if success else "failed")),
            iterations=int(result_dict.get("iterations", 0)),
            plan=list(result_dict.get("plan", [])),
            steps=list(result_dict.get("steps", [])),
            verification_feedback=str(result_dict.get("verification_feedback", "")),
            error_message=str(result_dict.get("error_message", "")),
            runner_type=self.runner_type,
            capsule_id=capsule_id,
            exit_code=exit_code if proc is not None else None,
            execution_time_ms=(time.monotonic() - t_start) * 1000,
            code_observation=(
                __import__(
                    "app.execution_environment.models",
                    fromlist=["CodeExecutionObservation"],
                ).CodeExecutionObservation.model_validate(result_dict["code_observation"])
                if isinstance(result_dict.get("code_observation"), dict)
                else None
            ),
        )


def _encode_envelope(payload: dict[str, Any]) -> str:
    """Base64-encode the envelope payload for passing as an env var."""
    import base64

    return base64.b64encode(json.dumps(payload).encode()).decode()


def _try_forward_event(line: bytes, callback: Any) -> None:
    """Parse a JSON line and schedule forwarding to the event callback.

    Errors are logged but never propagate — a broken event must not kill
    the stdout read loop.
    """
    try:
        stripped = line.strip()
        if not stripped:
            return
        evt = json.loads(stripped)
        if not isinstance(evt, dict) or "_result" in evt or "_log" in evt:
            return  # Skip result lines and internal log lines
        if "type" not in evt:
            return
        # Schedule the coroutine fire-and-forget — we're inside an async context.
        # The task reference is intentionally not stored; event delivery is
        # best-effort and must not block the stdout reader.
        _t = asyncio.ensure_future(callback(evt))
        _t.add_done_callback(
            lambda t: (
                logger.debug("isolated_event_forward_error: %s", t.exception())
                if not t.cancelled() and t.exception()
                else None
            )
        )
    except Exception as exc:
        logger.debug("isolated_event_forward_error: %s", exc)
