"""Verify blue/green switch script syntax."""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[2] / "infra" / "k8s" / "switch-traffic.sh"


def test_script_exists():
    assert SCRIPT_PATH.exists(), f"switch-traffic.sh not found at {SCRIPT_PATH}"


def test_script_syntax_is_valid():
    if not SCRIPT_PATH.exists():
        return
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script syntax error:\n{result.stderr}"


def test_script_exits_without_args():
    if not SCRIPT_PATH.exists():
        return
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1  # expects color argument
    assert "Usage:" in result.stdout or "Usage:" in result.stderr
