"""Envelope builder and HMAC integrity verification.

The control plane signs the :class:`ExecutionEnvelope` before dispatching it
to an isolated runner.  The runner verifies the signature to ensure the
envelope has not been tampered with in transit.

Signing key
-----------
``ISOLATED_EXECUTION_SIGNING_KEY`` env var — **MUST be set in production**.
Minimum 32 characters.  The module emits a logged warning in development
and a hard startup error in production when the key is absent or too short.

Canonical fields covered by the HMAC
-------------------------------------
ALL security-relevant fields are included:
  - Identity: tenant_id, goal_id, attempt_id, agent_id, issued_at
  - Goal: goal_text, dry_run, sandbox_mode, workflow_mode, cost_limit_usd
  - Policy: the full policy dict (network, filesystem, resource limits,
    allowed/denied capabilities, egress allow-list, audit level, flags)
  - Spec: runner_type, image, image_tag
  - Feature flags snapshot

Fields intentionally EXCLUDED from canonical bytes (they are NOT
security-relevant or are set after signing):
  - execution_context, agent_config, runtime_profile, tool_context — these
    are operator-controlled inputs, not security-policy fields.  Include them
    if your threat model requires it.
  - scoped_* credentials — intentionally excluded everywhere.
  - correlation_id, signature (would create circular dependency).

Expiry / replay protection
--------------------------
``verify_envelope()`` enforces a configurable TTL (default 3600 seconds).
The ``issued_at`` ISO-8601 timestamp is included in the canonical bytes, so
a replayed envelope with a modified ``issued_at`` fails verification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.execution_environment.models import (
    AuditLevel,
    CodeExecutionWorkload,
    ExecutionEnvelope,
    ExecutionEnvironmentPolicy,
    ExecutionEnvironmentSpec,
    ExecutionKind,
    ExecutionResourceLimits,
    FilesystemPolicy,
    NetworkPolicy,
    RunnerType,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)

_DEV_ONLY_SIGNING_KEY = "agentverse-isolated-dev-key-change-in-prod"
_MIN_KEY_LENGTH = 32
_DEFAULT_ENVELOPE_TTL_SECONDS = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def _get_signing_key() -> bytes:
    """Return the signing key bytes, enforcing production requirements.

    Raises:
        RuntimeError: In production environments when the key is absent or
            shorter than ``_MIN_KEY_LENGTH`` characters.
    """
    env_key = os.environ.get("ISOLATED_EXECUTION_SIGNING_KEY", "")
    environment = os.environ.get("ENVIRONMENT", "development").lower()
    is_production = environment == "production"

    if not env_key:
        if is_production:
            raise RuntimeError(
                "SECURITY: ISOLATED_EXECUTION_SIGNING_KEY must be set in production. "
                "Generate a strong key: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        logger.warning(
            "isolated_execution_signing_key_missing",
            message=(
                "ISOLATED_EXECUTION_SIGNING_KEY not set. "
                "Using insecure dev-only key. This MUST be overridden in production."
            ),
        )
        return _DEV_ONLY_SIGNING_KEY.encode()

    if len(env_key) < _MIN_KEY_LENGTH:
        if is_production:
            raise RuntimeError(
                f"SECURITY: ISOLATED_EXECUTION_SIGNING_KEY must be at least "
                f"{_MIN_KEY_LENGTH} characters. Got {len(env_key)}."
            )
        logger.warning(
            "isolated_execution_signing_key_too_short",
            length=len(env_key),
            minimum=_MIN_KEY_LENGTH,
        )

    return env_key.encode()


# ---------------------------------------------------------------------------
# Canonical representation
# ---------------------------------------------------------------------------


def _canonical_bytes(envelope: ExecutionEnvelope) -> bytes:
    """Return stable bytes over ALL security-relevant envelope fields.

    Includes: identity, goal, policy (network + filesystem + resource limits +
    capabilities + egress + audit level), spec (runner type + image), and
    feature flags.  Changes to any of these fields invalidate the signature.
    """
    canonical: dict[str, Any] = {
        # Identity
        "tenant_id": envelope.tenant_id,
        "goal_id": envelope.goal_id,
        "attempt_id": envelope.attempt_id,
        "agent_id": envelope.agent_id,
        "issued_at": envelope.issued_at,
        "execution_kind": envelope.execution_kind.value,
        "code_workload": (
            envelope.code_workload.model_dump(mode="json")
            if envelope.code_workload is not None
            else None
        ),
        # Goal
        "goal_text": envelope.goal_text,
        "dry_run": envelope.dry_run,
        "sandbox_mode": envelope.sandbox_mode,
        "workflow_mode": envelope.workflow_mode,
        "cost_limit_usd": envelope.cost_limit_usd,
        # Full policy — prevents in-transit escalation of any policy field
        "policy": {
            "network_policy": str(envelope.policy.network_policy),
            "filesystem_policy": str(envelope.policy.filesystem_policy),
            "resource_limits": envelope.policy.resource_limits.to_dict(),
            "allowed_capabilities": sorted(envelope.policy.allowed_capabilities),
            "denied_capabilities": sorted(envelope.policy.denied_capabilities),
            "egress_allowlist": sorted(envelope.policy.egress_allowlist),
            "allow_host_path_mounts": envelope.policy.allow_host_path_mounts,
            "allow_privileged": envelope.policy.allow_privileged,
            "audit_level": str(envelope.policy.audit_level),
        },
        # Spec — prevents runner-type substitution
        "spec": {
            "runner_type": str(envelope.spec.runner_type),
            "image": envelope.spec.image,
            "image_tag": envelope.spec.image_tag,
        },
        # Feature flags — prevents flag-state tampering
        "feature_flags": dict(sorted(envelope.feature_flags.items())),
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------


def sign_envelope(envelope: ExecutionEnvelope) -> ExecutionEnvelope:
    """Compute and set the HMAC-SHA256 signature on the envelope in-place."""
    raw = _canonical_bytes(envelope)
    envelope.signature = hmac.new(_get_signing_key(), raw, hashlib.sha256).hexdigest()
    return envelope


def verify_envelope(
    envelope: ExecutionEnvelope,
    *,
    max_age_seconds: int = _DEFAULT_ENVELOPE_TTL_SECONDS,
) -> bool:
    """Return True if the envelope signature is valid AND not expired.

    Args:
        envelope: The envelope to verify.
        max_age_seconds: Maximum allowed age of ``issued_at``.  Defaults to
            3600 s.  Set to 0 to skip the age check (not recommended in prod).

    Returns:
        False when: signature is missing, signature is wrong, or envelope is
        older than ``max_age_seconds``.
    """
    if not envelope.signature:
        return False

    # HMAC integrity check
    raw = _canonical_bytes(envelope)
    expected = hmac.new(_get_signing_key(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope.signature):
        return False

    # Expiry check (replay protection)
    if max_age_seconds > 0 and envelope.issued_at:
        try:
            issued = datetime.fromisoformat(envelope.issued_at)
            # Ensure timezone-aware comparison
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=UTC)
            age = datetime.now(UTC) - issued
            if age > timedelta(seconds=max_age_seconds):
                logger.warning(
                    "isolated_envelope_expired",
                    goal_id=envelope.goal_id,
                    age_seconds=age.total_seconds(),
                    max_age_seconds=max_age_seconds,
                )
                return False
        except (ValueError, OverflowError):
            # Unparseable issued_at — treat as expired
            return False

    return True


# ---------------------------------------------------------------------------
# Envelope builder
# ---------------------------------------------------------------------------


def build_envelope(
    *,
    tenant_id: str,
    goal_id: str,
    goal_text: str = "",
    execution_kind: ExecutionKind = ExecutionKind.AGENT_GOAL,
    code_workload: CodeExecutionWorkload | None = None,
    agent_id: str = "",
    execution_context: dict[str, Any] | None = None,
    agent_config: dict[str, Any] | None = None,
    runtime_profile: dict[str, Any] | None = None,
    tool_context: dict[str, Any] | None = None,
    dry_run: bool = False,
    sandbox_mode: bool = False,
    workflow_mode: str = "single_agent",
    priority: str = "normal",
    hitl_state: dict[str, Any] | None = None,
    cost_limit_usd: float = 0.0,
    feature_flags: dict[str, bool] | None = None,
    resource_limits: ExecutionResourceLimits | None = None,
    runner_type: RunnerType = RunnerType.FAKE,
    # Scoped credentials — must be provided by caller, never defaults
    scoped_llm_api_key: str = "",
    scoped_db_url: str = "",
    scoped_redis_prefix: str = "",
) -> ExecutionEnvelope:
    """Construct, sign, and return a complete :class:`ExecutionEnvelope`."""
    policy = ExecutionEnvironmentPolicy(
        network_policy=NetworkPolicy.DENY_ALL,
        filesystem_policy=FilesystemPolicy.READ_ONLY_ROOT,
        resource_limits=resource_limits or ExecutionResourceLimits(),
        audit_level=AuditLevel.STANDARD,
    )
    spec = ExecutionEnvironmentSpec(runner_type=runner_type)

    envelope = ExecutionEnvelope(
        tenant_id=tenant_id,
        goal_id=goal_id,
        attempt_id=uuid.uuid4().hex,
        agent_id=agent_id,
        correlation_id=uuid.uuid4().hex,
        execution_kind=execution_kind,
        code_workload=code_workload,
        goal_text=goal_text,
        execution_context=execution_context or {},
        agent_config=agent_config or {},
        runtime_profile=runtime_profile or {},
        tool_context=tool_context or {},
        dry_run=dry_run,
        sandbox_mode=sandbox_mode,
        workflow_mode=workflow_mode,
        priority=priority,
        policy=policy,
        spec=spec,
        hitl_state=hitl_state or {},
        cost_limit_usd=cost_limit_usd,
        feature_flags=feature_flags or {},
        scoped_llm_api_key=scoped_llm_api_key,
        scoped_db_url=scoped_db_url,
        scoped_redis_prefix=scoped_redis_prefix,
    )
    return sign_envelope(envelope)


def envelope_from_dict(data: dict[str, Any]) -> ExecutionEnvelope:
    """Reconstruct a signed envelope at the worker trust boundary."""
    policy_data = dict(data.get("policy") or {})
    limit_data = dict(policy_data.get("resource_limits") or {})
    spec_data = dict(data.get("spec") or {})
    workload_data = data.get("code_workload")
    return ExecutionEnvelope(
        tenant_id=str(data.get("tenant_id", "")),
        goal_id=str(data.get("goal_id", "")),
        attempt_id=str(data.get("attempt_id", "")),
        agent_id=str(data.get("agent_id", "")),
        correlation_id=str(data.get("correlation_id", "")),
        execution_kind=ExecutionKind(str(data.get("execution_kind", "agent_goal"))),
        code_workload=(
            CodeExecutionWorkload.model_validate(workload_data)
            if isinstance(workload_data, dict)
            else None
        ),
        goal_text=str(data.get("goal_text", "")),
        execution_context=dict(data.get("execution_context") or {}),
        agent_config=dict(data.get("agent_config") or {}),
        runtime_profile=dict(data.get("runtime_profile") or {}),
        tool_context=dict(data.get("tool_context") or {}),
        dry_run=bool(data.get("dry_run", False)),
        sandbox_mode=bool(data.get("sandbox_mode", False)),
        workflow_mode=str(data.get("workflow_mode", "single_agent")),
        priority=str(data.get("priority", "normal")),
        policy=ExecutionEnvironmentPolicy(
            network_policy=NetworkPolicy(policy_data.get("network_policy", "deny_all")),
            filesystem_policy=FilesystemPolicy(
                policy_data.get("filesystem_policy", "read_only_root")
            ),
            resource_limits=ExecutionResourceLimits(
                cpu_cores=float(limit_data.get("cpu_cores", 1.0)),
                memory_mb=int(limit_data.get("memory_mb", 512)),
                wall_clock_seconds=int(limit_data.get("wall_clock_seconds", 1800)),
                output_bytes=int(limit_data.get("output_bytes", 10_485_760)),
                artifact_bytes=int(limit_data.get("artifact_bytes", 52_428_800)),
                max_processes=int(limit_data.get("max_processes", 64)),
            ),
            allowed_capabilities=list(policy_data.get("allowed_capabilities") or []),
            denied_capabilities=list(policy_data.get("denied_capabilities") or []),
            egress_allowlist=list(policy_data.get("egress_allowlist") or []),
            allow_host_path_mounts=bool(policy_data.get("allow_host_path_mounts", False)),
            allow_privileged=bool(policy_data.get("allow_privileged", False)),
            audit_level=AuditLevel(policy_data.get("audit_level", "standard")),
        ),
        spec=ExecutionEnvironmentSpec(
            runner_type=RunnerType(spec_data.get("runner_type", "fake")),
            image=str(spec_data.get("image", "")),
            image_tag=str(spec_data.get("image_tag", "")),
            labels=dict(spec_data.get("labels") or {}),
        ),
        hitl_state=dict(data.get("hitl_state") or {}),
        cost_limit_usd=float(data.get("cost_limit_usd", 0.0)),
        feature_flags=dict(data.get("feature_flags") or {}),
        issued_at=str(data.get("issued_at", "")),
        signature=str(data.get("signature", "")),
    )


__all__ = [
    "build_envelope",
    "envelope_from_dict",
    "sign_envelope",
    "verify_envelope",
]
