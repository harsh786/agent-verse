from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.orchestration.strategy_contracts import (
    ArtifactKind,
    ArtifactReference,
    CertificationEvidence,
    CertificationKind,
    CertificationStatus,
    CheckpointMigration,
    EvidenceKind,
    EvidenceReference,
    ExecutionTerminalState,
    PatternLimits,
    ProbeStatus,
    ReadinessProbe,
    StrategyCheckpoint,
    StrategyCostLevel,
    StrategyExecutionRequest,
    StrategyExecutionResult,
    StrategyFamily,
    StrategyLatencyClass,
    StrategyLifecycleState,
    StrategyRiskLevel,
    StrategySpec,
)


class CustomInteger(int):
    pass


class CustomFloat(float):
    pass


def _assert_validation_error(
    error: ValidationError,
    *,
    location: tuple[str, ...],
    error_type: str,
) -> None:
    validation_errors = error.errors()
    assert len(validation_errors) == 1
    assert validation_errors[0]["loc"] == location
    assert validation_errors[0]["type"] == error_type


def _assert_stable_json(model: BaseModel) -> None:
    payload = model.model_dump_json()
    restored = type(model).model_validate_json(payload)
    assert restored == model
    assert restored.model_dump_json() == payload


def _limits() -> PatternLimits:
    return PatternLimits(
        calls=4,
        nodes=8,
        edges=12,
        depth=3,
        fan_out=2,
        rounds=3,
        tokens=2_000,
        duration_seconds=30,
        cost_usd=0.25,
    )


def _execution_result(*, trace_summary: object) -> StrategyExecutionResult:
    return StrategyExecutionResult.model_validate(
        {
            "terminal_state": ExecutionTerminalState.SUCCEEDED,
            "answer": "The governed execution completed.",
            "evidence": (
                EvidenceReference(
                    kind=EvidenceKind.TRACE,
                    reference="trace://execution/123",
                    digest="sha256:evidence",
                ),
            ),
            "artifacts": (
                ArtifactReference(
                    kind=ArtifactKind.REPORT,
                    reference="artifact://reports/123",
                    media_type="application/json",
                    digest="sha256:artifact",
                ),
            ),
            "cost_usd": 0.02,
            "next_action": None,
            "safe_rationale_summary": (
                "Execution met the policy and evidence requirements."
            ),
            "trace_summary": trace_summary,
        }
    )


def test_strategy_lifecycle_states_match_required_contract() -> None:
    assert {state.value for state in StrategyLifecycleState} == {
        "planned",
        "partial",
        "implemented",
        "certified",
        "disabled",
    }


def test_strategy_spec_requires_semver_and_state_schema() -> None:
    spec = StrategySpec(
        strategy_id="react",
        adapter_version="2.1.0",
        family=StrategyFamily.REASONING,
        risk=StrategyRiskLevel.MEDIUM,
        cost=StrategyCostLevel.LOW,
        latency=StrategyLatencyClass.INTERACTIVE,
        state_schema_version=3,
        lifecycle_state=StrategyLifecycleState.IMPLEMENTED,
        default_limits=_limits(),
        readiness_requirements=("provider", "checkpoint_store"),
    )
    _assert_stable_json(spec)

    invalid_cases = (
        ({"adapter_version": "v2"}, ("adapter_version",), "string_pattern_mismatch"),
        ({"state_schema_version": 0}, ("state_schema_version",), "greater_than"),
    )
    for update, location, error_type in invalid_cases:
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec.model_validate({**spec.model_dump(), **update})
        _assert_validation_error(exc_info.value, location=location, error_type=error_type)

    with pytest.raises(ValidationError) as exc_info:
        spec.strategy_id = "changed"  # type: ignore[misc]
    _assert_validation_error(
        exc_info.value,
        location=("strategy_id",),
        error_type="frozen_instance",
    )


