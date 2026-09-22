"""Tests for the AgentVerse GitHub Action entrypoint.

`entrypoint.py` reads its configuration from environment variables at *import*
time (fail-fast if required vars are missing), so each test (re)imports the
module fresh via `load_entrypoint()` with the environment it needs.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import httpx
import pytest
import respx

ENTRYPOINT_PATH = Path(__file__).resolve().parent.parent / "entrypoint.py"

# Env vars entrypoint.py reads (required + optional-with-defaults).
_AGENTVERSE_ENV_VARS = (
    "AGENTVERSE_API_KEY",
    "AGENTVERSE_BASE_URL",
    "AGENTVERSE_GOAL",
    "AGENTVERSE_TIMEOUT",
    "AGENTVERSE_FAIL_ON_ERROR",
    "GITHUB_OUTPUT",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _AGENTVERSE_ENV_VARS:
        monkeypatch.delenv(key, raising=False)


def _import_fresh():
    """Load entrypoint.py as a brand-new module object (bypassing sys.modules cache)."""
    sys.modules.pop("entrypoint", None)
    spec = importlib.util.spec_from_file_location("entrypoint", ENTRYPOINT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_entrypoint(monkeypatch: pytest.MonkeyPatch, **env: str):
    """(Re)import entrypoint.py with a controlled environment.

    Supplies sane defaults for the two required vars so callers only need to
    override what the test cares about; pass e.g. AGENTVERSE_API_KEY=None-less
    (simply omit) — to actually test a *missing* var, delete it explicitly
    after calling this, or use `load_entrypoint_missing`.
    """
    _clear_env(monkeypatch)
    defaults = {
        "AGENTVERSE_API_KEY": "test-api-key",
        "AGENTVERSE_GOAL": "say hello",
    }
    defaults.update(env)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return _import_fresh()


class FakeSSEResponse:
    """Minimal stand-in for the object `urllib.request.urlopen` returns."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._lines)


# --------------------------------------------------------------------------
# Configuration / import-time behavior
# --------------------------------------------------------------------------


