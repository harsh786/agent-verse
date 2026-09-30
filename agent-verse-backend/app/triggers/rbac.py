"""RBAC permission matrix for trigger operations.

6 roles x 8 operations = 48 cells, all explicitly defined. ``system`` is the
role of automated fires; manual fires map the caller via :func:`trigger_role`.
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
    # Automated fires (schedules, event-bus consumers, token/HMAC-authenticated
    # webhooks): the platform firing a trigger its owner configured.
    "system": {
        "create": False,
        "read": False,
        "update": False,
        "delete": False,
        "fire": True,
        "pause": False,
        "resume": False,
        "view_dlq": False,
    },
}

SYSTEM_ROLE = "system"

VALID_OPERATIONS = frozenset(TRIGGER_PERMISSION_MATRIX["admin"].keys())


def trigger_role(tenant_ctx: object) -> str:
    """Map an authenticated caller's platform roles onto this matrix.

    The most privileged matching role wins. A key with no roles at all is a
    legacy key (``api_key``; the scope middleware already gates its writes).
    Any other role set without admin/developer/operator — viewer, approver,
    agent, custom roles — maps to ``viewer`` so it is denied by default.
    """
    roles = {str(r) for r in (getattr(tenant_ctx, "roles", ()) or ())}
    for role in ("admin", "developer", "operator"):
        if role in roles:
            return role
    return "api_key" if not roles else "viewer"


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