def test_strategy_spec_requires_typed_risk_cost_and_latency_metadata() -> None:
    payload = {
        "strategy_id": "react",
        "adapter_version": "2.1.0",
        "family": StrategyFamily.REASONING,
        "risk": StrategyRiskLevel.HIGH,
        "cost": StrategyCostLevel.MEDIUM,
        "latency": StrategyLatencyClass.BATCH,
        "state_schema_version": 3,
        "lifecycle_state": StrategyLifecycleState.IMPLEMENTED,
        "default_limits": _limits(),
    }
    spec = StrategySpec.model_validate(payload)
    _assert_stable_json(spec)
    assert spec.risk is StrategyRiskLevel.HIGH
    assert spec.cost is StrategyCostLevel.MEDIUM
    assert spec.latency is StrategyLatencyClass.BATCH

    for field_name in ("risk", "cost", "latency"):
        missing_payload = dict(payload)
        missing_payload.pop(field_name)
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec.model_validate(missing_payload)
        _assert_validation_error(
            exc_info.value,
            location=(field_name,),
            error_type="missing",
        )

    invalid_cases = (
        ("risk", "severe"),
        ("cost", "unbounded"),
        ("latency", "immediate"),
    )
    for field_name, invalid_value in invalid_cases:
        with pytest.raises(ValidationError):
            StrategySpec.model_validate({**payload, field_name: invalid_value})


def test_pattern_limits_reject_non_positive_bounds() -> None:
    limits = _limits()
    _assert_stable_json(limits)

    for field_name in (
        "calls",
        "depth",
        "fan_out",
        "rounds",
        "tokens",
        "duration_seconds",
        "cost_usd",
    ):
        with pytest.raises(ValidationError) as exc_info:
            PatternLimits.model_validate({**limits.model_dump(), field_name: 0})
        _assert_validation_error(
            exc_info.value,
            location=(field_name,),
            error_type="greater_than",
        )

        with pytest.raises(ValidationError) as exc_info:
            PatternLimits.model_validate({**limits.model_dump(), field_name: None})
        _assert_validation_error(
            exc_info.value,
            location=(field_name,),
            error_type="int_type" if field_name != "cost_usd" else "float_type",
        )


@pytest.mark.parametrize(
    "invalid_value",
    (True, 1.0, "1", b"1", Decimal("1")),
)
def test_positive_integer_fields_require_actual_int_values(
    invalid_value: object,
) -> None:
    limits = _limits()
    with pytest.raises(ValidationError):
        PatternLimits.model_validate({**limits.model_dump(), "calls": invalid_value})

    spec = StrategySpec(
        strategy_id="react",
        adapter_version="2.1.0",
        family=StrategyFamily.REASONING,
        risk=StrategyRiskLevel.LOW,
        cost=StrategyCostLevel.LOW,
        latency=StrategyLatencyClass.REALTIME,
        state_schema_version=1,
        lifecycle_state=StrategyLifecycleState.IMPLEMENTED,
        default_limits=limits,
    )
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(
            {**spec.model_dump(), "state_schema_version": invalid_value}
        )

    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"checkpoint_version": invalid_value})


def test_positive_integer_consumers_reject_int_subclasses() -> None:
    invalid_value = CustomInteger(1)
    limits = _limits()

    with pytest.raises(ValidationError):
        PatternLimits.model_validate({**limits.model_dump(), "calls": invalid_value})

    spec = StrategySpec(
        strategy_id="react",
        adapter_version="2.1.0",
        family=StrategyFamily.REASONING,
        risk=StrategyRiskLevel.LOW,
        cost=StrategyCostLevel.LOW,
        latency=StrategyLatencyClass.REALTIME,
        state_schema_version=1,
        lifecycle_state=StrategyLifecycleState.IMPLEMENTED,
        default_limits=limits,
    )
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(
            {**spec.model_dump(), "state_schema_version": invalid_value}
        )

    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"checkpoint_version": invalid_value})


@pytest.mark.parametrize(
    "invalid_value",
    (
        True,
        "1",
        b"1",
        Decimal("1"),
        float("nan"),
        float("inf"),
        float("-inf"),
    ),
)
def test_float_contract_fields_require_finite_builtin_numbers(
    invalid_value: object,
) -> None:
    limits = _limits()
    with pytest.raises(ValidationError):
        PatternLimits.model_validate({**limits.model_dump(), "cost_usd": invalid_value})

    result = _execution_result(trace_summary={"status": "completed"})
    with pytest.raises(ValidationError):
        StrategyExecutionResult.model_validate(
            {**result.model_dump(), "cost_usd": invalid_value}
        )

    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"duration_seconds": invalid_value})


