"""CODE-06: tenant code runs only through app.tools.code_execution.execute_governed.

The "governed" CodeInterpreterTool in app/mcp/code_interpreter.py (approval,
budget and classification checks, a per-process idempotency cache) had no caller,
while the three live callers bypassed it. The live callers now share one
entrypoint (concurrency caps + durable audit) and the dead wrapper is gone; this
guard fails if a new caller constructs the raw interpreter directly.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED = {
    APP / "tools" / "code_interpreter.py",  # defines it
    APP / "tools" / "code_execution.py",  # the governed entrypoint
}


def test_only_the_governed_entrypoint_constructs_the_interpreter() -> None:
    offenders = [
        str(p.relative_to(APP.parent))
        for p in APP.rglob("*.py")
        if p not in ALLOWED and re.search(r"\bCodeInterpreter\(", p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"raw sandbox use bypasses execute_governed: {offenders}"


def test_dead_governed_wrapper_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.mcp.code_interpreter")