class TestConfiguration:
    def test_missing_api_key_raises_keyerror(self, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("AGENTVERSE_GOAL", "say hello")
        with pytest.raises(KeyError, match="AGENTVERSE_API_KEY"):
            _import_fresh()

    def test_missing_goal_raises_keyerror(self, monkeypatch):
        _clear_env(monkeypatch)
        monkeypatch.setenv("AGENTVERSE_API_KEY", "test-api-key")
        with pytest.raises(KeyError, match="AGENTVERSE_GOAL"):
            _import_fresh()

    def test_defaults_applied_for_optional_vars(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        assert module.BASE_URL == "http://localhost:8000"
        assert module.TIMEOUT == 300
        assert module.FAIL_ON_ERROR is True

    def test_base_url_trailing_slash_is_stripped(self, monkeypatch):
        module = load_entrypoint(monkeypatch, AGENTVERSE_BASE_URL="http://example.com/")
        assert module.BASE_URL == "http://example.com"

    def test_fail_on_error_false_is_parsed(self, monkeypatch):
        module = load_entrypoint(monkeypatch, AGENTVERSE_FAIL_ON_ERROR="false")
        assert module.FAIL_ON_ERROR is False

    def test_headers_include_api_key(self, monkeypatch):
        module = load_entrypoint(monkeypatch, AGENTVERSE_API_KEY="secret-123")
        assert module.HEADERS["X-API-Key"] == "secret-123"
        assert module.HEADERS["Content-Type"] == "application/json"

    def test_module_import_has_no_side_effects(self, monkeypatch):
        """Regression test: entrypoint.py used to call asyncio.run(main()) at
        module scope, so merely importing it (e.g. from a test) fired a real
        network request and could sys.exit the whole test process. It must
        now only run when executed as a script."""
        module = load_entrypoint(monkeypatch)
        assert hasattr(module, "main")
        # If import had side effects, we'd never reach this line without
        # a network error / SystemExit having already been raised above.


# --------------------------------------------------------------------------
# wait_for_completion_sse
# --------------------------------------------------------------------------


class TestWaitForCompletionSSE:
    def test_goal_complete_event_returns_complete(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse([b'data: {"type": "goal_complete"}\n'])
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "complete", "goal_id": "goal-1"}

    def test_goal_finished_event_returns_complete(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse([b'data: {"type": "goal_finished"}\n'])
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "complete", "goal_id": "goal-1"}

    def test_goal_failed_event_returns_failed_with_reason(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse(
            [b'data: {"type": "goal_failed", "reason": "tool exploded"}\n']
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "failed", "goal_id": "goal-1", "error": "tool exploded"}

    def test_goal_error_event_returns_failed(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse([b'data: {"type": "goal_error"}\n'])
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "failed", "goal_id": "goal-1", "error": None}

    def test_malformed_json_line_is_skipped(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse(
            [
                b"data: {not valid json\n",
                b'data: {"type": "goal_complete"}\n',
            ]
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "complete", "goal_id": "goal-1"}

    def test_non_data_lines_are_ignored(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse(
            [
                b": keep-alive comment\n",
                b"event: ping\n",
                b"\n",
                b'data: {"type": "goal_complete"}\n',
            ]
        )
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result == {"status": "complete", "goal_id": "goal-1"}

    def test_unknown_event_type_keeps_waiting_and_returns_none_at_stream_end(self, monkeypatch):
        module = load_entrypoint(monkeypatch)
        fake_resp = FakeSSEResponse([b'data: {"type": "step_started"}\n'])
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result is None

    def test_connection_error_returns_none(self, monkeypatch):
        module = load_entrypoint(monkeypatch)

        def _raise(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(module.urllib.request, "urlopen", _raise)

        result = module.wait_for_completion_sse("goal-1")

        assert result is None

    def test_breaks_and_returns_none_once_timeout_elapsed(self, monkeypatch):
        module = load_entrypoint(monkeypatch, AGENTVERSE_TIMEOUT="5")
        # First call is `start`, second is the in-loop check that exceeds TIMEOUT.
        times = iter([1000.0, 1010.0])
        monkeypatch.setattr(module.time, "time", lambda: next(times))
        # If the timeout check didn't short-circuit, this line would resolve
        # to a "complete" result — so seeing None proves the break fired.
        fake_resp = FakeSSEResponse([b'data: {"type": "goal_complete"}\n'])
        monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: fake_resp)

        result = module.wait_for_completion_sse("goal-1")

        assert result is None


# --------------------------------------------------------------------------
# main() — SSE happy/failure paths
# --------------------------------------------------------------------------


class TestMainSSEPath:
    @pytest.mark.asyncio
    async def test_successful_goal_via_sse_writes_outputs(self, monkeypatch, tmp_path, capsys):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(monkeypatch, GITHUB_OUTPUT=str(output_file))
        monkeypatch.setattr(
            module,
            "wait_for_completion_sse",
            lambda goal_id: {"status": "complete", "goal_id": goal_id},
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-42"})
            )
            respx.get("http://localhost:8000/goals/goal-42").mock(
                return_value=httpx.Response(
                    200, json={"result": "all done", "cost_usd": 0.42}
                )
            )
            await module.main()

        content = output_file.read_text()
        assert "goal-id=goal-42\n" in content
        assert "status=complete\n" in content
        assert "result=all done\n" in content
        assert "cost-usd=0.42\n" in content
        assert "Goal submitted: goal-42" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_sse_failed_exits_when_fail_on_error(self, monkeypatch, tmp_path):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(output_file), AGENTVERSE_FAIL_ON_ERROR="true"
        )
        monkeypatch.setattr(
            module,
            "wait_for_completion_sse",
            lambda goal_id: {"status": "failed", "goal_id": goal_id, "error": "boom"},
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-42"})
            )
            with pytest.raises(SystemExit) as exc_info:
                await module.main()

        assert exc_info.value.code == 1
        assert "status=failed\n" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_sse_failed_does_not_exit_when_fail_on_error_false(
        self, monkeypatch, tmp_path
    ):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(output_file), AGENTVERSE_FAIL_ON_ERROR="false"
        )
        monkeypatch.setattr(
            module,
            "wait_for_completion_sse",
            lambda goal_id: {"status": "failed", "goal_id": goal_id, "error": "boom"},
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-42"})
            )
            # Should return normally, no SystemExit.
            await module.main()

        assert "status=failed\n" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_sse_cancelled_status_treated_as_failure(self, monkeypatch, tmp_path):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(monkeypatch, GITHUB_OUTPUT=str(output_file))
        monkeypatch.setattr(
            module,
            "wait_for_completion_sse",
            lambda goal_id: {"status": "cancelled", "goal_id": goal_id},
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-42"})
            )
            with pytest.raises(SystemExit) as exc_info:
                await module.main()

        assert exc_info.value.code == 1
        assert "status=cancelled\n" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_result_fetch_failure_after_sse_complete_defaults_gracefully(
        self, monkeypatch, tmp_path
    ):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(monkeypatch, GITHUB_OUTPUT=str(output_file))
        monkeypatch.setattr(
            module,
            "wait_for_completion_sse",
            lambda goal_id: {"status": "complete", "goal_id": goal_id},
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-42"})
            )
            respx.get("http://localhost:8000/goals/goal-42").mock(
                return_value=httpx.Response(500, json={"error": "internal"})
            )
            await module.main()

        content = output_file.read_text()
        assert "status=complete\n" in content
        assert "result=\n" in content
        assert "cost-usd=0.0\n" in content


# --------------------------------------------------------------------------
# main() — polling fallback path (SSE unavailable)
# --------------------------------------------------------------------------


class TestMainPollingPath:
    @pytest.mark.asyncio
    async def test_polling_success_writes_outputs(self, monkeypatch, tmp_path):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(monkeypatch, GITHUB_OUTPUT=str(output_file))
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-7"})
            )
            respx.get("http://localhost:8000/goals/goal-7").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "complete", "result": "polled result", "cost_usd": 1.5},
                )
            )
            await module.main()

        content = output_file.read_text()
        assert "goal-id=goal-7\n" in content
        assert "status=complete\n" in content
        assert "result=polled result\n" in content
        assert "cost-usd=1.5\n" in content

    @pytest.mark.asyncio
    async def test_polling_failure_exits_when_fail_on_error(self, monkeypatch, tmp_path):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(monkeypatch, GITHUB_OUTPUT=str(output_file))
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-7"})
            )
            respx.get("http://localhost:8000/goals/goal-7").mock(
                return_value=httpx.Response(200, json={"status": "failed"})
            )
            with pytest.raises(SystemExit) as exc_info:
                await module.main()

        assert exc_info.value.code == 1
        assert "status=failed\n" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_polling_failure_does_not_exit_when_fail_on_error_false(
        self, monkeypatch, tmp_path
    ):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(output_file), AGENTVERSE_FAIL_ON_ERROR="false"
        )
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-7"})
            )
            respx.get("http://localhost:8000/goals/goal-7").mock(
                return_value=httpx.Response(200, json={"status": "cancelled"})
            )
            # Should return normally, no SystemExit.
            await module.main()

        assert "status=cancelled\n" in output_file.read_text()

    async def test_polling_timeout_exits_when_fail_on_error(self, monkeypatch, tmp_path):
        output_file = tmp_path / "github_output.txt"
        # TIMEOUT=0 makes the polling while-loop condition false immediately,
        # so we hit the timeout branch deterministically with no real waiting.
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(output_file), AGENTVERSE_TIMEOUT="0"
        )
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-7"})
            )
            with pytest.raises(SystemExit) as exc_info:
                await module.main()

        assert exc_info.value.code == 1
        assert "status=timeout\n" in output_file.read_text()

    @pytest.mark.asyncio
    async def test_polling_timeout_does_not_exit_when_fail_on_error_false(
        self, monkeypatch, tmp_path
    ):
        output_file = tmp_path / "github_output.txt"
        module = load_entrypoint(
            monkeypatch,
            GITHUB_OUTPUT=str(output_file),
            AGENTVERSE_TIMEOUT="0",
            AGENTVERSE_FAIL_ON_ERROR="false",
        )
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-7"})
            )
            await module.main()

        assert "status=timeout\n" in output_file.read_text()