@pytest.mark.parametrize("invalid_value", (CustomInteger(1), CustomFloat(1.0)))
def test_float_contract_consumers_reject_numeric_subclasses(
    invalid_value: object,
) -> None:
    limits = _limits()
    with pytest.raises(ValidationError):
        PatternLimits.model_validate({**limits.model_dump(), "cost_usd": invalid_value})

    result = _execution_result(trace_summary={"status": "completed"})
    with pytest.raises(ValidationError):
        StrategyExecutionResult.model_validate(
            {**result.model_dump(), "cost_usd": invalid_value}
        )

    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"duration_seconds": invalid_value})


def test_float_contract_fields_normalize_builtin_int_values() -> None:
    limits = PatternLimits.model_validate({**_limits().model_dump(), "cost_usd": 1})
    result = StrategyExecutionResult.model_validate(
        {**_execution_result(trace_summary={}).model_dump(), "cost_usd": 1}
    )
    trace_result = _execution_result(
        trace_summary={"cost_usd": 1, "duration_seconds": 2}
    )

    assert limits.cost_usd == 1.0
    assert isinstance(limits.cost_usd, float)
    assert result.cost_usd == 1.0
    assert isinstance(result.cost_usd, float)
    assert trace_result.trace_summary.cost_usd == 1.0
    assert isinstance(trace_result.trace_summary.cost_usd, float)
    assert trace_result.trace_summary.duration_seconds == 2.0
    assert isinstance(trace_result.trace_summary.duration_seconds, float)


def test_execution_request_requires_deadline_idempotency_and_policy() -> None:
    request = StrategyExecutionRequest(
        tenant_id="tenant-1",
        goal_id="goal-1",
        strategy_id="react",
        agent_id="agent-1",
        runtime_profile_ref="profile-1",
        context_snapshot_ref="context-1",
        policy_ref="policy-1",
        budget_ref="budget-1",
        cancellation_token="cancel-1",
        deadline=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        idempotency_key="goal-1:react:attempt-1",
    )
    _assert_stable_json(request)

    invalid_cases = (
        ({"tenant_id": ""}, ("tenant_id",), "string_too_short"),
        ({"goal_id": ""}, ("goal_id",), "string_too_short"),
        ({"strategy_id": ""}, ("strategy_id",), "string_too_short"),
        ({"idempotency_key": ""}, ("idempotency_key",), "string_too_short"),
        ({"policy_ref": ""}, ("policy_ref",), "string_too_short"),
        (
            {"deadline": datetime(2026, 8, 3, 12, 0)},
            ("deadline",),
            "value_error",
        ),
        (
            {
                "deadline": datetime(
                    2026,
                    8,
                    3,
                    12,
                    0,
                    tzinfo=timezone(timedelta(hours=5, minutes=30)),
                )
            },
            ("deadline",),
            "value_error",
        ),
    )
    for update, location, error_type in invalid_cases:
        with pytest.raises(ValidationError) as exc_info:
            StrategyExecutionRequest.model_validate({**request.model_dump(), **update})
        _assert_validation_error(exc_info.value, location=location, error_type=error_type)

    for missing_field in ("tenant_id", "goal_id", "idempotency_key", "policy_ref"):
        values = request.model_dump()
        values.pop(missing_field)
        with pytest.raises(ValidationError) as exc_info:
            StrategyExecutionRequest.model_validate(values)
        _assert_validation_error(
            exc_info.value,
            location=(missing_field,),
            error_type="missing",
        )


def test_execution_result_excludes_private_reasoning() -> None:
    result = _execution_result(
        trace_summary={
            "status": "allowed",
            "counts": {"calls": 2},
        }
    )
    _assert_stable_json(result)
    serialized = result.model_dump_json()
    assert "private_reasoning" not in serialized
    assert "chain_of_thought" not in serialized
    assert "hidden_reasoning" not in serialized

    for forbidden_field in (
        "private_reasoning",
        "chain_of_thought",
        "hidden_reasoning",
        "raw_thoughts",
    ):
        with pytest.raises(ValidationError) as exc_info:
            StrategyExecutionResult.model_validate(
                {**result.model_dump(), forbidden_field: "secret reasoning"}
            )
        _assert_validation_error(
            exc_info.value,
            location=(forbidden_field,),
            error_type="extra_forbidden",
        )


def test_trace_summary_rejects_forbidden_reasoning_names() -> None:
    for forbidden_field in (
        "private_reasoning",
        "chain_of_thought",
        "hidden_reasoning",
        "raw_thoughts",
    ):
        with pytest.raises(ValidationError):
            _execution_result(trace_summary={forbidden_field: "secret reasoning"})


