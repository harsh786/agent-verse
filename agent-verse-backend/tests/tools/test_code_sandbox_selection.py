"""Where CodeInterpreter runs a program: remote runner → Docker → dev subprocess → error.

The shipped workers have no Docker daemon, so without a configured runner every
workflow code step failed with "Docker sandbox unavailable ... Subprocess
execution is disabled". A configured runner (CODE_SANDBOX_URL) now wins, and is
authoritative: it never falls through to Docker or the host.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import app.tools.code_interpreter as ci
from app.core.config import get_settings
from app.tools.code_interpreter import CodeInterpreter, CodeResult

TOKEN = "selection-test-token-0123456789"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("CODE_SANDBOX_URL", raising=False)
    monkeypatch.delenv("CODE_SANDBOX_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure_remote(monkeypatch: pytest.MonkeyPatch, token: str = TOKEN) -> None:
    monkeypatch.setenv("CODE_SANDBOX_URL", "http://code-sandbox:8080")
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", token)
    get_settings.cache_clear()


class _Paths:
    """Records which execution path ran."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, docker: bool) -> None:
        self.calls: list[str] = []
        self.remote_args: dict[str, Any] = {}

        async def remote(_self: Any, code: str, language: str, timeout: float) -> CodeResult:
            self.calls.append("remote")
            self.remote_args = {"code": code, "language": language, "timeout": timeout}
            return CodeResult(stdout="remote\n", stderr="", exit_code=0)

        async def docker_exec(_self: Any, code: str, language: str, timeout: Any) -> CodeResult:
            self.calls.append("docker")
            return CodeResult(stdout="docker\n", stderr="", exit_code=0)

        def probe() -> bool:
            self.calls.append("probe")
            return docker

        monkeypatch.setattr("app.sandbox.client.RemoteSandboxClient.execute", remote)
        monkeypatch.setattr(CodeInterpreter, "_execute_docker", docker_exec)
        monkeypatch.setattr(ci, "_docker_available", probe)


async def test_configured_remote_runner_wins_over_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_remote(monkeypatch)
    paths = _Paths(monkeypatch, docker=True)
    res = await CodeInterpreter(default_timeout=17).execute("print(1)", "python")
    assert res.stdout == "remote\n"
    assert paths.calls == ["remote"]  # Docker is not even probed
    assert paths.remote_args == {"code": "print(1)", "language": "python", "timeout": 17}


async def test_explicit_timeout_reaches_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_remote(monkeypatch)
    paths = _Paths(monkeypatch, docker=False)
    await CodeInterpreter(default_timeout=30).execute("print(1)", "bash", 5)
    assert paths.remote_args["timeout"] == 5
    assert paths.remote_args["language"] == "bash"


async def test_without_a_runner_docker_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _Paths(monkeypatch, docker=True)
    res = await CodeInterpreter().execute("print(1)", "python")
    assert res.stdout == "docker\n"
    assert paths.calls == ["probe", "docker"]


async def test_without_runner_or_docker_the_dev_subprocess_opt_in_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
    paths = _Paths(monkeypatch, docker=False)
    res = await CodeInterpreter(default_timeout=10).execute("print('host')", "python")
    assert res.stdout.strip() == "host"
    assert paths.calls == ["probe"]


async def test_no_sandbox_in_development_explains_how_to_enable_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "false")
    _Paths(monkeypatch, docker=False)
    res = await CodeInterpreter().execute("print(1)", "python")
    assert res.exit_code == 1
    for hint in ("CODE_SANDBOX_URL", "CODE_SANDBOX_TOKEN", "docs/ops/code-sandbox.md"):
        assert hint in res.stderr
    assert "Subprocess execution is disabled" in res.stderr


async def test_no_sandbox_in_production_refuses_with_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")  # ignored in production
    _Paths(monkeypatch, docker=False)
    with pytest.raises(RuntimeError) as err:
        await CodeInterpreter().execute("print(1)", "python")
    assert "CODE_SANDBOX_URL" in str(err.value)
    assert "docs/ops/code-sandbox.md" in str(err.value)


async def test_configured_runner_without_token_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-configured runner never degrades to Docker or the host."""
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
    _configure_remote(monkeypatch, token="")
    calls: list[str] = []
    monkeypatch.setattr(ci, "_docker_available", lambda: calls.append("probe") or True)
    res = await CodeInterpreter().execute("print(1)", "python")
    assert res.exit_code == 1
    assert "CODE_SANDBOX_TOKEN is empty" in res.stderr
    assert calls == []


async def test_unreachable_runner_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
    monkeypatch.setenv("CODE_SANDBOX_URL", "http://127.0.0.1:9")  # discard port: refused
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", TOKEN)
    get_settings.cache_clear()
    calls: list[str] = []
    monkeypatch.setattr(ci, "_docker_available", lambda: calls.append("probe") or True)
    res = await CodeInterpreter().execute("print('must not run on the host')", "python", 5)
    assert res.exit_code == 1
    assert "unreachable" in res.stderr
    assert "must not run on the host" not in res.stdout
    assert calls == []


async def test_unsupported_language_is_refused_before_any_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_remote(monkeypatch)
    paths = _Paths(monkeypatch, docker=True)
    res = await CodeInterpreter().execute("x", "cobol")
    assert res.exit_code == 1 and "Unsupported language" in res.stderr
    assert paths.calls == []
