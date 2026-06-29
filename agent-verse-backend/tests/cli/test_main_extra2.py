"""Extra coverage tests for app/cli/main.py — targeting 85%+ overall coverage."""
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
# _base_url / _api_key helpers
# ---------------------------------------------------------------------------

def test_base_url_reads_from_config_file(tmp_path):
    cfg_dir = tmp_path / ".agentverse"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.json"
    cfg.write_text(json.dumps({"base_url": "http://custom:9000", "api_key": "k1"}))
    with patch("pathlib.Path.home", return_value=tmp_path):
        from app.cli.main import _base_url
        result = _base_url()
        assert result == "http://custom:9000"


def test_base_url_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("AGENTVERSE_URL", "http://envhost:1234")
    with patch("pathlib.Path.exists", return_value=False):
        from app.cli.main import _base_url
        assert _base_url() == "http://envhost:1234"


def test_base_url_default_when_no_env(monkeypatch):
    monkeypatch.delenv("AGENTVERSE_URL", raising=False)
    with patch("pathlib.Path.exists", return_value=False):
        from app.cli.main import _base_url
        assert _base_url() == "http://localhost:8000"


def test_api_key_reads_from_config_file():
    cfg_content = json.dumps({"api_key": "from-config-file", "base_url": "http://x"})
    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=cfg_content):
        from app.cli.main import _api_key
        key = _api_key()
        assert key == "from-config-file"


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("AGENTVERSE_API_KEY", "env-key-xyz")
    with patch("pathlib.Path.exists", return_value=False):
        from app.cli.main import _api_key
        assert _api_key() == "env-key-xyz"


def test_api_key_missing_exits():
    with patch("pathlib.Path.exists", return_value=False), \
         patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(cli_app, ["goals"])
        # Should fail because no API key
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# login command
# ---------------------------------------------------------------------------

def test_login_saves_credentials(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["login", "--key", "myapikey", "--url", "http://srv"])
        assert result.exit_code == 0
        cfg_file = tmp_path / ".agentverse" / "config.json"
        assert cfg_file.exists()
        cfg = json.loads(cfg_file.read_text())
        assert cfg["api_key"] == "myapikey"
        assert cfg["base_url"] == "http://srv"


def test_login_merges_existing_config(tmp_path):
    cfg_dir = tmp_path / ".agentverse"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"other_key": "other_val"}))
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = runner.invoke(cli_app, ["login", "--key", "newkey"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# create command
# ---------------------------------------------------------------------------

def test_create_json_output():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"agent_id": "a-123", "status": "created"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["create", "Build a Jira bot"])
        assert result.exit_code == 0
        assert "a-123" in result.output


def test_create_text_output():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"agent_id": "a-456", "status": "created"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["create", "Build a Jira bot", "--output", "text"])
        assert result.exit_code == 0
        assert "a-456" in result.output


# ---------------------------------------------------------------------------
# submit command
# ---------------------------------------------------------------------------

def test_submit_basic():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"goal_id": "g-789", "status": "planning"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["submit", "Analyze logs"])
        assert result.exit_code == 0
        assert "g-789" in result.output


def test_submit_dry_run():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"goal_id": "dry-1", "status": "dry_run"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["submit", "test goal", "--dry-run"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

def test_status_command():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"goal_id": "g1", "status": "complete"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["status", "g1"])
        assert result.exit_code == 0
        assert "g1" in result.output


# ---------------------------------------------------------------------------
# goals (list_goals_cmd)
# ---------------------------------------------------------------------------

def test_goals_list_with_status_filter():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {
            "goals": [
                {"goal_id": "g1", "status": "complete", "goal": "task one"},
                {"goal_id": "g2", "status": "failed", "goal": "task two"},
            ]
        }
        result = runner.invoke(cli_app, ["goals", "--status", "complete"])
        assert result.exit_code == 0
        assert "g1" in result.output
        assert "g2" not in result.output


def test_goals_list_no_goals():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {"goals": []}
        result = runner.invoke(cli_app, ["goals"])
        assert result.exit_code == 0


def test_goals_list_with_limit():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {
            "goals": [
                {"goal_id": f"g{i}", "status": "complete", "goal": f"task {i}"}
                for i in range(30)
            ]
        }
        result = runner.invoke(cli_app, ["goals", "--limit", "5"])
        assert result.exit_code == 0
        # Only 5 shown
        assert result.output.count("task") <= 5


