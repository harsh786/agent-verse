from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.execution_environment.models import (
    CodeExecutionWorkload,
    CodeLanguage,
    CodeWorkloadMode,
    ExecutionEnvelope,
    ExecutionKind,
)


def _workload(source: str = "result = 2 + 2") -> CodeExecutionWorkload:
    return CodeExecutionWorkload.create(
        workload_id="workload-1",
        mode=CodeWorkloadMode.PROGRAM_OF_THOUGHT,
        language=CodeLanguage.PYTHON_3_12,
        source=source,
        stdin_json={"value": 2},
        expected_output_schema={"type": "integer"},
        requested_artifacts=(),
    )


def test_workload_computes_and_verifies_source_digest() -> None:
    workload = _workload()
    assert workload.source_sha256 == hashlib.sha256(workload.source.encode()).hexdigest()
    with pytest.raises(ValidationError, match="source_sha256"):
        workload.model_copy(update={"source_sha256": "0" * 64}).model_validate(
            {**workload.model_dump(), "source_sha256": "0" * 64}
        )


def test_envelope_requires_exactly_one_payload() -> None:
    code = ExecutionEnvelope(
        tenant_id="tenant",
        goal_id="goal",
        execution_kind=ExecutionKind.CODE_INTERPRETER,
        code_workload=_workload(),
    )
    assert code.goal_text == ""
    with pytest.raises(ValueError, match="forbids code_workload"):
        ExecutionEnvelope(
            tenant_id="tenant",
            goal_id="goal",
            goal_text="agent goal",
            code_workload=_workload(),
        )
    with pytest.raises(ValueError, match="requires code_workload"):
        ExecutionEnvelope(
            tenant_id="tenant",
            goal_id="goal",
            execution_kind=ExecutionKind.CODE_INTERPRETER,
        )


def test_workload_rejects_unknown_language_and_unsafe_artifact_names() -> None:
    values = _workload().model_dump()
    with pytest.raises(ValidationError):
        CodeExecutionWorkload.model_validate({**values, "language": "javascript"})
    with pytest.raises(ValidationError, match="safe relative"):
        CodeExecutionWorkload.model_validate(
            {**values, "requested_artifacts": ["../secret"]}
        )
