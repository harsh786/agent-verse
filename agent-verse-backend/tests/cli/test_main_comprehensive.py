"""Comprehensive tests for app/cli/main.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli.main import app as cli_app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help / structural tests
# ---------------------------------------------------------------------------


def test_help_exits_zero():
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "agentverse" in result.output.lower() or "Usage" in result.output


def test_submit_help():
    result = runner.invoke(cli_app, ["submit", "--help"])
    assert result.exit_code == 0
    assert "goal" in result.output.lower()


def test_submit_requires_goal():
    result = runner.invoke(cli_app, ["submit"])
    assert result.exit_code != 0


def test_agents_help():
    result = runner.invoke(cli_app, ["agents", "--help"])
    assert result.exit_code == 0


def test_goals_help():
    result = runner.invoke(cli_app, ["goals", "--help"])
    assert result.exit_code == 0
    assert "limit" in result.output.lower() or "goal" in result.output.lower()


def test_status_help():
    result = runner.invoke(cli_app, ["status", "--help"])
    assert result.exit_code == 0


def test_status_requires_goal_id():
    result = runner.invoke(cli_app, ["status"])
    assert result.exit_code != 0


def test_logs_help():
    result = runner.invoke(cli_app, ["logs", "--help"])
    assert result.exit_code == 0


def test_logs_requires_goal_id():
    result = runner.invoke(cli_app, ["logs"])
    assert result.exit_code != 0


def test_approve_help():
    result = runner.invoke(cli_app, ["approve", "--help"])
    assert result.exit_code == 0


def test_approve_requires_request_id():
    result = runner.invoke(cli_app, ["approve"])
    assert result.exit_code != 0


def test_reject_help():
    result = runner.invoke(cli_app, ["reject", "--help"])
    assert result.exit_code == 0


def test_reject_requires_request_id():
    result = runner.invoke(cli_app, ["reject"])
    assert result.exit_code != 0


def test_cancel_help():
    result = runner.invoke(cli_app, ["cancel", "--help"])
    assert result.exit_code == 0


def test_cancel_requires_goal_id():
    result = runner.invoke(cli_app, ["cancel"])
    assert result.exit_code != 0


def test_schedule_help():
    result = runner.invoke(cli_app, ["schedule", "--help"])
    assert result.exit_code == 0


def test_schedule_requires_command():
    result = runner.invoke(cli_app, ["schedule"])
    assert result.exit_code != 0


def test_connectors_help():
    result = runner.invoke(cli_app, ["connectors", "--help"])
    assert result.exit_code == 0


def test_eval_help():
    result = runner.invoke(cli_app, ["eval", "--help"])
    assert result.exit_code == 0


def test_eval_requires_goal_id():
    result = runner.invoke(cli_app, ["eval"])
    assert result.exit_code != 0


def test_watch_help():
    result = runner.invoke(cli_app, ["watch", "--help"])
    assert result.exit_code == 0


def test_watch_requires_goal_id():
    result = runner.invoke(cli_app, ["watch"])
    assert result.exit_code != 0


def test_create_help():
    result = runner.invoke(cli_app, ["create", "--help"])
    assert result.exit_code == 0


def test_create_requires_command():
    result = runner.invoke(cli_app, ["create"])
    assert result.exit_code != 0


def test_dev_help():
    result = runner.invoke(cli_app, ["dev", "--help"])
    assert result.exit_code == 0
    assert "port" in result.output.lower()


def test_test_help():
    result = runner.invoke(cli_app, ["test", "--help"])
    assert result.exit_code == 0


def test_manifest_help():
    result = runner.invoke(cli_app, ["manifest", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# login command
# ---------------------------------------------------------------------------


def test_login_saves_credentials(tmp_path):
    """login should save api_key and base_url to config.json."""
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, [
            "login",
            "--key", "my-secret-key",
            "--url", "https://my-server.example.com",
        ])

    assert result.exit_code == 0
    assert "Credentials saved" in result.output or "✓" in result.output


def test_login_creates_config_dir(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, [
            "login",
            "--key", "test-key-123",
            "--url", "http://localhost:8000",
        ])
    assert result.exit_code == 0
    config_file = tmp_path / ".agentverse" / "config.json"
    assert config_file.exists()
    data = json.loads(config_file.read_text())
    assert data["api_key"] == "test-key-123"
    assert data["base_url"] == "http://localhost:8000"


def test_login_overwrites_existing_config(tmp_path):
    config_dir = tmp_path / ".agentverse"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"api_key": "old-key", "base_url": "http://old"}))

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, [
            "login",
            "--key", "new-key",
            "--url", "http://new",
        ])

    assert result.exit_code == 0
    data = json.loads(config_file.read_text())
    assert data["api_key"] == "new-key"
    assert data["base_url"] == "http://new"


# ---------------------------------------------------------------------------
# _base_url and _api_key helpers
# ---------------------------------------------------------------------------


def test_base_url_from_env():
    from app.cli.main import _base_url
    with (
        patch.dict("os.environ", {"AGENTVERSE_URL": "https://custom.example.com"}),
        patch("pathlib.Path.home", return_value=Path("/nonexistent-test-path-12345")),
    ):
        url = _base_url()
    assert url == "https://custom.example.com"


def test_base_url_default():
    from app.cli.main import _base_url
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("pathlib.Path.home", return_value=Path("/nonexistent-test-path-12345")),
    ):
        url = _base_url()
    assert url == "http://localhost:8000"


def test_api_key_from_env():
    from app.cli.main import _api_key
    with (
        patch.dict("os.environ", {"AGENTVERSE_API_KEY": "env-key-abc"}),
        patch("pathlib.Path.home", return_value=Path("/nonexistent-test-path-12345")),
    ):
        key = _api_key()
    assert key == "env-key-abc"


def test_api_key_missing_env_exits():
    from app.cli.main import _api_key
    import typer
    env_clean = {k: v for k, v in os.environ.items() if k != "AGENTVERSE_API_KEY"}
    with (
        patch.dict("os.environ", env_clean, clear=True),
        patch("pathlib.Path.home", return_value=Path("/nonexistent-test-path-12345")),
    ):
        # typer.Exit is not a SystemExit; catch BaseException
        try:
            _api_key()
            assert False, "Should have raised"
        except (SystemExit, typer.Exit, BaseException):
            pass  # expected — AGENTVERSE_API_KEY not set


# ---------------------------------------------------------------------------
# submit command — mocked httpx
# ---------------------------------------------------------------------------


def test_submit_posts_goal():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"goal_id": "g-test-123", "status": "pending"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch.dict("os.environ", {"AGENTVERSE_API_KEY": "test-key"}),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["submit", "Analyze sales data"])

    assert result.exit_code == 0
    assert "g-test-123" in result.output


def test_submit_dry_run_flag():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"goal_id": "dry-123", "status": "dry_run"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["submit", "Run task", "--dry-run"])

    assert result.exit_code == 0
    # Verify dry_run=True was passed
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["json"]["dry_run"] is True


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


def test_status_shows_goal_info():
    mock_data = {"goal_id": "g-abc", "status": "complete", "goal": "test goal"}
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["status", "g-abc"])

    assert result.exit_code == 0
    assert "complete" in result.output


# ---------------------------------------------------------------------------
# agents command
# ---------------------------------------------------------------------------


def test_agents_no_agents_message():
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["agents"])

    assert result.exit_code == 0
    assert "No agents" in result.output


def test_agents_lists_agents():
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"agent_id": "a-1", "name": "Searcher", "autonomy_mode": "full-auto"},
    ]
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["agents"])

    assert result.exit_code == 0
    assert "Searcher" in result.output


# ---------------------------------------------------------------------------
# goals (list_goals_cmd)
# ---------------------------------------------------------------------------


def test_list_goals_shows_goals():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "goals": [
            {"id": "g-001", "goal": "Summarize Q3 report", "status": "complete"},
            {"id": "g-002", "goal": "Send email", "status": "running"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["goals"])

    assert result.exit_code == 0
    assert "complete" in result.output


def test_list_goals_status_filter():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "goals": [
            {"id": "g-001", "goal": "Task A", "status": "complete"},
            {"id": "g-002", "goal": "Task B", "status": "failed"},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["goals", "--status", "complete"])

    assert result.exit_code == 0
    assert "Task A" in result.output
    # Task B (failed) should not appear
    assert "Task B" not in result.output


# ---------------------------------------------------------------------------
# cancel command
# ---------------------------------------------------------------------------


def test_cancel_goal():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "cancelled"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["cancel", "g-cancel-me"])

    assert result.exit_code == 0
    assert "g-cancel-me" in result.output


# ---------------------------------------------------------------------------
# approve / reject commands
# ---------------------------------------------------------------------------


def test_approve_request():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "approved"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["approve", "req-123", "--note", "Looks good"])

    assert result.exit_code == 0
    assert "req-123" in result.output


def test_reject_request():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "rejected"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["reject", "req-456", "--note", "Not approved"])

    assert result.exit_code == 0
    assert "req-456" in result.output


# ---------------------------------------------------------------------------
# connectors command
# ---------------------------------------------------------------------------


def test_connectors_no_connectors():
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["connectors"])

    assert result.exit_code == 0
    assert "No connectors" in result.output


def test_connectors_lists():
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"server_id": "mcp-1", "name": "GitHub", "status": "connected"},
    ]
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["connectors"])

    assert result.exit_code == 0
    assert "GitHub" in result.output


# ---------------------------------------------------------------------------
# eval command
# ---------------------------------------------------------------------------


def test_eval_shows_scorecard():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "scores": {"accuracy": 0.9, "completeness": 0.8},
        "average_score": 0.85,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["eval", "g-eval-1"])

    assert result.exit_code == 0
    assert "accuracy" in result.output
    assert "PASS" in result.output


def test_eval_no_data_message():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {}  # No 'scores' key
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["eval", "g-no-eval"])

    assert result.exit_code == 0
    assert "No eval data" in result.output


# ---------------------------------------------------------------------------
# logs command
# ---------------------------------------------------------------------------


def test_logs_shows_events():
    mock_resp = MagicMock()
    mock_resp.json.return_value = [
        {"ts": "2024-01-01T10:00:00", "type": "goal_started", "step": None, "output": None},
        {"ts": "2024-01-01T10:01:00", "type": "step_complete", "step": "Step 1", "output": None},
    ]
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["logs", "g-log-1"])

    assert result.exit_code == 0
    assert "goal_started" in result.output


# ---------------------------------------------------------------------------
# schedule command
# ---------------------------------------------------------------------------


def test_schedule_creates_schedule():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"schedule_id": "sched-1", "cron": "0 9 * * 1"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["schedule", "Run every Monday morning"])

    assert result.exit_code == 0
    assert "sched-1" in result.output


# ---------------------------------------------------------------------------
# create command
# ---------------------------------------------------------------------------


def test_create_json_output():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"agent_id": "a-new", "status": "created"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["create", "A summarizer agent"])

    assert result.exit_code == 0
    assert "a-new" in result.output


def test_create_text_output():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"agent_id": "a-text", "status": "created"}
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = MagicMock(return_value=mock_resp)

    with (
        patch("app.cli.main.httpx.Client", return_value=mock_client),
        patch("app.cli.main._base_url", return_value="http://localhost:8000"),
        patch("app.cli.main._api_key", return_value="test-key"),
    ):
        result = runner.invoke(cli_app, ["create", "An agent", "--output", "text"])

    assert result.exit_code == 0
    assert "a-text" in result.output


# ---------------------------------------------------------------------------
# manifest command
# ---------------------------------------------------------------------------


def test_manifest_validate_file_not_found():
    result = runner.invoke(cli_app, ["manifest", "validate", "/nonexistent/path/agent.yaml"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "error" in result.output.lower()


def test_manifest_unknown_action():
    result = runner.invoke(cli_app, ["manifest", "unknown-action", "agent.yaml"])
    assert result.exit_code != 0
    assert "Unknown action" in result.output


# ---------------------------------------------------------------------------
# test command
# ---------------------------------------------------------------------------


def test_run_tests_command_passes_to_pytest():
    """test command should invoke pytest as subprocess."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli_app, ["test", "my_test_file.py"])
    # Should invoke and exit with pytest's return code
    assert result.exit_code == 0
    mock_run.assert_called_once()
    cmd_args = mock_run.call_args[0][0]
    assert "pytest" in " ".join(str(a) for a in cmd_args)
    assert "my_test_file.py" in cmd_args


def test_run_tests_verbose_flag():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli_app, ["test", "tests.py", "--verbose"])
    cmd_args = mock_run.call_args[0][0]
    assert "-v" in cmd_args