# --------------------------------------------------------------------------
# main() — network errors and malformed responses on submission
# --------------------------------------------------------------------------


class TestMainSubmissionErrors:
    @pytest.mark.asyncio
    async def test_network_timeout_on_submit_propagates(self, monkeypatch, tmp_path):
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(tmp_path / "github_output.txt")
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                side_effect=httpx.ConnectTimeout("timed out")
            )
            with pytest.raises(httpx.ConnectTimeout):
                await module.main()

    @pytest.mark.asyncio
    async def test_http_error_status_on_submit_propagates(self, monkeypatch, tmp_path):
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(tmp_path / "github_output.txt")
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(500, json={"detail": "server error"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await module.main()

    @pytest.mark.asyncio
    async def test_malformed_submit_response_missing_goal_id_raises(
        self, monkeypatch, tmp_path
    ):
        module = load_entrypoint(
            monkeypatch, GITHUB_OUTPUT=str(tmp_path / "github_output.txt")
        )

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"unexpected": "shape"})
            )
            with pytest.raises(KeyError):
                await module.main()

    @pytest.mark.asyncio
    async def test_github_output_defaults_to_devnull_when_unset(self, monkeypatch):
        """GITHUB_OUTPUT is genuinely absent (not just pointing at a tmp file) —
        entrypoint.py should fall back to /dev/null rather than crash, which is
        what running outside of an actual GitHub Actions runner looks like."""
        module = load_entrypoint(monkeypatch)
        assert "GITHUB_OUTPUT" not in os.environ
        monkeypatch.setattr(module, "wait_for_completion_sse", lambda goal_id: None)

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

        with respx.mock:
            respx.post("http://localhost:8000/goals").mock(
                return_value=httpx.Response(200, json={"goal_id": "goal-9"})
            )
            respx.get("http://localhost:8000/goals/goal-9").mock(
                return_value=httpx.Response(200, json={"status": "complete", "result": "ok"})
            )
            # Must not raise even though nothing consumes /dev/null's content.
            await module.main()