# ---------------------------------------------------------------------------
# cancel command
# ---------------------------------------------------------------------------

def test_cancel_command():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._post") as mock_post:
        mock_post.return_value = {"status": "cancelled"}
        result = runner.invoke(cli_app, ["cancel", "g-abc"])
        assert result.exit_code == 0
        assert "g-abc" in result.output


# ---------------------------------------------------------------------------
# approve / reject commands
# ---------------------------------------------------------------------------

def test_approve_command():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._post") as mock_post:
        mock_post.return_value = {"status": "approved"}
        result = runner.invoke(cli_app, ["approve", "req-1", "--note", "looks good"])
        assert result.exit_code == 0
        assert "req-1" in result.output


def test_reject_command():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._post") as mock_post:
        mock_post.return_value = {"status": "rejected"}
        result = runner.invoke(cli_app, ["reject", "req-2", "--note", "not safe"])
        assert result.exit_code == 0
        assert "req-2" in result.output


# ---------------------------------------------------------------------------
# connectors command
# ---------------------------------------------------------------------------

def test_connectors_empty():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = []
        result = runner.invoke(cli_app, ["connectors"])
        assert result.exit_code == 0
        assert "No connectors" in result.output


def test_connectors_non_empty():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = [
            {"server_id": "srv-1", "name": "Jira", "status": "healthy"},
        ]
        result = runner.invoke(cli_app, ["connectors"])
        assert result.exit_code == 0
        assert "Jira" in result.output


def test_connectors_dict_response():
    """When API returns a dict (not list), treat as empty."""
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {"connectors": []}
        result = runner.invoke(cli_app, ["connectors"])
        assert result.exit_code == 0
        assert "No connectors" in result.output


# ---------------------------------------------------------------------------
# eval command
# ---------------------------------------------------------------------------

def test_eval_no_scores():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {"goal_id": "g1"}
        result = runner.invoke(cli_app, ["eval", "g1"])
        assert result.exit_code == 0
        assert "No eval data" in result.output


def test_eval_with_scores():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {
            "goal_id": "g2",
            "scores": {"accuracy": 0.9, "efficiency": 0.8},
            "average_score": 0.85,
        }
        result = runner.invoke(cli_app, ["eval", "g2"])
        assert result.exit_code == 0
        assert "accuracy" in result.output
        assert "PASS" in result.output


def test_eval_below_threshold():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {
            "goal_id": "g3",
            "scores": {"accuracy": 0.5},
            "average_score": 0.5,
        }
        result = runner.invoke(cli_app, ["eval", "g3"])
        assert result.exit_code == 0
        assert "FAIL" in result.output


def test_eval_computes_average_from_scores():
    """When average_score is not in response, compute from scores."""
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {
            "goal_id": "g4",
            "scores": {"accuracy": 0.9, "speed": 0.9},
        }
        result = runner.invoke(cli_app, ["eval", "g4"])
        assert result.exit_code == 0
        assert "PASS" in result.output


# ---------------------------------------------------------------------------
# logs command
# ---------------------------------------------------------------------------

def test_logs_command():
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = [
            {"ts": "2024-01-01T00:00:00", "type": "step_started", "step": "step 1"},
            {"ts": "2024-01-01T00:00:01", "type": "tool_call", "tool_name": "search"},
        ]
        result = runner.invoke(cli_app, ["logs", "g-xyz"])
        assert result.exit_code == 0
        assert "step_started" in result.output


def test_logs_command_dict_response():
    """When events endpoint returns a dict (not list), shows nothing."""
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("app.cli.main._get") as mock_get:
        mock_get.return_value = {"message": "not a list"}
        result = runner.invoke(cli_app, ["logs", "g-xyz"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# schedule command
# ---------------------------------------------------------------------------

def test_schedule_command():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"schedule_id": "sched-1", "trigger_type": "cron"}
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["schedule", "every day at 9am", "--agent", "a1"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# agents command
# ---------------------------------------------------------------------------

def test_agents_empty():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = []
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["agents"])
        assert result.exit_code == 0
        assert "No agents" in result.output


def test_agents_non_empty():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"agent_id": "a1", "name": "ResearchBot", "autonomy_mode": "full"},
    ]
    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        result = runner.invoke(cli_app, ["agents"])
        assert result.exit_code == 0
        assert "ResearchBot" in result.output


# ---------------------------------------------------------------------------
# manifest command
# ---------------------------------------------------------------------------

