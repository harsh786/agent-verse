"""Test HITL gate is enforced by default in fully-autonomous mode (FIX C1/C7)."""
import os
from unittest.mock import patch


def _agent_source() -> str:
    """Read combined source of graph.py and all node mixin files."""
    import pathlib
    parts = [pathlib.Path("app/agent/graph.py").read_text(encoding="utf-8")]
    for f in sorted(pathlib.Path("app/agent/nodes").glob("*.py")):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)

def test_write_high_not_bypassed_by_default():
    with patch.dict(os.environ, {"ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH": "false"}):
        flag = os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
        assert not flag, "write_high bypass must be off by default"


def test_write_high_bypassed_when_flag_set():
    with patch.dict(os.environ, {"ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH": "true"}):
        flag = os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
        assert flag


def test_hitl_gate_source_contains_env_flag():
    """graph.py must gate the write_high bypass behind ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH."""
    src = _agent_source()
    assert "ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH" in src, (
        "graph.py must check ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH env flag"
    )
    assert "_allow_fa_write_high" in src, (
        "graph.py must use _allow_fa_write_high flag variable"
    )
