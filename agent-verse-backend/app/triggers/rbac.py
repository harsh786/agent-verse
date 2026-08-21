"""RBAC permission matrix for trigger operations.

5 roles x 8 operations = 40 cells, all explicitly defined.
Operations: create | read | update | delete | fire | pause | resume | view_dlq
"""

from __future__ import annotations

# Explicit matrix: role → operation → bool
# Every cell MUST be explicitly True or False (no implicit defaults).
TRIGGER_PERMISSION_MATRIX: dict[str, dict[str, bool]] = {
    "admin": {
        "create": True,
        "read": True,
        "update": True,
        "delete": True,
        "fire": True,
        "pause": True,
        "resume": True,
        "view_dlq": True,
    },
    "developer": {
        "create": True,
        "read": True,
        "update": True,
        "delete": False,
        "fire": True,
        "pause": True,
        "resume": True,
        "view_dlq": True,
    },
    "operator": {
        "create": False,
        "read": True,
        "update": False,
        "delete": False,
        "fire": True,
        "pause": True,
        "resume": True,
        "view_dlq": True,
    },
    "viewer": {
        "create": False,
        "read": True,
        "update": False,
        "delete": False,
        "fire": False,
        "pause": False,
        "resume": False,
        "view_dlq": False,
    },
    "api_key": {
        "create": False,
        "read": True,
        "update": False,
        "delete": False,
        "fire": True,
        "pause": False,
        "resume": False,
        "view_dlq": False,
    },
}

VALID_OPERATIONS = frozenset(TRIGGER_PERMISSION_MATRIX["admin"].keys())


class TriggerPermissionDenied(Exception):  # noqa: N818
    pass


def check_permission(role: str, operation: str) -> bool:
    """Return True if the role can perform the operation, else raise TriggerPermissionDenied.

    Raises:
        TriggerPermissionDenied: if the role/operation is unknown or not permitted.
    """
    if operation not in VALID_OPERATIONS:
        raise TriggerPermissionDenied(
            f"Unknown operation: '{operation}'. Valid operations: {sorted(VALID_OPERATIONS)}"
        )
    role_matrix = TRIGGER_PERMISSION_MATRIX.get(role)
    if role_matrix is None:
        raise TriggerPermissionDenied(
            f"Unknown role: '{role}'. Valid roles: {sorted(TRIGGER_PERMISSION_MATRIX)}"
        )
    if not role_matrix[operation]:
        raise TriggerPermissionDenied(
            f"Role '{role}' is not permitted to perform '{operation}' on triggers"
        )
    return True
