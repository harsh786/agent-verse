"""Execution-environment data contracts.

All types in this module are plain dataclasses / enums — zero external
dependencies — so they can be imported in both the control plane and any
isolated runner without pulling in the full app dependency tree.
"""
from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExecutionFailureReason(enum.StrEnum):
    """Structured failure codes returned by the execution plane."""

    RUNNER_UNAVAILABLE = "runner_unavailable"
    POLICY_DENIED = "policy_denied"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    INTERNAL_ERROR = "internal_error"
    CANCELLED = "cancelled"
    SANDBOX_VIOLATION = "sandbox_violation"


class RunnerType(enum.StrEnum):
    FAKE = "fake"          # In-process fake runner (tests / default)
    LOCAL = "local"        # Subprocess runner
    KUBERNETES = "kubernetes"  # Kubernetes Job runner


class NetworkPolicy(enum.StrEnum):
    DENY_ALL = "deny_all"
    ALLOW_TENANT_ALLOWLIST = "allow_tenant_allowlist"
    ALLOW_INTERNAL_ONLY = "allow_internal_only"


class FilesystemPolicy(enum.StrEnum):
    READ_ONLY_ROOT = "read_only_root"
    EPHEMERAL_WRITABLE = "ephemeral_writable"


class AuditLevel(enum.StrEnum):
    STANDARD = "standard"
    VERBOSE = "verbose"
    MINIMAL = "minimal"


class ExecutionKind(enum.StrEnum):
    AGENT_GOAL = "agent_goal"
    CODE_INTERPRETER = "code_interpreter"


class CodeLanguage(enum.StrEnum):
    PYTHON_3_12 = "python_3_12"


class CodeWorkloadMode(enum.StrEnum):
    PROGRAM_OF_THOUGHT = "program_of_thought"
    CODEACT = "codeact"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _FrozenCodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodeExecutionWorkload(_FrozenCodeModel):
    workload_id: str = Field(min_length=1)
    mode: CodeWorkloadMode
    language: Literal[CodeLanguage.PYTHON_3_12] = CodeLanguage.PYTHON_3_12
    source: str = Field(min_length=1, max_length=32 * 1024)
    stdin_json: JsonValue | None = None
    expected_output_schema: dict[str, JsonValue]
    requested_artifacts: tuple[str, ...] = Field(default=(), max_length=16)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("requested_artifacts")
    @classmethod
    def validate_artifact_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("requested artifact names must be unique")
        for name in value:
            if not name or name.startswith(("/", ".")) or ".." in name.split("/"):
                raise ValueError("requested artifact must be a safe relative path")
        return value

    @model_validator(mode="after")
    def validate_digest_and_sizes(self) -> CodeExecutionWorkload:
        expected = hashlib.sha256(self.source.encode()).hexdigest()
        if self.source_sha256 != expected:
            raise ValueError("source_sha256 does not match source")
        if len(self.source.encode()) > 32 * 1024:
            raise ValueError("source byte limit exceeded")
        if (
            self.stdin_json is not None
            and len(canonical_json(self.stdin_json).encode()) > 64 * 1024
        ):
            raise ValueError("stdin JSON byte limit exceeded")
        return self

    @classmethod
    def create(cls, **values: Any) -> CodeExecutionWorkload:
        source = str(values.get("source", ""))
        values["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
        return cls.model_validate(values)


class CodeExecutionObservation(_FrozenCodeModel):
    workload_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    exit_code: int | None
    terminal_state: Literal["completed", "failed", "cancelled", "timed_out", "denied"]
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    result_json: JsonValue | None
    artifact_refs: tuple[str, ...]
    cpu_time_ms: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    denial_codes: tuple[str, ...]
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CodeCancellationReceipt(_FrozenCodeModel):
    workload_id: str = Field(min_length=1)
    requested_at: datetime
    acknowledged_at: datetime
    process_group_terminated: bool
    cleanup_state: Literal["complete", "quarantined", "failed"]

    @model_validator(mode="after")
    def validate_timestamps(self) -> CodeCancellationReceipt:
        if self.acknowledged_at < self.requested_at:
            raise ValueError("acknowledged_at precedes requested_at")
        return self


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResourceLimits:
    """Hard resource caps enforced inside the isolated environment."""

    cpu_cores: float = 1.0          # fractional vCPU (e.g. 0.5)
    memory_mb: int = 512            # RSS ceiling in MiB
    wall_clock_seconds: int = 1800  # overall wall-clock timeout
    output_bytes: int = 10_485_760  # 10 MiB max stdout/result payload
    artifact_bytes: int = 52_428_800  # 50 MiB max artifact store
    max_processes: int = 64         # process/thread ceiling (ulimit -u)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "wall_clock_seconds": self.wall_clock_seconds,
            "output_bytes": self.output_bytes,
            "artifact_bytes": self.artifact_bytes,
            "max_processes": self.max_processes,
        }


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEnvironmentPolicy:
    """Composite security + resource policy attached to an execution request."""

    network_policy: NetworkPolicy = NetworkPolicy.DENY_ALL
    filesystem_policy: FilesystemPolicy = FilesystemPolicy.READ_ONLY_ROOT
    resource_limits: ExecutionResourceLimits = field(
        default_factory=ExecutionResourceLimits
    )
    # Explicit tool/capability allow / deny lists (mirror of GovernancePolicy)
    allowed_capabilities: list[str] = field(default_factory=list)
    denied_capabilities: list[str] = field(default_factory=list)
    # Tenant-scoped network egress allow-list (CIDR ranges or hostnames)
    egress_allowlist: list[str] = field(default_factory=list)
    # Whether the isolated environment may mount any host path
    allow_host_path_mounts: bool = False
    # Whether the isolated environment runs in privileged mode
    allow_privileged: bool = False
    # Audit level requested for this execution
    audit_level: AuditLevel = AuditLevel.STANDARD

    def to_dict(self) -> dict[str, Any]:
        return {
            "network_policy": self.network_policy.value,
            "filesystem_policy": self.filesystem_policy.value,
            "resource_limits": self.resource_limits.to_dict(),
            "allowed_capabilities": self.allowed_capabilities,
            "denied_capabilities": self.denied_capabilities,
            "egress_allowlist": self.egress_allowlist,
            "allow_host_path_mounts": self.allow_host_path_mounts,
            "allow_privileged": self.allow_privileged,
            "audit_level": self.audit_level.value,
        }


