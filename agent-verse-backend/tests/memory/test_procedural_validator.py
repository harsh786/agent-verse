# tests/memory/test_procedural_validator.py
"""Dedicated coverage for app/memory/procedural_validator.py.

ProcedureContract + validate_procedure gate every skill reuse: a learned tool
sequence must be re-validated against the *current* tenant, policy, connector
readiness and tool schema versions before the agent is allowed to replay it.
These tests exercise every rejection path individually (not just the single
happy-path + schema-mismatch pair covered incidentally in
tests/memory/test_memory_learning_services.py), plus the pydantic-level
schema validation (missing/extra fields) that had no coverage at all.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.memory.procedural_validator import ProcedureContract, validate_procedure


def _procedure(**overrides: object) -> ProcedureContract:
    defaults: dict[str, object] = {
        "procedure_id": "skill-1",
        "tenant_id": "tenant-a",
        "skill_version": "v1",
        "tool_sequence": ("jira.search_issues", "jira.get_issue"),
        "required_capabilities": frozenset({"jira.read"}),
        "tool_schema_versions": {"jira.search_issues": "v2", "jira.get_issue": "v1"},
        "connector_ids": frozenset({"jira-connector"}),
        "policy_fingerprint": "policy-v1",
    }
    defaults.update(overrides)
    return ProcedureContract(**defaults)  # type: ignore[arg-type]


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "tenant_id": "tenant-a",
        "available_tools": {"jira.search_issues": "v2", "jira.get_issue": "v1"},
        "allowed_capabilities": frozenset({"jira.read", "jira.write"}),
        "ready_connectors": frozenset({"jira-connector", "slack-connector"}),
        "policy_fingerprint": "policy-v1",
    }
    kwargs.update(overrides)
    return kwargs


# ── Schema validation (pydantic) ────────────────────────────────────────────


def test_valid_procedure_contract_is_accepted() -> None:
    procedure = _procedure()
    assert procedure.tenant_id == "tenant-a"
    assert procedure.deprecated is False


def test_procedure_contract_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError, match="policy_fingerprint"):
        ProcedureContract(
            procedure_id="skill-1",
            tenant_id="tenant-a",
            skill_version="v1",
            tool_sequence=("search",),
            required_capabilities=frozenset({"research"}),
            tool_schema_versions={"search": "v2"},
            connector_ids=frozenset({"docs"}),
        )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "missing_field",
    [
        "procedure_id",
        "tenant_id",
        "skill_version",
        "tool_sequence",
        "required_capabilities",
        "tool_schema_versions",
        "connector_ids",
    ],
)
def test_procedure_contract_rejects_each_missing_required_field(missing_field: str) -> None:
    kwargs: dict[str, object] = {
        "procedure_id": "skill-1",
        "tenant_id": "tenant-a",
        "skill_version": "v1",
        "tool_sequence": ("search",),
        "required_capabilities": frozenset({"research"}),
        "tool_schema_versions": {"search": "v2"},
        "connector_ids": frozenset({"docs"}),
        "policy_fingerprint": "policy-v1",
    }
    del kwargs[missing_field]
    with pytest.raises(ValidationError):
        ProcedureContract(**kwargs)  # type: ignore[arg-type]


def test_procedure_contract_rejects_unknown_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        ProcedureContract(
            procedure_id="skill-1",
            tenant_id="tenant-a",
            skill_version="v1",
            tool_sequence=("search",),
            required_capabilities=frozenset({"research"}),
            tool_schema_versions={"search": "v2"},
            connector_ids=frozenset({"docs"}),
            policy_fingerprint="policy-v1",
            unexpected_field="surprise",
        )  # type: ignore[call-arg]


def test_procedure_contract_is_frozen_and_immutable() -> None:
    procedure = _procedure()
    with pytest.raises(ValidationError):
        procedure.deprecated = True  # type: ignore[misc]


def test_procedure_contract_coerces_list_inputs_to_tuple_and_frozenset() -> None:
    """Pydantic must normalize list/set literals to the declared tuple/frozenset types."""
    procedure = ProcedureContract(
        procedure_id="skill-1",
        tenant_id="tenant-a",
        skill_version="v1",
        tool_sequence=["search", "fetch"],  # type: ignore[arg-type]
        required_capabilities={"research"},  # type: ignore[arg-type]
        tool_schema_versions={"search": "v2"},
        connector_ids={"docs"},  # type: ignore[arg-type]
        policy_fingerprint="policy-v1",
    )
    assert procedure.tool_sequence == ("search", "fetch")
    assert procedure.required_capabilities == frozenset({"research"})
    assert procedure.connector_ids == frozenset({"docs"})


# ── validate_procedure: happy path ──────────────────────────────────────────


def test_validate_procedure_accepts_matching_context() -> None:
    validate_procedure(_procedure(), **_valid_kwargs())  # must not raise


def test_validate_procedure_allows_superset_of_capabilities_and_connectors() -> None:
    """Extra allowed capabilities/ready connectors beyond what the procedure needs are fine."""
    validate_procedure(
        _procedure(),
        **_valid_kwargs(
            allowed_capabilities=frozenset({"jira.read", "jira.write", "slack.write"}),
            ready_connectors=frozenset({"jira-connector", "github-connector"}),
        ),
    )


def test_validate_procedure_accepts_empty_capabilities_and_connectors_when_none_required() -> None:
    """Vacuous truth: a procedure that needs nothing is trivially satisfiable."""
    procedure = _procedure(required_capabilities=frozenset(), connector_ids=frozenset())
    validate_procedure(
        procedure,
        **_valid_kwargs(allowed_capabilities=frozenset(), ready_connectors=frozenset()),
    )


def test_validate_procedure_accepts_empty_tool_schema_versions() -> None:
    """No declared schema pins means no schema check is enforced."""
    procedure = _procedure(tool_schema_versions={})
    validate_procedure(procedure, **_valid_kwargs(available_tools={}))


# ── validate_procedure: rejection paths ─────────────────────────────────────


def test_validate_procedure_rejects_cross_tenant_reuse() -> None:
    with pytest.raises(PermissionError, match="tenant boundary"):
        validate_procedure(_procedure(), **_valid_kwargs(tenant_id="tenant-b"))


def test_validate_procedure_rejects_deprecated_procedure() -> None:
    with pytest.raises(RuntimeError, match="deprecated"):
        validate_procedure(_procedure(deprecated=True), **_valid_kwargs())


def test_validate_procedure_rejects_stale_policy_fingerprint() -> None:
    with pytest.raises(PermissionError, match="policy is stale"):
        validate_procedure(_procedure(), **_valid_kwargs(policy_fingerprint="policy-v2"))


def test_validate_procedure_rejects_missing_required_capability() -> None:
    procedure = _procedure(required_capabilities=frozenset({"jira.read", "jira.admin"}))
    with pytest.raises(PermissionError, match="capability denied"):
        validate_procedure(
            procedure, **_valid_kwargs(allowed_capabilities=frozenset({"jira.read"}))
        )


def test_validate_procedure_rejects_unready_connector() -> None:
    procedure = _procedure(connector_ids=frozenset({"jira-connector", "github-connector"}))
    with pytest.raises(RuntimeError, match="connector unavailable"):
        validate_procedure(
            procedure, **_valid_kwargs(ready_connectors=frozenset({"jira-connector"}))
        )


def test_validate_procedure_rejects_tool_schema_mismatch() -> None:
    with pytest.raises(RuntimeError, match=r"tool schema mismatch: jira\.search_issues"):
        validate_procedure(
            _procedure(),
            **_valid_kwargs(available_tools={"jira.search_issues": "v3", "jira.get_issue": "v1"}),
        )


def test_validate_procedure_rejects_tool_schema_missing_entirely() -> None:
    """A tool the procedure depends on that has since been removed must also fail closed."""
    with pytest.raises(RuntimeError, match=r"tool schema mismatch: jira\.get_issue"):
        validate_procedure(
            _procedure(), **_valid_kwargs(available_tools={"jira.search_issues": "v2"})
        )


def test_validate_procedure_checks_tenant_before_deprecation() -> None:
    """Fail-closed ordering: cross-tenant access is rejected even for a valid, non-deprecated procedure."""
    procedure = _procedure(deprecated=False)
    with pytest.raises(PermissionError, match="tenant boundary"):
        validate_procedure(procedure, **_valid_kwargs(tenant_id="someone-else"))
