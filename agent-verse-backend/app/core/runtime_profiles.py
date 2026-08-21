"""RuntimeProfilesRegistry — Layer 0 registry for all active GoalRuntimeProfiles.

Holds all active GoalRuntimeProfiles by (tenant_id, goal_id) key.
Allows the platform to inspect and audit runtime decisions per goal.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any


class RuntimeProfilesRegistry:
    """In-memory registry of active GoalRuntimeProfiles.

    In production this is a thin in-process cache; the durable copy lives
    in goals.execution_context (Postgres JSONB column).
    """

    def __init__(self) -> None:
        self._profiles: dict[str, Any] = {}  # "tenant_id:goal_id" → GoalRuntimeProfile

    def register(self, profile: Any) -> None:
        """Register a GoalRuntimeProfile for a goal."""
        key = f"{profile.tenant_id}:{profile.goal_id}"
        self._profiles[key] = profile

    def get(self, tenant_id: str, goal_id: str) -> Any | None:
        """Retrieve a registered profile. Returns None if not found."""
        return self._profiles.get(f"{tenant_id}:{goal_id}")

    def list_for_tenant(self, tenant_id: str) -> list[Any]:
        """List all active profiles for a tenant."""
        return [v for k, v in self._profiles.items() if k.startswith(f"{tenant_id}:")]

    def remove(self, tenant_id: str, goal_id: str) -> None:
        """Remove a profile when goal completes."""
        self._profiles.pop(f"{tenant_id}:{goal_id}", None)

    def __len__(self) -> int:
        return len(self._profiles)


@lru_cache(maxsize=1)
def get_runtime_profiles_registry() -> RuntimeProfilesRegistry:
    """Return the platform-wide RuntimeProfilesRegistry singleton."""
    return RuntimeProfilesRegistry()
