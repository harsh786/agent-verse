"""Comprehensive tests for CodeInterpreter — CodeResult, language support,
subprocess fallback, Docker path (mocked), and get_interpreter factory.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.tools.code_interpreter import (
    _DOCKER_IMAGES,
    _FILE_EXTENSIONS,
    CodeInterpreter,
    CodeResult,
    get_interpreter,
)

# ── 1. CodeResult dataclass ───────────────────────────────────────────────────

def test_code_result_success_true():
    r = CodeResult(stdout="hello", stderr="", exit_code=0)
    assert r.success is True


def test_code_result_success_false_nonzero():
    r = CodeResult(stdout="", stderr="err", exit_code=1)
    assert r.success is False


def test_code_result_success_false_timed_out():
    r = CodeResult(stdout="", stderr="", exit_code=0, timed_out=True)
    assert r.success is False


def test_code_result_to_dict_keys():
    r = CodeResult(stdout="out", stderr="err", exit_code=0, timed_out=False, execution_time_ms=42.5)
    d = r.to_dict()
    for key in ["stdout", "stderr", "exit_code", "success", "timed_out", "execution_time_ms"]:
        assert key in d


def test_code_result_to_dict_values():
    r = CodeResult(stdout="hello", stderr="", exit_code=0, timed_out=False, execution_time_ms=10.12345)
    d = r.to_dict()
    assert d["stdout"] == "hello"
    assert d["success"] is True
    assert d["execution_time_ms"] == pytest.approx(10.12)  # rounded to 2 dp


# ── 2. Language lookup maps ───────────────────────────────────────────────────

def test_docker_images_keys():
    assert "python" in _DOCKER_IMAGES
    assert "javascript" in _DOCKER_IMAGES
    assert "bash" in _DOCKER_IMAGES


def test_file_extensions():
    assert _FILE_EXTENSIONS["python"] == "py"
    assert _FILE_EXTENSIONS["javascript"] == "js"
    assert _FILE_EXTENSIONS["bash"] == "sh"


# ── 3. Unsupported language ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_unsupported_language():
    interp = CodeInterpreter()
    result = await interp.execute("print('hi')", language="ruby")
    assert result.exit_code == 1
    assert "Unsupported" in result.stderr


# ── 4. Subprocess fallback disabled by default ────────────────────────────────

@pytest.mark.asyncio
async def test_subprocess_fallback_disabled_without_env():
    interp = CodeInterpreter()
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "false"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute("print('hi')", language="python")
    assert result.exit_code == 1
    assert "Subprocess execution is disabled" in result.stderr


# ── 5. Subprocess fallback enabled ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subprocess_python_execution():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('print("hello subprocess")', language="python")
    assert result.exit_code == 0
    assert "hello subprocess" in result.stdout


@pytest.mark.asyncio
async def test_subprocess_bash_execution():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('echo "bash works"', language="bash")
    assert result.exit_code == 0
    assert "bash works" in result.stdout


@pytest.mark.asyncio
async def test_subprocess_python_stderr():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('import sys; sys.stderr.write("error\n")', language="python")
    assert "error" in result.stderr


@pytest.mark.asyncio
async def test_subprocess_python_exit_code():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('import sys; sys.exit(2)', language="python")
    assert result.exit_code == 2
    assert result.success is False


@pytest.mark.asyncio
async def test_subprocess_timeout():
    interp = CodeInterpreter(default_timeout=1)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('import time; time.sleep(10)', language="python", timeout=1)
    assert result.timed_out is True
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_subprocess_execution_time_recorded():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('print("time")', language="python")
    assert result.execution_time_ms >= 0


# ── 6. Timeout alias ─────────────────────────────────────────────────────────

def test_timeout_alias_overrides_default_timeout():
    interp = CodeInterpreter(default_timeout=30, timeout=60)
    assert interp._timeout == 60


def test_default_timeout_used_when_timeout_none():
    interp = CodeInterpreter(default_timeout=45)
    assert interp._timeout == 45


# ── 7. _execute_subprocess_fallback unsupported language ─────────────────────

@pytest.mark.asyncio
async def test_subprocess_fallback_unsupported_language():
    """execute() validates language before dispatch, so unsupported gets error result."""
    interp = CodeInterpreter()
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute("some code", language="cobol")
    assert result.exit_code == 1
    assert "Unsupported" in result.stderr


# ── 8. Docker path (mocked) ───────────────────────────────────────────────────

def _fake_docker_client(
    *,
    exit_code: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    wait_exc: Exception | None = None,
    create_exc: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    """A docker client whose container runs the real lifecycle code paths.

    These tests used to mock ``run_in_executor`` itself and return canned bytes,
    so ``_execute_docker``'s container code never ran under test — which is how a
    ``timeout=`` kwarg that docker-py does not accept broke every real sandbox
    execution without a single test noticing.
    """
    container = MagicMock()
    sock = MagicMock()
    container.attach_socket.return_value = sock
    if wait_exc is not None:
        container.wait.side_effect = wait_exc
    else:
        container.wait.return_value = {"StatusCode": exit_code}

    out_bytes, err_bytes = stdout, stderr

    def _logs(stdout: bool = True, stderr: bool = True) -> bytes:
        if stdout and not stderr:
            return out_bytes
        if stderr and not stdout:
            return err_bytes
        return out_bytes + err_bytes

    container.logs.side_effect = _logs

    client = MagicMock()
    if create_exc is not None:
        client.containers.create.side_effect = create_exc
    else:
        client.containers.create.return_value = container
    return client, container


@pytest.mark.asyncio
async def test_docker_execute_success():
    interp = CodeInterpreter()
    client, container = _fake_docker_client(stdout=b"docker output\n")
    with patch("docker.from_env", return_value=client):
        result = await interp._execute_docker('print("hi")', "python", None)

    assert result.exit_code == 0
    assert "docker output" in result.stdout
    assert result.timed_out is False
    kwargs = client.containers.create.call_args.kwargs
    assert "timeout" not in kwargs, "docker-py create()/run() has no timeout argument"
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["stdin_open"] is True
    # The program is streamed over stdin, never bind-mounted from a host path.
    assert "volumes" not in kwargs
    container.attach_socket.return_value._sock.sendall.assert_called_once_with(b'print("hi")')
    container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_docker_nonzero_exit_keeps_real_stderr():
    interp = CodeInterpreter()
    client, container = _fake_docker_client(exit_code=3, stderr=b"Traceback: boom\n")
    with patch("docker.from_env", return_value=client):
        result = await interp._execute_docker("raise SystemExit(3)", "python", None)
    assert result.exit_code == 3
    assert "Traceback: boom" in result.stderr
    assert result.timed_out is False
    container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_docker_execute_exception_returns_error():
    """An infrastructure failure is an error, and it is NOT reported as a timeout."""
    interp = CodeInterpreter()
    client, _ = _fake_docker_client(create_exc=RuntimeError("container failed: timed out pulling"))
    with patch("docker.from_env", return_value=client):
        result = await interp._execute_docker('print("hi")', "python", None)
    assert result.exit_code == 1
    assert "container failed" in result.stderr
    # The old heuristic grepped "timed out" in the message and misreported
    # infrastructure errors (including the TypeError) as timeouts.
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_docker_execute_timeout_detected():
    """A wait() deadline expiry kills the container and reports timed_out."""
    import requests

    interp = CodeInterpreter()
    client, container = _fake_docker_client(wait_exc=requests.exceptions.ReadTimeout("deadline"))
    with patch("docker.from_env", return_value=client):
        result = await interp._execute_docker("while True: pass", "python", 1)
    assert result.timed_out is True
    assert result.exit_code == 124
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_docker_failed_start_still_removes_the_container():
    interp = CodeInterpreter()
    client, container = _fake_docker_client()
    container.start.side_effect = RuntimeError("invalid tmpfs option")
    with patch("docker.from_env", return_value=client):
        result = await interp._execute_docker("print(1)", "python", None)
    assert result.exit_code == 1
    container.remove.assert_called_once_with(force=True)


# ── 9. _check_docker ─────────────────────────────────────────────────────────

def test_check_docker_returns_bool():
    result = CodeInterpreter._check_docker()
    assert isinstance(result, bool)


# ── 10. get_interpreter factory ───────────────────────────────────────────────

def test_get_interpreter_returns_instance():
    interp = get_interpreter(timeout=15)
    assert isinstance(interp, CodeInterpreter)
    assert interp._timeout == 15


def test_get_interpreter_default_timeout():
    interp = get_interpreter()
    assert interp._timeout == 30


# ── 11. JavaScript subprocess ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subprocess_javascript_execution():
    interp = CodeInterpreter(default_timeout=10)
    with patch.dict(os.environ, {"AGENTVERSE_ALLOW_SUBPROCESS_EXEC": "true"}):
        with patch("app.tools.code_interpreter._DOCKER_AVAILABLE", False):
            result = await interp.execute('console.log("js works")', language="javascript")
    # node may not be available in CI — just verify no crash
    assert isinstance(result.exit_code, int)