# ---------------------------------------------------------------------------
# Execution environment spec
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEnvironmentSpec:
    """Describes the environment image / version that should run the workload."""

    runner_type: RunnerType = RunnerType.FAKE
    image: str = ""       # container image (for LOCAL / KUBERNETES runners)
    image_tag: str = ""   # e.g. "sha256:…" for pinned immutable image
    # Labels / annotations propagated to the container or K8s pod
    labels: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution envelope
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEnvelope:
    """Signed execution package passed from the control plane to the execution plane.

    The envelope carries everything the runner needs to reconstruct the agent
    execution context.  It must not contain raw host credentials, Docker socket
    paths, or unrestricted DB/Redis URLs.

    Sensitive fields (scoped_llm_api_key, scoped_db_url, scoped_redis_prefix)
    are populated by the scheduler immediately before dispatch and are never
    persisted.
    """

    # --- Identity ---
    tenant_id: str
    goal_id: str
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    agent_id: str = ""
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    execution_kind: ExecutionKind = ExecutionKind.AGENT_GOAL
    code_workload: CodeExecutionWorkload | None = None

    # --- Goal + context ---
    goal_text: str = ""
    execution_context: dict[str, Any] = field(default_factory=dict)
    # Serialised AgentConfig from the AgentStore (plain dict, no ORM objects)
    agent_config: dict[str, Any] = field(default_factory=dict)
    # Serialised RuntimeProfile from _build_runtime_profile()
    runtime_profile: dict[str, Any] = field(default_factory=dict)
    # Serialised ToolContext (tool names + prompt block; no raw MCP credentials)
    tool_context: dict[str, Any] = field(default_factory=dict)

    # --- Execution flags ---
    dry_run: bool = False
    sandbox_mode: bool = False
    workflow_mode: str = "single_agent"
    priority: str = "normal"

    # --- Policy ---
    policy: ExecutionEnvironmentPolicy = field(
        default_factory=ExecutionEnvironmentPolicy
    )
    spec: ExecutionEnvironmentSpec = field(
        default_factory=ExecutionEnvironmentSpec
    )

    # --- Governance state snapshot ---
    # Serialised HITL state for in-flight approval requests
    hitl_state: dict[str, Any] = field(default_factory=dict)
    # Per-goal cost limit (USD) — 0.0 means "use tenant default"
    cost_limit_usd: float = 0.0
    # Feature-flag snapshot at submission time
    feature_flags: dict[str, bool] = field(default_factory=dict)

    # --- Scoped credentials (populated by scheduler, never persisted) ---
    scoped_llm_api_key: str = ""      # tenant's LLM key (decrypted for this execution)
    scoped_db_url: str = ""           # scoped DB URL (RLS-only user or same URL + GUC)
    scoped_redis_prefix: str = ""     # key prefix for Redis isolation

    # --- Envelope integrity ---
    issued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    signature: str = ""  # HMAC-SHA256 over canonical fields, set by envelope.py

    def __post_init__(self) -> None:
        if self.execution_kind is ExecutionKind.AGENT_GOAL:
            if not self.goal_text or self.code_workload is not None:
                raise ValueError("agent_goal requires goal_text and forbids code_workload")
        elif self.goal_text or self.code_workload is None:
            raise ValueError("code_interpreter requires code_workload and forbids goal_text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "goal_id": self.goal_id,
            "attempt_id": self.attempt_id,
            "agent_id": self.agent_id,
            "correlation_id": self.correlation_id,
            "execution_kind": self.execution_kind.value,
            "code_workload": (
                self.code_workload.model_dump(mode="json")
                if self.code_workload is not None
                else None
            ),
            "goal_text": self.goal_text,
            "execution_context": self.execution_context,
            "agent_config": self.agent_config,
            "runtime_profile": self.runtime_profile,
            "tool_context": self.tool_context,
            "dry_run": self.dry_run,
            "sandbox_mode": self.sandbox_mode,
            "workflow_mode": self.workflow_mode,
            "priority": self.priority,
            "policy": self.policy.to_dict(),
            "spec": {
                "runner_type": self.spec.runner_type.value,
                "image": self.spec.image,
                "image_tag": self.spec.image_tag,
                "labels": self.spec.labels,
            },
            "hitl_state": self.hitl_state,
            "cost_limit_usd": self.cost_limit_usd,
            "feature_flags": self.feature_flags,
            "issued_at": self.issued_at,
            "signature": self.signature,
            # NOTE: scoped_* credentials are intentionally excluded from to_dict()
            # to prevent accidental logging or serialisation.
        }


