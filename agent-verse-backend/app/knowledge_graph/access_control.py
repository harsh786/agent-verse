"""Knowledge Graph Access Control — SUPPLEMENT U5.

Controls who can see, query, and export knowledge graphs.
Enforced at: API layer + SSE stream + S3 file access.

Access is tenant-isolated AND role-based within the tenant.
"""

from __future__ import annotations

from typing import Any

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

# ── Access level matrix ───────────────────────────────────────────────────────

ACCESS_LEVELS: dict[str, list[str]] = {
    "tenant_admin": ["read", "write", "export", "delete", "share"],
    "org_admin": ["read", "write", "export"],
    "org_member": ["read", "query"],
    "org_viewer": ["read"],
    "approver": [],  # no knowledge graph access by default
    "agent_runner": ["read", "query"],
    "observer": ["read"],
}

_ALL_OPERATIONS = frozenset({"read", "write", "export", "delete", "share", "query"})


class GraphAccessControl:
    """Enforces role-based access to knowledge graphs.

    Usage:
        gac = GraphAccessControl()
        gac.require("org_member", "query")      # passes
        gac.require("approver", "write")        # raises PermissionError
    """

    def __init__(self) -> None:
        self._overrides: dict[str, list[str]] = {}  # tenant-specific overrides

    def allowed_operations(self, role: str, tenant_id: str | None = None) -> list[str]:
        """Return the list of operations allowed for a role."""
        # Check tenant-specific overrides first
        key = f"{tenant_id}:{role}" if tenant_id else role
        if key in self._overrides:
            return self._overrides[key]
        return ACCESS_LEVELS.get(role, [])

    def can(self, role: str, operation: str, tenant_id: str | None = None) -> bool:
        """Return True if the given role is allowed to perform operation."""
        return operation in self.allowed_operations(role, tenant_id)

    def require(self, role: str, operation: str, tenant_id: str | None = None) -> None:
        """Raise PermissionError if role cannot perform operation."""
        if not self.can(role, operation, tenant_id):
            raise PermissionError(
                f"Role '{role}' is not allowed to '{operation}' knowledge graphs. "
                f"Allowed: {self.allowed_operations(role, tenant_id)}"
            )

    def set_override(
        self,
        role: str,
        operations: list[str],
        tenant_id: str | None = None,
    ) -> None:
        """Set a per-tenant override for a role's permissions."""
        key = f"{tenant_id}:{role}" if tenant_id else role
        self._overrides[key] = [op for op in operations if op in _ALL_OPERATIONS]
        _log.info(
            "graph_access_control.override_set",
            role=role,
            tenant_id=tenant_id,
            operations=self._overrides[key],
        )

    def validate_export_request(
        self,
        tenant_id: str,
        org_id: str,
        requesting_role: str,
        export_format: str = "json",
    ) -> dict[str, Any]:
        """Validate an export request and return sanitised config."""
        with _tracer.start_as_current_span("graph_access.validate_export") as span:
            span.set_attribute("tenant_id", tenant_id)
            span.set_attribute("org_id", org_id)
            span.set_attribute("role", requesting_role)

            self.require(requesting_role, "export", tenant_id)

            return {
                "allowed": True,
                "format": export_format,
                "include_embeddings": requesting_role in ("tenant_admin", "org_admin"),
                "include_metadata": True,
                "max_nodes": 10_000 if requesting_role == "tenant_admin" else 1_000,
            }


# Module-level singleton
_gac = GraphAccessControl()


def get_graph_access_control() -> GraphAccessControl:
    return _gac
