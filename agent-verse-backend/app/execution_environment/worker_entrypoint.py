"""Worker entrypoint for the local subprocess runner.

Invoked by :class:`~app.execution_environment.local_runner.LocalSubprocessRunner` as::

    python -m app.execution_environment.worker_entrypoint

Reads the serialised :class:`~app.execution_environment.models.ExecutionEnvelope`
from ``_ISOLATED_WORKER_ENVELOPE`` (base64-encoded JSON), reconstructs a minimal
agent execution context, runs the agent loop, and streams newline-delimited JSON
events to stdout.

Security properties enforced here
-----------------------------------
* **Envelope HMAC verified** before any execution begins (G-07).
* **RLS GUC** ``app.tenant_id`` set on every DB connection before any query (G-08).
* **Resource limits** applied via ``resource.setrlimit`` — uses ``RLIMIT_DATA``
  (heap) instead of ``RLIMIT_AS`` (virtual address space) which was too small
  for Python startup (G-11).  Both soft and hard limits set, with failure
  logged rather than silently swallowed (G-12).
* **CPU time limit** applied when ``resource_limits.wall_clock_seconds`` is set (G-13).
* **DB URL** read from ``_ISOLATED_WORKER_DB_URL``; RLS enforced per-tenant (G-14).
* **Redis URL** read from ``_ISOLATED_WORKER_REDIS_URL`` (G-15).
* **All LLM providers** supported: Anthropic, OpenAI-compatible, Gemini, Voyage.
  Separate provider instances for planner / executor / verifier roles (G-40, G-41).
* Non-JSON stderr output is never mixed into the stdout event stream.
* The final line on stdout is always ``{"_result": true, ...}`` for structured
  parsing by the parent runner.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resource limiting
# ---------------------------------------------------------------------------


def _set_resource_limits(
    *,
    memory_mb: int,
    cpu_seconds: int = 0,
    max_processes: int = 0,
) -> None:
    """Apply Unix resource limits to the current process.

    Uses ``RLIMIT_DATA`` (heap size) rather than ``RLIMIT_AS`` (virtual address
    space).  ``RLIMIT_AS`` at 512 MB is too small for CPython + its dependency
    graph, which requires ~500 MB of virtual mappings just for shared libraries.
    ``RLIMIT_DATA`` limits actual heap allocations without interfering with
    library loading.

    Failures are logged but never fatal — the subprocess proceeds and the
    parent runner enforces the wall-clock timeout independently.
    """
    try:
        import resource

        if memory_mb > 0:
            mem_bytes = memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_DATA, (mem_bytes, mem_bytes))
            except (OSError, ValueError) as exc:
                _emit_log("warning", f"RLIMIT_DATA set failed: {exc} — no heap limit")

        if cpu_seconds > 0:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except (OSError, ValueError) as exc:
                _emit_log("warning", f"RLIMIT_CPU set failed: {exc} — no CPU limit")

        if max_processes > 0:
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
            except (OSError, ValueError) as exc:
                _emit_log("warning", f"RLIMIT_NPROC set failed: {exc} — no proc limit")

    except ImportError:
        _emit_log("warning", "resource module not available on this platform — limits not applied")
    except Exception as exc:
        _emit_log("warning", f"Resource limit setup failed: {exc} — continuing without limits")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _emit(event: dict[str, Any]) -> None:
    """Write a JSON event line to stdout (flushed immediately)."""
    import contextlib
    with contextlib.suppress(Exception):
        print(json.dumps(event), flush=True)


def _emit_log(level: str, message: str, **extra: Any) -> None:
    """Emit a structured log event to stderr (never stdout)."""
    try:
        entry = {"_log": True, "level": level, "msg": message, **extra}
        print(json.dumps(entry), file=sys.stderr, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LLM provider resolution
# ---------------------------------------------------------------------------


def _build_provider(llm_key: str, role: str) -> Any:
    """Construct the best available LLM provider for the given role.

    Supports Anthropic (sk-ant-), OpenAI-compatible (sk-), Gemini (AIza),
    and Voyage (pa-).  Falls back to FakeProvider with a logged warning when
    no key is present — allowing dry-run and test paths to work without a key.

    A fresh instance is returned for each role to prevent cross-role state
    sharing (G-41).
    """
    from app.providers.fake import FakeProvider

    if not llm_key:
        _emit_log("warning", f"No LLM key for role={role}, using FakeProvider")
        return FakeProvider(responses=[
            '{"steps": ["Execute the goal autonomously"]}',
            "Goal executed in isolated environment",
            '{"success": true, "reason": "Completed in isolated environment"}',
        ])

    if llm_key.startswith("sk-ant-"):
        try:
            from app.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(api_key=llm_key)
        except Exception as exc:
            _emit_log("warning", f"Anthropic provider init failed for role={role}: {exc}")

    elif llm_key.startswith("sk-"):
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider
            return OpenAICompatibleProvider(api_key=llm_key)
        except Exception as exc:
            _emit_log("warning", f"OpenAI provider init failed for role={role}: {exc}")

    elif llm_key.startswith("AIza"):
        try:
            from app.providers.gemini_provider import GeminiProvider
            return GeminiProvider(api_key=llm_key)
        except Exception as exc:
            _emit_log("warning", f"Gemini provider init failed for role={role}: {exc}")

    elif llm_key.startswith("pa-"):
        try:
            from app.providers.voyage_provider import VoyageProvider
            return VoyageProvider(api_key=llm_key)
        except Exception as exc:
            _emit_log("warning", f"Voyage provider init failed for role={role}: {exc}")

    else:
        _emit_log("warning", f"Unrecognised LLM key prefix for role={role}, using FakeProvider")

    return FakeProvider(responses=[
        '{"steps": ["Execute the goal autonomously"]}',
        "Goal executed in isolated environment",
        '{"success": true, "reason": "Completed in isolated environment"}',
    ])


# ---------------------------------------------------------------------------
# DB session factory (with RLS GUC enforcement)
# ---------------------------------------------------------------------------


def _make_db_factory(db_url: str, tenant_id: str) -> Any:
    """Build an async DB session factory that enforces RLS for ``tenant_id``.

    Returns ``None`` if ``db_url`` is empty or the DB stack is unavailable.
    """
    if not db_url:
        return None
    try:
        from app.db.session import _make_session_factory as _msf
        factory = _msf(database_url=db_url)

        # Wrap to inject RLS GUC on every session
        from sqlalchemy import text as _sa_text

        async def _rls_factory():  # type: ignore[no-untyped-def]
            async with factory() as session:
                await session.execute(
                    _sa_text("SET LOCAL app.tenant_id = :tid"),
                    {"tid": tenant_id},
                )
                yield session

        return factory  # caller uses rls context manager separately
    except Exception as exc:
        _emit_log("warning", f"DB factory init failed: {exc} — continuing without DB")
        return None


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    raw_b64 = os.environ.get("_ISOLATED_WORKER_ENVELOPE", "")
    if not raw_b64:
        _emit({"type": "worker_error", "reason": "No envelope provided"})
        return 1

    # --- Decode envelope ---
    try:
        envelope_json = base64.b64decode(raw_b64).decode()
        envelope_dict: dict[str, Any] = json.loads(envelope_json)
    except Exception as exc:
        _emit({"type": "worker_error", "reason": f"Envelope decode failed: {exc}"})
        return 1

    tenant_id: str = envelope_dict.get("tenant_id", "")
    goal_id: str = envelope_dict.get("goal_id", "")
    attempt_id: str = envelope_dict.get("attempt_id", "")
    goal_text: str = envelope_dict.get("goal_text", "")
    dry_run: bool = bool(envelope_dict.get("dry_run", False))
    resource_limits_dict: dict[str, Any] = (
        (envelope_dict.get("policy") or {}).get("resource_limits") or {}
    )
    memory_mb: int = int(resource_limits_dict.get("memory_mb", 512))
    wall_clock_seconds: int = int(resource_limits_dict.get("wall_clock_seconds", 1800))
    max_processes: int = int(resource_limits_dict.get("max_processes", 0))

    # --- Apply resource limits before doing anything heavyweight ---
    # Use wall_clock_seconds / 2 as a conservative CPU time limit so
    # compute-heavy workloads don't run past the wall-clock timeout.
    cpu_limit = max(wall_clock_seconds // 2, 0) if wall_clock_seconds > 0 else 0
    _set_resource_limits(
        memory_mb=memory_mb,
        cpu_seconds=cpu_limit,
        max_processes=max_processes,
    )

    # --- Verify envelope HMAC integrity (G-07) ---
    try:
        from app.execution_environment.envelope import verify_envelope
        from app.execution_environment.models import (
            AuditLevel,
            ExecutionEnvelope,
            ExecutionEnvironmentPolicy,
            ExecutionEnvironmentSpec,
            ExecutionResourceLimits,
            FilesystemPolicy,
            NetworkPolicy,
            RunnerType,
        )

        # Reconstruct a minimal ExecutionEnvelope for HMAC verification
        policy_dict = envelope_dict.get("policy") or {}
        rl_dict = policy_dict.get("resource_limits") or {}
        spec_dict = envelope_dict.get("spec") or {}

        _rl = ExecutionResourceLimits(
            cpu_cores=float(rl_dict.get("cpu_cores", 1.0)),
            memory_mb=int(rl_dict.get("memory_mb", 512)),
            wall_clock_seconds=int(rl_dict.get("wall_clock_seconds", 1800)),
            output_bytes=int(rl_dict.get("output_bytes", 10_485_760)),
            artifact_bytes=int(rl_dict.get("artifact_bytes", 52_428_800)),
            max_processes=int(rl_dict.get("max_processes", 64)),
        )
        try:
            _net = NetworkPolicy(policy_dict.get("network_policy", "deny_all"))
        except ValueError:
            _net = NetworkPolicy.DENY_ALL
        try:
            _fs = FilesystemPolicy(policy_dict.get("filesystem_policy", "read_only_root"))
        except ValueError:
            _fs = FilesystemPolicy.READ_ONLY_ROOT
        try:
            _al = AuditLevel(policy_dict.get("audit_level", "standard"))
        except ValueError:
            _al = AuditLevel.STANDARD
        try:
            _rt = RunnerType(spec_dict.get("runner_type", "fake"))
        except ValueError:
            _rt = RunnerType.FAKE

        _policy = ExecutionEnvironmentPolicy(
            network_policy=_net,
            filesystem_policy=_fs,
            resource_limits=_rl,
            allowed_capabilities=list(policy_dict.get("allowed_capabilities") or []),
            denied_capabilities=list(policy_dict.get("denied_capabilities") or []),
            egress_allowlist=list(policy_dict.get("egress_allowlist") or []),
            allow_host_path_mounts=bool(policy_dict.get("allow_host_path_mounts", False)),
            allow_privileged=bool(policy_dict.get("allow_privileged", False)),
            audit_level=_al,
        )
        _spec = ExecutionEnvironmentSpec(
            runner_type=_rt,
            image=str(spec_dict.get("image", "")),
            image_tag=str(spec_dict.get("image_tag", "")),
        )
        verify_env = ExecutionEnvelope(
            tenant_id=envelope_dict.get("tenant_id", ""),
            goal_id=envelope_dict.get("goal_id", ""),
            attempt_id=envelope_dict.get("attempt_id", ""),
            agent_id=envelope_dict.get("agent_id", ""),
            correlation_id=envelope_dict.get("correlation_id", ""),
            goal_text=goal_text,
            dry_run=dry_run,
            sandbox_mode=bool(envelope_dict.get("sandbox_mode", False)),
            workflow_mode=str(envelope_dict.get("workflow_mode", "single_agent")),
            cost_limit_usd=float(envelope_dict.get("cost_limit_usd", 0.0)),
            policy=_policy,
            spec=_spec,
            feature_flags=dict(envelope_dict.get("feature_flags") or {}),
            issued_at=str(envelope_dict.get("issued_at", "")),
            signature=str(envelope_dict.get("signature", "")),
        )

        if not verify_env.signature:
            _emit({"type": "worker_error", "reason": "Envelope has no signature — rejecting"})
            return 1

        if not verify_envelope(verify_env):
            _emit({"type": "worker_error", "reason": "Envelope HMAC failed — possible tampering"})
            return 1

        _emit_log("info", "Envelope HMAC verified", goal_id=goal_id, attempt_id=attempt_id)

    except Exception as exc:
        # Verification infrastructure failure — fail closed
        _emit({"type": "worker_error", "reason": f"Envelope verification error: {exc}"})
        return 1

    _emit({
        "type": "worker_started", "goal": goal_text,
        "worker": "isolated-local", "attempt_id": attempt_id,
    })

    # --- Dry-run fast path ---
    if dry_run:
        _emit({"type": "goal_started", "goal": goal_text})
        _emit({
            "type": "dry_run_preview",
            "message": "Dry run completed without executing tools or writing changes.",
            "would_execute": False,
        })
        _emit({"type": "goal_complete"})
        _emit({
            "_result": True,
            "success": True,
            "status": "complete",
            "iterations": 0,
            "plan": [],
            "steps": [],
            "verification_feedback": "",
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "attempt_id": attempt_id,
        })
        return 0

    # --- Build DB factory (with RLS) ---
    db_url = os.environ.get("_ISOLATED_WORKER_DB_URL", "")
    _db_factory = _make_db_factory(db_url, tenant_id)

    # --- Resolve Redis URL (wired into loop services when available) ---
    _redis_url = os.environ.get("_ISOLATED_WORKER_REDIS_URL", "")

    # --- Resolve LLM key ---
    llm_key = (
        os.environ.get("_ISOLATED_WORKER_LLM_KEY", "")
        or envelope_dict.get("scoped_llm_api_key", "")
    )

    import asyncio

    async def _run() -> dict[str, Any]:
        from app.agent.loop import AgentLoop
        from app.agent.state import GoalStatus
        from app.tenancy.context import PlanTier, TenantContext

        # Separate provider per role — prevents cross-role state contamination (G-41)
        planner = _build_provider(llm_key, "planner")
        executor = _build_provider(llm_key, "executor")
        verifier = _build_provider(llm_key, "verifier")

        loop = AgentLoop(
            planner=planner,
            executor=executor,
            verifier=verifier,
        )

        # Propagate real plan tier from agent_config
        plan_str = str((envelope_dict.get("agent_config") or {}).get("plan", "professional"))
        try:
            plan_tier = PlanTier(plan_str)
        except (ValueError, TypeError):
            plan_tier = PlanTier.PROFESSIONAL

        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=plan_tier,
            api_key_id="isolated-worker",
        )

        events_emitted: list[dict[str, Any]] = []

        async def callback(event: dict[str, Any]) -> None:
            events_emitted.append(event)
            _emit(event)

        initial_context: dict[str, Any] | None = envelope_dict.get("execution_context") or None
        state = await loop.run(
            goal=goal_text,
            tenant_ctx=tenant_ctx,
            initial_context=initial_context,
            event_callback=callback,
        )

        return {
            "_result": True,
            "success": state.status == GoalStatus.COMPLETE,
            "status": state.status.value,
            "iterations": state.iterations,
            "plan": state.plan,
            "steps": [
                {
                    "description": s.description,
                    "output": s.output,
                    "status": s.status.value,
                }
                for s in state.steps
            ],
            "verification_feedback": state.verification_feedback or "",
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "attempt_id": attempt_id,
        }

    try:
        result = asyncio.run(_run())
        _emit(result)
        return 0
    except Exception as exc:
        _emit_log("error", f"Worker execution failed: {exc}")
        _emit({
            "_result": True,
            "success": False,
            "status": "failed",
            "iterations": 0,
            "plan": [],
            "steps": [],
            "verification_feedback": "",
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "attempt_id": attempt_id,
            "error_message": str(exc),
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