def test_free_form_trace_details_are_forbidden() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _execution_result(trace_summary={"details": {"quality_score": 0.95}})
    _assert_validation_error(
        exc_info.value,
        location=("trace_summary", "details"),
        error_type="extra_forbidden",
    )

    readiness = {
        "strategy_id": "react",
        "adapter_version": "2.0.0",
        "state_schema_version": 3,
        "status": ProbeStatus.READY,
        "checked_at": datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        "static_evidence": (
            EvidenceReference(
                kind=EvidenceKind.CONTRACT_TEST,
                reference="test://strategy-contracts",
                digest="sha256:static",
            ),
        ),
        "operational_evidence": (
            EvidenceReference(
                kind=EvidenceKind.METRIC,
                reference="metric://provider-health",
                digest="sha256:operational",
            ),
        ),
        "safe_rationale_summary": "Readiness checks passed.",
        "details": {"latency_ms": 12},
    }
    with pytest.raises(ValidationError) as exc_info:
        ReadinessProbe.model_validate(readiness)
    _assert_validation_error(
        exc_info.value,
        location=("details",),
        error_type="extra_forbidden",
    )

    certification = {
        "strategy_id": "react",
        "adapter_version": "2.0.0",
        "state_schema_version": 3,
        "kind": CertificationKind.LOAD,
        "status": CertificationStatus.PASSED,
        "evidence": EvidenceReference(
            kind=EvidenceKind.METRIC,
            reference="metric://load/123",
            digest="sha256:load",
        ),
        "recorded_at": datetime(2026, 8, 3, 12, 5, tzinfo=UTC),
        "safe_rationale_summary": "Load checks passed.",
        "details": {"p95_latency_ms": 18},
    }
    with pytest.raises(ValidationError) as exc_info:
        CertificationEvidence.model_validate(certification)
    _assert_validation_error(
        exc_info.value,
        location=("details",),
        error_type="extra_forbidden",
    )


def test_nested_contract_values_are_deeply_immutable() -> None:
    result = _execution_result(
        trace_summary={
            "reason_codes": ["policy_allowed"],
            "selected_ids": ["react"],
            "counts": {"calls": 2},
        }
    )
    migration = CheckpointMigration(
        from_adapter_version="1.4.0",
        to_adapter_version="2.0.0",
        from_state_schema_version=2,
        to_state_schema_version=3,
        migration_id="react-state-v2-to-v3",
        migrated_at=datetime(2026, 8, 3, 11, 55, tzinfo=UTC),
    )

    with pytest.raises(ValidationError):
        result.trace_summary.reason_codes = ("changed",)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.trace_summary.counts[0].value = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        migration.migration_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_numeric_contract_fields_reject_non_finite_values(non_finite: float) -> None:
    limits = _limits()
    for field_name in PatternLimits.model_fields:
        with pytest.raises(ValidationError):
            PatternLimits.model_validate({**limits.model_dump(), field_name: non_finite})

    result = _execution_result(trace_summary={"status": "completed"})
    with pytest.raises(ValidationError):
        StrategyExecutionResult.model_validate(
            {**result.model_dump(), "cost_usd": non_finite}
        )

    for trace_summary in (
        {"cost_usd": non_finite},
        {"duration_seconds": non_finite},
        {"counts": {"calls": non_finite}},
    ):
        with pytest.raises(ValidationError):
            _execution_result(trace_summary=trace_summary)


@pytest.mark.parametrize(
    "invalid_value",
    [
        "2",
        b"2",
        Decimal("2"),
        True,
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        {},
        [],
    ],
)
def test_trace_counters_reject_non_numeric_or_unsafe_values(
    invalid_value: object,
) -> None:
    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"counts": {"calls": invalid_value}})


@pytest.mark.parametrize("invalid_value", (CustomInteger(1), CustomFloat(1.0)))
def test_trace_counters_reject_numeric_subclasses(invalid_value: object) -> None:
    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"counts": {"calls": invalid_value}})