def test_manifest_unknown_action():
    result = runner.invoke(cli_app, ["manifest", "deploy", "agent.yaml"])
    assert result.exit_code != 0
    assert "Unknown action" in result.output


def test_manifest_file_not_found():
    with patch("app.cli.main._api_key", return_value="key", side_effect=None):
        result = runner.invoke(cli_app, ["manifest", "validate", "nonexistent_file.yaml"])
        assert result.exit_code != 0


def test_manifest_validate_error():
    with patch("app.sdk.manifest.AgentManifest.from_yaml", side_effect=Exception("parse error")):
        result = runner.invoke(cli_app, ["manifest", "validate", "bad.yaml"])
        assert result.exit_code != 0
        assert "Error" in result.output


# ---------------------------------------------------------------------------
# dev server command
# ---------------------------------------------------------------------------

def test_dev_server_keyboard_interrupt():
    with patch("subprocess.run", side_effect=KeyboardInterrupt):
        result = runner.invoke(cli_app, ["dev"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()


def test_dev_server_error():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "uvicorn")):
        result = runner.invoke(cli_app, ["dev"])
        assert result.exit_code != 0


def test_dev_server_no_reload():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli_app, ["dev", "--no-reload"])
        assert result.exit_code == 0
        # --no-reload flag passed; check subprocess was called
        assert mock_run.called


# ---------------------------------------------------------------------------
# run_tests command
# ---------------------------------------------------------------------------

def test_run_tests_command():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli_app, ["test", "my_test_file.py"])
        assert result.exit_code == 0


def test_run_tests_verbose():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli_app, ["test", "my_test_file.py", "--verbose"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "-v" in call_args


def test_run_tests_failure_propagates():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=2)
        result = runner.invoke(cli_app, ["test", "my_test_file.py"])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# _stream_goal (via watch command)
# ---------------------------------------------------------------------------

def test_stream_goal_events():
    """Test _stream_goal processes various SSE event types."""
    import io
    from app.cli.main import _stream_goal

    events = [
        'data: {"type": "goal_started", "goal": "Deploy app"}',
        'data: {"type": "plan_ready", "steps": ["step1", "step2", "step3"]}',
        'data: {"type": "step_started", "step": "step 1"}',
        'data: {"type": "step_complete", "output": "done"}',
        'data: {"type": "waiting_approval", "action": "deploy prod", "request_id": "r1"}',
        'data: {"type": "goal_complete"}',
    ]

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.iter_lines.return_value = iter(events)

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_stream
        mock_client_cls.return_value = mock_client
        # Should not raise
        _stream_goal("g-stream")


def test_stream_goal_failed_event():
    """Test _stream_goal handles goal_failed events."""
    events = ['data: {"type": "goal_failed", "reason": "timeout"}']

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.iter_lines.return_value = iter(events)

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls, \
         patch("sys.exit"):  # prevent actual exit in test
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_stream
        mock_client_cls.return_value = mock_client
        from app.cli.main import _stream_goal
        _stream_goal("g-fail")


def test_stream_goal_keyboard_interrupt():
    """KeyboardInterrupt during streaming is handled gracefully."""
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.iter_lines.side_effect = KeyboardInterrupt

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_stream
        mock_client_cls.return_value = mock_client
        from app.cli.main import _stream_goal
        _stream_goal("g-interrupt")  # Should not raise


def test_stream_goal_invalid_json_line():
    """Non-JSON data lines are silently skipped."""
    events = ["data: not-valid-json"]

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.iter_lines.return_value = iter(events)

    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: mock_resp
    mock_stream.__exit__ = MagicMock(return_value=False)

    with patch("app.cli.main._api_key", return_value="key"), \
         patch("app.cli.main._base_url", return_value="http://localhost:8000"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_stream
        mock_client_cls.return_value = mock_client
        from app.cli.main import _stream_goal
        _stream_goal("g-badjson")  # Should not raise


# ---------------------------------------------------------------------------
# _get / _post helper functions
# ---------------------------------------------------------------------------

def test_get_helper():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"ok": True}
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        from app.cli.main import _get
        result = _get("http://localhost:8000/goals", "mykey")
        assert result == {"ok": True}


def test_post_helper():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"created": True}
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client
        from app.cli.main import _post
        result = _post("http://localhost:8000/goals", "mykey", {"goal": "test"})
        assert result == {"created": True}
