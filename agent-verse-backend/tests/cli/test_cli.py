"""Tests for the agentverse CLI (app/cli/main.py)."""
from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app as cli_app

runner = CliRunner()


def test_help_exits_zero():
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "agentverse" in result.output.lower() or "Usage" in result.output


def test_submit_requires_goal():
    """submit without its required positional arg should exit non-zero."""
    result = runner.invoke(cli_app, ["submit"])
    assert result.exit_code != 0  # Missing required arg


def test_agents_help_command():
    result = runner.invoke(cli_app, ["agents", "--help"])
    assert result.exit_code == 0


def test_goals_help_command():
    result = runner.invoke(cli_app, ["goals", "--help"])
    assert result.exit_code == 0


def test_logs_requires_goal_id():
    """logs without its required positional arg should exit non-zero."""
    result = runner.invoke(cli_app, ["logs"])
    assert result.exit_code != 0  # Missing required arg


def test_dev_command_help():
    result = runner.invoke(cli_app, ["dev", "--help"])
    assert result.exit_code == 0


def test_approve_help_command():
    result = runner.invoke(cli_app, ["approve", "--help"])
    assert result.exit_code == 0


def test_connectors_help_exits_without_key():
    """connectors --help shows help before any network call."""
    result = runner.invoke(cli_app, ["connectors", "--help"])
    assert result.exit_code == 0


def test_status_requires_goal_id():
    """status without its required positional arg should exit non-zero."""
    result = runner.invoke(cli_app, ["status"])
    assert result.exit_code != 0
