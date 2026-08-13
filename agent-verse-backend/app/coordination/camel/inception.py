"""Deterministic CAMEL inception validation."""

from __future__ import annotations

import hashlib
import json

from app.coordination.camel.models import InceptionArtifact, RoleContract


def build_inception(
    roles: tuple[RoleContract, ...],
    *,
    available_tools: frozenset[str],
    available_connectors: frozenset[str],
    platform_authority: frozenset[str],
    maximum_context_characters: int,
) -> InceptionArtifact:
    if len(roles) < 2 or len({role.role_name for role in roles}) != len(roles):
        raise ValueError("CAMEL requires at least two unique roles")
    schemas = {role.communication_schema for role in roles}
    if len(schemas) != 1:
        raise ValueError("role communication schemas conflict")
    responsibilities = {item.casefold() for role in roles for item in role.responsibilities}
    if len(responsibilities) < len(roles):
        raise ValueError("roles must have complementary responsibilities")
    for role in roles:
        if not role.tool_allowlist <= available_tools:
            raise ValueError("role requests an unavailable tool")
        if not role.connector_allowlist <= available_connectors:
            raise ValueError("role requests an unavailable connector")
        if not role.authority_ceiling <= platform_authority:
            raise PermissionError("role authority exceeds platform authority")
    canonical = json.dumps(
        [role.model_dump(mode="json") for role in sorted(roles, key=lambda item: item.role_name)],
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(canonical) > maximum_context_characters:
        raise ValueError("role context exceeds maximum")
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return InceptionArtifact(
        contract_digest=digest,
        role_names=tuple(sorted(role.role_name for role in roles)),
        safe_summary="Bounded collaboration between "
        + ", ".join(sorted(role.role_name for role in roles)),
        communication_schema=next(iter(schemas)),
    )


__all__ = ["build_inception"]
