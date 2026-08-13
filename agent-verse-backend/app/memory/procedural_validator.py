"""Versioned procedural-memory validation before every reuse."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProcedureContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    procedure_id: str
    tenant_id: str
    skill_version: str
    tool_sequence: tuple[str, ...]
    required_capabilities: frozenset[str]
    tool_schema_versions: dict[str, str]
    connector_ids: frozenset[str]
    policy_fingerprint: str
    deprecated: bool = False


def validate_procedure(
    procedure: ProcedureContract,
    *,
    tenant_id: str,
    available_tools: dict[str, str],
    allowed_capabilities: frozenset[str],
    ready_connectors: frozenset[str],
    policy_fingerprint: str,
) -> None:
    if procedure.tenant_id != tenant_id:
        raise PermissionError("procedure crosses tenant boundary")
    if procedure.deprecated:
        raise RuntimeError("procedure is deprecated")
    if procedure.policy_fingerprint != policy_fingerprint:
        raise PermissionError("procedure policy is stale")
    if not procedure.required_capabilities <= allowed_capabilities:
        raise PermissionError("procedure capability denied")
    if not procedure.connector_ids <= ready_connectors:
        raise RuntimeError("procedure connector unavailable")
    for tool, version in procedure.tool_schema_versions.items():
        if available_tools.get(tool) != version:
            raise RuntimeError(f"tool schema mismatch: {tool}")


__all__ = ["ProcedureContract", "validate_procedure"]
