"""Tests for app.execution_environment.code_worker — the minimal code-interpreter worker.

main() is invoked in-process (like test_worker_entrypoint.py) by manipulating
os.environ, so these are fast unit tests with no subprocess/Docker dependency.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from unittest.mock import patch

import pytest

from app.execution_environment.code_worker import _emit, _matches_schema, _safe_import
from app.execution_environment.envelope import build_envelope
from app.execution_environment.models import (
    CodeExecutionWorkload,
    CodeWorkloadMode,
    ExecutionKind,
)


def _make_workload(
    source: str,
    *,
    schema: dict | None = None,
    stdin_json=None,
) -> CodeExecutionWorkload:
    src_hash = hashlib.sha256(source.encode()).hexdigest()
    return CodeExecutionWorkload(
        workload_id="wl-1",
        mode=CodeWorkloadMode.PROGRAM_OF_THOUGHT,
        source=source,
        stdin_json=stdin_json,
        expected_output_schema=schema or {"type": "integer"},
        source_sha256=src_hash,
    )


def _envelope_env(workload: CodeExecutionWorkload) -> dict:
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        execution_kind=ExecutionKind.CODE_INTERPRETER,
        code_workload=workload,
    )
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"_ISOLATED_WORKER_ENVELOPE": encoded}


def _run_main(env: dict) -> tuple[list[dict], int]:
    from app.execution_environment import code_worker as cw

    events: list[dict] = []
    original_emit = cw._emit

    def capture(value: dict) -> None:
        events.append(value)

    cw._emit = capture  # type: ignore[assignment]
    try:
        with patch.dict(os.environ, env, clear=False):
            rc = cw.main()
    finally:
        cw._emit = original_emit  # type: ignore[assignment]
    return events, rc


# ---------------------------------------------------------------------------
# _emit
# ---------------------------------------------------------------------------


def test_emit_prints_sorted_json(capsys) -> None:
    _emit({"b": 1, "a": 2})
    out = capsys.readouterr().out.strip()
    assert out == '{"a":2,"b":1}'


# ---------------------------------------------------------------------------
# _safe_import
# ---------------------------------------------------------------------------


def test_safe_import_allows_allowed_module() -> None:
    mod = _safe_import("json")
    assert mod is not None
    assert mod.__name__ == "json"


def test_safe_import_denies_disallowed_module() -> None:
    with pytest.raises(ImportError):
        _safe_import("os")


def test_safe_import_denies_relative_import() -> None:
    with pytest.raises(ImportError):
        _safe_import("json", level=1)


# ---------------------------------------------------------------------------
# _matches_schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        ({"a": 1}, {"type": "object"}, True),
        ([1, 2], {"type": "object"}, False),
        ([1, 2], {"type": "array"}, True),
        ("x", {"type": "string"}, True),
        (1, {"type": "string"}, False),
        (5, {"type": "integer"}, True),
        (True, {"type": "integer"}, False),
        (5.0, {"type": "number"}, True),
        (True, {"type": "number"}, False),
        (True, {"type": "boolean"}, True),
        (None, {"type": "null"}, True),
        (1, {"type": "null"}, False),
        (5, {}, True),
    ],
)
def test_matches_schema_type_checks(value, schema, expected) -> None:
    assert _matches_schema(value, schema) is expected


def test_matches_schema_object_missing_required_key_fails() -> None:
    schema = {"type": "object", "required": ["x"]}
    assert _matches_schema({"y": 1}, schema) is False
    assert _matches_schema({"x": 1}, schema) is True


def test_matches_schema_required_not_a_list_fails() -> None:
    schema = {"type": "object", "required": "x"}
    assert _matches_schema({"x": 1}, schema) is False


def test_matches_schema_unknown_type_returns_false() -> None:
    assert _matches_schema(5, {"type": "weird"}) is False


# ---------------------------------------------------------------------------
# main() — envelope failures
# ---------------------------------------------------------------------------


def test_main_fails_on_missing_envelope() -> None:
    events, rc = _run_main({"_ISOLATED_WORKER_ENVELOPE": ""})
    assert rc == 1
    assert events[-1]["success"] is False
    assert events[-1]["status"] == "failed"
    assert "invalid envelope" in events[-1]["error_message"]


def test_main_fails_on_invalid_base64() -> None:
    events, rc = _run_main({"_ISOLATED_WORKER_ENVELOPE": "!!!not-base64!!!"})
    assert rc == 1
    assert events[-1]["status"] == "failed"


def test_main_fails_when_no_code_workload() -> None:
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="no code")
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    events, rc = _run_main({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 1
    assert events[-1]["status"] == "failed"
    assert "verification failed" in events[-1]["error_message"]


def test_main_fails_when_envelope_tampered() -> None:
    workload = _make_workload("result = 1 + 1")
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        execution_kind=ExecutionKind.CODE_INTERPRETER,
        code_workload=workload,
    )
    payload = envelope.to_dict()
    payload["goal_text"] = "TAMPERED"
    payload["signature"] = envelope.signature

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    events, rc = _run_main({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 1
    assert events[-1]["status"] == "failed"


def test_main_denies_workload_violating_policy() -> None:
    # "import os" is denied by the code policy validator.
    workload = _make_workload("import os\nresult = 1")
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 1
    assert events[-1]["status"] == "denied"
    assert "policy denied" in events[-1]["error_message"]


# ---------------------------------------------------------------------------
# main() — successful execution
# ---------------------------------------------------------------------------


def test_main_executes_simple_program_successfully() -> None:
    workload = _make_workload("result = 2 + 2", schema={"type": "integer"})
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 0
    final = events[-1]
    assert final["success"] is True
    assert final["status"] == "completed"
    obs = final["code_observation"]
    assert obs["result_json"] == 4
    assert obs["terminal_state"] == "completed"
    assert obs["exit_code"] == 0


def test_main_uses_stdin_json_in_namespace() -> None:
    workload = _make_workload(
        "result = stdin_json['x'] * 2", schema={"type": "integer"}, stdin_json={"x": 21}
    )
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 0
    assert events[-1]["code_observation"]["result_json"] == 42


def test_main_fails_when_result_schema_mismatch() -> None:
    workload = _make_workload("result = 'not an int'", schema={"type": "integer"})
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 1
    final = events[-1]
    assert final["success"] is False
    assert final["status"] == "failed"
    obs = final["code_observation"]
    assert obs["stderr"] == "ValueError"
    assert obs["result_json"] is None


def test_main_fails_on_runtime_exception_in_program() -> None:
    workload = _make_workload("result = 1 / 0")
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 1
    obs = events[-1]["code_observation"]
    assert obs["stderr"] == "ZeroDivisionError"


def test_main_fails_when_builtin_not_in_safe_list_used() -> None:
    # 'print' passes static policy validation but is absent from the worker's
    # restricted __builtins__ dict, so it fails with a NameError at exec time.
    workload = _make_workload("print('hi')\nresult = 1")
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    assert rc == 1
    obs = events[-1]["code_observation"]
    assert obs["terminal_state"] == "failed"
    assert obs["stderr"] == "NameError"


def test_main_observation_has_valid_sha256_digest() -> None:
    workload = _make_workload("result = 10")
    env = _envelope_env(workload)

    events, rc = _run_main(env)
    obs = events[-1]["code_observation"]
    assert len(obs["observation_sha256"]) == 64
    int(obs["observation_sha256"], 16)  # must be valid hex
