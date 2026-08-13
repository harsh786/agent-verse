"""Control-plane validation for bounded Python code workloads."""

from __future__ import annotations

from dataclasses import dataclass

from app.execution_environment.models import CodeExecutionWorkload, canonical_json
from app.execution_environment.python_policy import CodePolicyViolation, PythonPolicyValidator


@dataclass(frozen=True, slots=True)
class CodeWorkloadLimits:
    source_bytes: int = 16 * 1024
    stdin_bytes: int = 32 * 1024
    artifact_count: int = 8

    def __post_init__(self) -> None:
        if not 0 < self.source_bytes <= 32 * 1024:
            raise ValueError("source_bytes exceeds absolute ceiling")
        if not 0 < self.stdin_bytes <= 64 * 1024:
            raise ValueError("stdin_bytes exceeds absolute ceiling")
        if not 0 <= self.artifact_count <= 16:
            raise ValueError("artifact_count exceeds absolute ceiling")


class CodeWorkloadValidator:
    def __init__(self, limits: CodeWorkloadLimits | None = None) -> None:
        self._limits = limits or CodeWorkloadLimits()
        self._python = PythonPolicyValidator()

    def validate(self, workload: CodeExecutionWorkload) -> tuple[CodePolicyViolation, ...]:
        violations: list[CodePolicyViolation] = []
        if len(workload.source.encode()) > self._limits.source_bytes:
            violations.append(CodePolicyViolation("source_size_exceeded"))
        if (
            workload.stdin_json is not None
            and len(canonical_json(workload.stdin_json).encode()) > self._limits.stdin_bytes
        ):
            violations.append(CodePolicyViolation("stdin_size_exceeded"))
        if len(workload.requested_artifacts) > self._limits.artifact_count:
            violations.append(CodePolicyViolation("artifact_count_exceeded"))
        violations.extend(self._python.validate(workload.source))
        return tuple(violations)


__all__ = ["CodeWorkloadLimits", "CodeWorkloadValidator"]
