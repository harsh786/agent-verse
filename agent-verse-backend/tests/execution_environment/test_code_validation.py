from __future__ import annotations

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.models import CodeExecutionWorkload, CodeWorkloadMode


def _workload(source: str) -> CodeExecutionWorkload:
    return CodeExecutionWorkload.create(
        workload_id="workload",
        mode=CodeWorkloadMode.CODEACT,
        source=source,
        stdin_json=None,
        expected_output_schema={"type": "object"},
        requested_artifacts=(),
    )


def test_benign_computation_and_allowed_imports_pass() -> None:
    source = "import math\nfrom statistics import mean\nresult = mean([math.sqrt(4), 4])"
    assert CodeWorkloadValidator().validate(_workload(source)) == ()


def test_dynamic_execution_and_host_imports_are_denied() -> None:
    for source in (
        "import os\nresult = os.environ",
        "from pathlib import Path\nresult = Path('/')",
        "result = eval('2+2')",
        "result = (1).__class__",
        "result = __import__('socket')",
    ):
        assert CodeWorkloadValidator().validate(_workload(source)), source


def test_syntax_and_relative_imports_fail_closed() -> None:
    assert {item.code for item in CodeWorkloadValidator().validate(_workload("if:"))} == {
        "syntax_invalid"
    }
    violations = CodeWorkloadValidator().validate(_workload("from . import helper"))
    assert {item.code for item in violations} == {
        "import_denied",
        "relative_import_denied",
    }
