from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class SandboxType(str, enum.Enum):
    NONE = "none"
    PYTHON = "python"
    BROWSER = "browser"
    SHELL = "shell"
    MCP = "mcp"
    SIMULATION = "simulation"


@dataclass
class SandboxRuntimeProfile:
    sandbox_type: SandboxType = SandboxType.NONE
    network_policy: str = "none"
    filesystem_policy: str = "read_only"
    timeout_seconds: int = 30
    requires_dry_run: bool = False
    rollback_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_type": self.sandbox_type.value,
            "network_policy": self.network_policy,
            "filesystem_policy": self.filesystem_policy,
            "timeout_seconds": self.timeout_seconds,
            "requires_dry_run": self.requires_dry_run,
            "rollback_required": self.rollback_required,
        }
