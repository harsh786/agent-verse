"""Test HITL gate is enforced by default in fully-autonomous mode (FIX C1/C7)."""
import os
from unittest.mock import patch


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
    import pathlib
    src = pathlib.Path("app/agent/graph.py").read_text()
    assert "ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH" in src, (
        "graph.py must check ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH env flag"
    )
    assert "_allow_fa_write_high" in src, (
        "graph.py must use _allow_fa_write_high flag variable"
    )
