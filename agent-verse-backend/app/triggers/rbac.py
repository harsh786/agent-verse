"""RBAC permission matrix for trigger operations."""
from __future__ import annotations

# Maps role → set of allowed operations
TRIGGER_PERMISSION_MATRIX: dict[str, frozenset[str]] = {
    "admin":     frozenset(["create", "read", "update", "delete", "enable",
                             "disable", "fire_manual", "view_history", "rotate_secret"]),
    "developer": frozenset(["create", "read", "update", "enable", "disable",
                             "fire_manual", "view_history"]),
    "operator":  frozenset(["read", "enable", "disable", "fire_manual", "view_history"]),
    "viewer":    frozenset(["read", "view_history"]),
    "api_key":   frozenset(["fire_manual"]),
}


class TriggerPermissionDenied(Exception):
    pass


def check_permission(role: str, operation: str) -> None:
    """Raise TriggerPermissionDenied if the role cannot perform the operation."""
    allowed = TRIGGER_PERMISSION_MATRIX.get(role, frozenset())
    if operation not in allowed:
        raise TriggerPermissionDenied(
            f"Role '{role}' is not permitted to perform '{operation}' on triggers"
        )
