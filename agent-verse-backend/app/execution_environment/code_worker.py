"""Dedicated minimal worker for validated code-interpreter workloads."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.envelope import envelope_from_dict, verify_envelope
from app.execution_environment.models import CodeExecutionObservation


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def _safe_import(
    name: str,
    globals_: Any = None,
    locals_: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    from app.execution_environment.python_policy import ALLOWED_MODULES

    if level or name not in ALLOWED_MODULES:
        raise ImportError("module denied by code policy")
    return __import__(name, globals_, locals_, fromlist, level)


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    expected = schema.get("type")
    if expected == "object" and not isinstance(value, dict):
        return False
    if expected == "array" and not isinstance(value, list):
        return False
    if expected == "string" and not isinstance(value, str):
        return False
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return False
    if expected == "number" and (not isinstance(value, int | float) or isinstance(value, bool)):
        return False
    if expected == "boolean" and not isinstance(value, bool):
        return False
    if expected == "null" and value is not None:
        return False
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            return False
    return expected in {None, "object", "array", "string", "integer", "number", "boolean", "null"}


def main() -> int:
    started = time.monotonic()
    raw = os.environ.get("_ISOLATED_WORKER_ENVELOPE", "")
    try:
        data = json.loads(base64.b64decode(raw).decode())
        envelope = envelope_from_dict(data)
    except Exception:
        _emit(
            {
                "_result": True,
                "success": False,
                "status": "failed",
                "error_message": "invalid envelope",
            }
        )
        return 1
    workload = envelope.code_workload
    if workload is None or not verify_envelope(envelope):
        _emit(
            {
                "_result": True,
                "success": False,
                "status": "failed",
                "error_message": "envelope verification failed",
            }
        )
        return 1
    violations = CodeWorkloadValidator().validate(workload)
    if violations:
        _emit(
            {
                "_result": True,
                "success": False,
                "status": "denied",
                "error_message": "code policy denied",
            }
        )
        return 1

    safe_builtins = {
        "__import__": _safe_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
    namespace: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "stdin_json": workload.stdin_json,
    }
    terminal = "completed"
    stderr = ""
    result: Any = None
    exit_code = 0
    try:
        exec(compile(workload.source, "<generated-program>", "exec"), namespace)
        result = namespace.get("result")
        if not _matches_schema(result, workload.expected_output_schema):
            raise ValueError("result does not match expected schema")
        json.dumps(result, allow_nan=False)
    except Exception as exc:
        terminal = "failed"
        stderr = type(exc).__name__
        exit_code = 1
        result = None
    payload = {
        "workload_id": workload.workload_id,
        "source_sha256": workload.source_sha256,
        "exit_code": exit_code,
        "terminal_state": terminal,
        "stdout": "",
        "stderr": stderr,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "result_json": result,
        "artifact_refs": [],
        "cpu_time_ms": 0,
        "wall_time_ms": max(0, int((time.monotonic() - started) * 1000)),
        "peak_memory_bytes": 0,
        "denial_codes": [],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    observation = CodeExecutionObservation.model_validate(
        {**payload, "observation_sha256": digest}
    )
    _emit(
        {
            "_result": True,
            "success": terminal == "completed",
            "status": terminal,
            "code_observation": observation.model_dump(mode="json"),
        }
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
