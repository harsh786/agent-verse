"""The remote code-sandbox client (app/sandbox/client.py)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.sandbox.client import RemoteSandboxClient, RemoteSandboxConfig, remote_sandbox_config
from app.tools.code_execution import CodeExecutionBusyError

URL = "http://code-sandbox:8080"
TOKEN = "client-test-token-0123456789"


def _client(handler: Any, token: str = TOKEN) -> RemoteSandboxClient:
    return RemoteSandboxClient(
        RemoteSandboxConfig(url=URL, token=token), transport=httpx.MockTransport(handler)
    )


async def test_sends_code_with_the_bearer_token_and_maps_the_answer() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "stdout": "42\n",
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "output_truncated": False,
                "duration_ms": 12.5,
            },
        )

    res = await _client(handler).execute("print(42)", "python", 30)
    assert seen["url"] == f"{URL}/v1/execute"
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["body"] == {"language": "python", "code": "print(42)", "timeout_seconds": 30.0}
    assert (res.stdout, res.exit_code, res.timed_out, res.execution_time_ms) == (
        "42\n",
        0,
        False,
        12.5,
    )
    assert res.success


async def test_program_timeout_is_reported_as_timed_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"stdout": "", "stderr": "killed", "exit_code": 124, "timed_out": True},
        )

    res = await _client(handler).execute("while True: pass", "python", 1)
    assert res.timed_out and res.exit_code == 124 and not res.success


async def test_wrong_token_is_an_honest_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "missing or invalid sandbox token"})

    res = await _client(handler).execute("print(1)", "python", 5)
    assert res.exit_code == 1
    assert "rejected the credentials" in res.stderr
    assert "CODE_SANDBOX_TOKEN" in res.stderr
    assert TOKEN not in res.stderr


async def test_missing_token_never_calls_the_runner() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called without a token")

    res = await _client(handler, token="").execute("print(1)", "python", 5)
    assert res.exit_code == 1
    assert "CODE_SANDBOX_TOKEN is empty" in res.stderr


async def test_unreachable_runner_is_an_honest_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    res = await _client(handler).execute("print(1)", "python", 5)
    assert res.exit_code == 1 and not res.timed_out
    assert f"runner at {URL} is unreachable" in res.stderr


async def test_no_answer_in_time_counts_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    res = await _client(handler).execute("print(1)", "python", 5)
    assert res.timed_out and res.exit_code == 124
    assert "no answer" in res.stderr


async def test_busy_runner_raises_busy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "all 4 execution slots are busy", "limit": 4})

    with pytest.raises(CodeExecutionBusyError) as err:
        await _client(handler).execute("print(1)", "python", 5)
    assert (err.value.scope, err.value.limit) == ("sandbox", 4)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, {"error": "unsupported language 'javascript'"}, "unsupported language"),
        (413, {"error": "code exceeds 1000000 bytes"}, "code exceeds"),
        (500, {"error": "sandbox failed to run the program"}, "HTTP 500"),
    ],
)
async def test_runner_errors_are_reported(status: int, body: Any, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    res = await _client(handler).execute("x", "javascript", 5)
    assert res.exit_code == 1
    assert expected in res.stderr


async def test_malformed_answer_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>proxy error</html>")

    res = await _client(handler).execute("print(1)", "python", 5)
    assert res.exit_code == 1
    assert "malformed" in res.stderr


def test_config_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_SANDBOX_URL", "http://code-sandbox:8080/")
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", TOKEN)
    get_settings.cache_clear()
    try:
        cfg = remote_sandbox_config()
        assert cfg == RemoteSandboxConfig(url="http://code-sandbox:8080", token=TOKEN)
        assert TOKEN not in repr(cfg)
        assert TOKEN not in repr(get_settings())
        monkeypatch.setenv("CODE_SANDBOX_URL", "  ")
        get_settings.cache_clear()
        assert remote_sandbox_config() is None
    finally:
        get_settings.cache_clear()
