"""Execution-environment policy evaluation.

Determines whether an :class:`ExecutionEnvelope` is safe to dispatch given the
current operator configuration.  This is an additive gate — it does NOT replace
any existing GovernancePolicy, GuardrailEngine, or HITLGateway checks, which
run independently in the control plane before the envelope is built.

Rules evaluated (fail-closed — first matching DENY wins):
1. Privileged mode always denied.
2. Host path mounts always denied.
3. Empty tenant_id denied.
4. Empty goal_id denied.
5. Empty goal_text denied.
6. Resource limit sanity: wall_clock_seconds must be > 0.
7. Resource limit sanity: memory_mb must be > 0.
8. Resource limit upper bounds enforced to prevent resource exhaustion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.models import (
    ExecutionEnvelope,
    ExecutionFailureReason,
    ExecutionKind,
)

logger = logging.getLogger(__name__)

# Hard caps — tenants cannot exceed these even with explicit envelope fields.
_MAX_WALL_CLOCK_SECONDS = 3600 * 6  # 6 hours
_MAX_MEMORY_MB = 16_384  # 16 GiB


@dataclass
class PolicyDecision:
    allowed: bool
    failure_reason: ExecutionFailureReason | None = None
    message: str = ""


def evaluate_policy(envelope: ExecutionEnvelope) -> PolicyDecision:
    """Evaluate isolation-layer policy rules against the envelope."""
    policy = envelope.policy

    if policy.allow_privileged:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message="Privileged execution mode is not permitted.",
        )

    if policy.allow_host_path_mounts:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message="Host path mounts are not permitted.",
        )

    if not envelope.tenant_id:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message="Envelope missing tenant_id.",
        )

    if not envelope.goal_id:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message="Envelope missing goal_id.",
        )

    if envelope.execution_kind is ExecutionKind.AGENT_GOAL and not envelope.goal_text:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message="Envelope missing goal_text.",
        )

    if envelope.execution_kind is ExecutionKind.CODE_INTERPRETER:
        if envelope.goal_text or envelope.code_workload is None:
            return PolicyDecision(
                allowed=False,
                failure_reason=ExecutionFailureReason.POLICY_DENIED,
                message="Code execution payload is missing or ambiguous.",
            )
        violations = CodeWorkloadValidator().validate(envelope.code_workload)
        if violations:
            return PolicyDecision(
                allowed=False,
                failure_reason=ExecutionFailureReason.POLICY_DENIED,
                message="Code policy denied: " + ",".join(item.code for item in violations),
            )

    # Resource limit sanity checks
    rl = policy.resource_limits
    if rl.wall_clock_seconds <= 0:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message=f"resource_limits.wall_clock_seconds must be > 0, got {rl.wall_clock_seconds}.",
        )

    if rl.memory_mb <= 0:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message=f"resource_limits.memory_mb must be > 0, got {rl.memory_mb}.",
        )

    if rl.wall_clock_seconds > _MAX_WALL_CLOCK_SECONDS:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message=(
                f"resource_limits.wall_clock_seconds {rl.wall_clock_seconds} exceeds "
                f"maximum allowed {_MAX_WALL_CLOCK_SECONDS}s."
            ),
        )

    if rl.memory_mb > _MAX_MEMORY_MB:
        return PolicyDecision(
            allowed=False,
            failure_reason=ExecutionFailureReason.POLICY_DENIED,
            message=(
                f"resource_limits.memory_mb {rl.memory_mb} exceeds "
                f"maximum allowed {_MAX_MEMORY_MB} MiB."
            ),
        )

    logger.debug(
        "isolated_policy_allowed tenant=%s goal=%s runner=%s",
        envelope.tenant_id,
        envelope.goal_id,
        envelope.spec.runner_type,
    )
    return PolicyDecision(allowed=True)