# ---------------------------------------------------------------------------
# Execution request / result / events / artifacts
# ---------------------------------------------------------------------------


@dataclass
class ExecutionRequest:
    """Thin wrapper sent from the scheduler to a concrete runner."""

    envelope: ExecutionEnvelope
    runner_type: RunnerType = RunnerType.FAKE


@dataclass
class ExecutionEvent:
    """Event emitted by the execution plane and forwarded to the control plane."""

    goal_id: str
    tenant_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    # Isolation metadata (optional — not present on core agent events)
    runner_type: str = ""
    capsule_id: str = ""      # container/pod/process ID
    attempt_id: str = ""
    emitted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_sse_dict(self) -> dict[str, Any]:
        """Return a dict compatible with the existing SSE event format."""
        result = {**self.payload, "type": self.event_type}
        if self.runner_type:
            result["_isolation"] = {
                "runner_type": self.runner_type,
                "capsule_id": self.capsule_id,
                "attempt_id": self.attempt_id,
            }
        return result


@dataclass
class ExecutionArtifact:
    """Reference to an artifact produced by the isolated execution."""

    artifact_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    goal_id: str = ""
    tenant_id: str = ""
    name: str = ""
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    storage_url: str = ""    # internal storage location (MinIO/S3)
    checksum_sha256: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class ExecutionResult:
    """Structured result returned from the execution plane to the control plane."""

    goal_id: str
    tenant_id: str
    attempt_id: str
    success: bool
    status: str               # mirrors GoalStatus values
    iterations: int = 0
    error_message: str = ""
    failure_reason: ExecutionFailureReason | None = None
    # Raw AgentState fields surfaced for downstream processing
    plan: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    verification_feedback: str = ""
    # Isolation observability metadata
    runner_type: str = ""
    capsule_id: str = ""
    queue_time_ms: float = 0.0
    startup_time_ms: float = 0.0
    execution_time_ms: float = 0.0
    exit_code: int | None = None
    timeout_hit: bool = False
    resource_limit_hit: bool = False
    cleanup_status: str = "ok"
    code_observation: CodeExecutionObservation | None = None
    artifacts: list[ExecutionArtifact] = field(default_factory=list)
    completed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "tenant_id": self.tenant_id,
            "attempt_id": self.attempt_id,
            "success": self.success,
            "status": self.status,
            "iterations": self.iterations,
            "error_message": self.error_message,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "plan": self.plan,
            "steps": self.steps,
            "verification_feedback": self.verification_feedback,
            "runner_type": self.runner_type,
            "capsule_id": self.capsule_id,
            "queue_time_ms": self.queue_time_ms,
            "startup_time_ms": self.startup_time_ms,
            "execution_time_ms": self.execution_time_ms,
            "exit_code": self.exit_code,
            "timeout_hit": self.timeout_hit,
            "resource_limit_hit": self.resource_limit_hit,
            "cleanup_status": self.cleanup_status,
            "code_observation": (
                self.code_observation.model_dump(mode="json")
                if self.code_observation is not None
                else None
            ),
            "completed_at": self.completed_at,
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "name": a.name,
                    "mime_type": a.mime_type,
                    "size_bytes": a.size_bytes,
                    "storage_url": a.storage_url,
                    "checksum_sha256": a.checksum_sha256,
                    "created_at": a.created_at,
                }
                for a in self.artifacts
            ],
        }