def test_trace_counters_reject_invalid_or_duplicate_keys() -> None:
    for invalid_key in ("Calls", "call-count", "call count", "private_reasoning"):
        with pytest.raises(ValidationError):
            _execution_result(trace_summary={"counts": {invalid_key: 1}})

    with pytest.raises(ValidationError):
        _execution_result(
            trace_summary={
                "counts": (
                    {"key": "calls", "value": 1},
                    {"key": "calls", "value": 2},
                )
            }
        )


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "private_reasoning",
        "private_reasoning_tokens",
        "chain_of_thought",
        "chain_of_thought_steps",
        "hidden_reasoning",
        "hidden_reasoning_count",
        "foo.hidden_reasoning.count",
        "raw_thoughts",
        "raw_thoughts_count",
        "raw_thoughts-count",
    ),
)
def test_trace_counters_reject_embedded_private_reasoning_namespaces(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValidationError):
        _execution_result(trace_summary={"counts": {forbidden_key: 1}})


def test_semantic_versions_enforce_prerelease_numeric_identifiers() -> None:
    spec = StrategySpec(
        strategy_id="react",
        adapter_version="1.0.0",
        family=StrategyFamily.REASONING,
        risk=StrategyRiskLevel.LOW,
        cost=StrategyCostLevel.LOW,
        latency=StrategyLatencyClass.REALTIME,
        state_schema_version=1,
        lifecycle_state=StrategyLifecycleState.IMPLEMENTED,
        default_limits=_limits(),
    )

    for valid_version in (
        "1.0.0-0",
        "1.0.0-alpha.1",
        "1.0.0-x.7.z.92+build.5",
        "1.0.0+build.01",
    ):
        assert StrategySpec.model_validate(
            {**spec.model_dump(), "adapter_version": valid_version}
        ).adapter_version == valid_version

    for invalid_version in (
        "1.0.0-01",
        "1.0.0-alpha.01",
        "1\u0661.0.0",
        "1\uff11.0.0",
        "1.0.0-1\u0661",
        "1.0.0-1\uff11",
    ):
        with pytest.raises(ValidationError):
            StrategySpec.model_validate(
                {**spec.model_dump(), "adapter_version": invalid_version}
            )


@pytest.mark.parametrize(
    "invalid_version",
    (
        " 1.2.3",
        "1.2.3 ",
        "\t1.2.3",
        "1.2.3\t",
        "\n1.2.3",
        "1.2.3\n",
    ),
)
def test_semantic_versions_reject_surrounding_whitespace(
    invalid_version: str,
) -> None:
    with pytest.raises(ValidationError):
        StrategySpec(
            strategy_id="react",
            adapter_version=invalid_version,
            family=StrategyFamily.REASONING,
            risk=StrategyRiskLevel.LOW,
            cost=StrategyCostLevel.LOW,
            latency=StrategyLatencyClass.REALTIME,
            state_schema_version=1,
            lifecycle_state=StrategyLifecycleState.IMPLEMENTED,
            default_limits=_limits(),
        )


def test_trace_summary_machine_tokens_are_strict_and_prose_stays_supported() -> None:
    result = _execution_result(
        trace_summary={
            "phase": "policy_allowed",
            "status": "limit.cost",
            "reason_codes": ("react-v1",),
            "limit_type": "cost_usd",
            "selected_ids": ("react-v1",),
            "rejected_ids": ("legacy.react",),
        }
    )
    assert result.safe_rationale_summary == (
        "Execution met the policy and evidence requirements."
    )

    invalid_tokens = (
        "two words",
        "Uppercase",
        " padded",
        "padded ",
        "line\nbreak",
        "control\x00character",
        "a" * 65,
        ".leading",
        "trailing-",
    )
    for invalid_token in invalid_tokens:
        for field_name in ("phase", "status", "limit_type"):
            with pytest.raises(ValidationError):
                _execution_result(trace_summary={field_name: invalid_token})
        for field_name in ("reason_codes", "selected_ids", "rejected_ids"):
            with pytest.raises(ValidationError):
                _execution_result(trace_summary={field_name: (invalid_token,)})


def test_trace_summary_serialization_is_canonical_for_mapping_input() -> None:
    first = _execution_result(
        trace_summary={
            "status": "completed",
            "counts": {"nodes": 3.5, "calls": 0},
        }
    )
    second = _execution_result(
        trace_summary={
            "counts": {"calls": 0, "nodes": 3.5},
            "status": "completed",
        }
    )

    assert first.trace_summary == second.trace_summary
    assert first.model_dump_json() == second.model_dump_json()
    assert tuple(counter.key for counter in first.trace_summary.counts) == (
        "calls",
        "nodes",
    )


