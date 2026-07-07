from __future__ import annotations
from typing import TYPE_CHECKING
from app.sandbox_runtime.profile import SandboxRuntimeProfile, SandboxType

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class SandboxExecutor:
    def select_sandbox(self, profile: "GoalRuntimeProfile") -> SandboxRuntimeProfile:
        if not profile.security.sandbox_required:
            return SandboxRuntimeProfile(sandbox_type=SandboxType.NONE)
        if profile.properties.requires_code:
            return SandboxRuntimeProfile(
                sandbox_type=SandboxType.PYTHON,
                network_policy="none",
                filesystem_policy="ephemeral",
                timeout_seconds=30,
                requires_dry_run=True,
                rollback_required=True,
            )
        return SandboxRuntimeProfile(
            sandbox_type=SandboxType.SIMULATION,
            network_policy="tenant_connectors_only",
            filesystem_policy="read_only",
            timeout_seconds=60,
            requires_dry_run=True,
        )