def test_checkpoint_records_adapter_and_state_schema_versions() -> None:
    migration = CheckpointMigration(
        from_adapter_version="1.4.0",
        to_adapter_version="2.0.0",
        from_state_schema_version=2,
        to_state_schema_version=3,
        migration_id="react-state-v2-to-v3",
        migrated_at=datetime(2026, 8, 3, 11, 55, tzinfo=UTC),
    )
    checkpoint = StrategyCheckpoint(
        strategy_id="react",
        adapter_version="2.0.0",
        state_schema_version=3,
        cursor="step-4",
        state_ref="checkpoint://react/goal-1/step-4",
        migration=migration,
        created_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )
    _assert_stable_json(checkpoint)
    assert checkpoint.adapter_version == "2.0.0"
    assert checkpoint.state_schema_version == 3
    assert checkpoint.migration == migration

    probe = ReadinessProbe(
        strategy_id="react",
        adapter_version="2.0.0",
        state_schema_version=3,
        status=ProbeStatus.READY,
        checked_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        static_evidence=(
            EvidenceReference(
                kind=EvidenceKind.CONTRACT_TEST,
                reference="test://strategy-contracts",
                digest="sha256:static-probe",
            ),
        ),
        operational_evidence=(
            EvidenceReference(
                kind=EvidenceKind.METRIC,
                reference="metric://provider-health",
                digest="sha256:operational-probe",
            ),
        ),
        safe_rationale_summary="Static and operational readiness checks passed.",
    )
    certification = CertificationEvidence(
        strategy_id="react",
        adapter_version="2.0.0",
        state_schema_version=3,
        kind=CertificationKind.RESTART,
        status=CertificationStatus.PASSED,
        evidence=EvidenceReference(
            kind=EvidenceKind.TEST_RESULT,
            reference="test://restart/456",
            digest="sha256:certification",
        ),
        recorded_at=datetime(2026, 8, 3, 12, 5, tzinfo=UTC),
        safe_rationale_summary="Restart recovery preserved accepted state.",
    )
    _assert_stable_json(probe)
    _assert_stable_json(certification)

    invalid_cases = (
        ({"adapter_version": "2"}, ("adapter_version",), "string_pattern_mismatch"),
        ({"state_schema_version": 0}, ("state_schema_version",), "greater_than"),
    )
    for update, location, error_type in invalid_cases:
        with pytest.raises(ValidationError) as exc_info:
            StrategyCheckpoint.model_validate({**checkpoint.model_dump(), **update})
        _assert_validation_error(exc_info.value, location=location, error_type=error_type)


def test_readiness_probe_requires_static_and_operational_evidence() -> None:
    payload = {
        "strategy_id": "react",
        "adapter_version": "2.0.0",
        "state_schema_version": 3,
        "status": ProbeStatus.READY,
        "checked_at": datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        "static_evidence": (
            EvidenceReference(
                kind=EvidenceKind.CONTRACT_TEST,
                reference="test://strategy-contracts",
                digest="sha256:static",
            ),
        ),
        "operational_evidence": (
            EvidenceReference(
                kind=EvidenceKind.METRIC,
                reference="metric://provider-health",
                digest="sha256:operational",
            ),
        ),
        "safe_rationale_summary": "Static and operational checks passed.",
    }
    _assert_stable_json(ReadinessProbe.model_validate(payload))

    for field_name in ("static_evidence", "operational_evidence"):
        with pytest.raises(ValidationError):
            ReadinessProbe.model_validate({**payload, field_name: ()})


def test_certification_kinds_include_security() -> None:
    assert {kind.value for kind in CertificationKind} == {
        "unit",
        "integration",
        "restart",
        "policy",
        "security",
        "cost",
        "load",
        "canary",
    }
    evidence = CertificationEvidence(
        strategy_id="react",
        adapter_version="2.0.0",
        state_schema_version=3,
        kind=CertificationKind.SECURITY,
        status=CertificationStatus.PASSED,
        evidence=EvidenceReference(
            kind=EvidenceKind.TEST_RESULT,
            reference="test://security/789",
            digest="sha256:security",
        ),
        recorded_at=datetime(2026, 8, 3, 12, 5, tzinfo=UTC),
        safe_rationale_summary="Security checks passed.",
    )
    _assert_stable_json(evidence)