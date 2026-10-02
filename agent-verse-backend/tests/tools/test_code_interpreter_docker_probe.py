"""WF-CODE-STEP-SUBPROCESS: the Docker probe is live, never pinned to "no".

The interpreter probed Docker once at import time; a process whose import hit
a transient daemon outage was pinned to the (disabled) subprocess fallback for
its whole life, so code steps failed intermittently per worker process.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from typing import Any

import pytest

import app.tools.code_interpreter as ci


@pytest.fixture(autouse=True)
def _fresh_probe_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(ci, "_docker_ok", False)
    monkeypatch.setattr(ci, "_docker_checked_at", 0.0)
    monkeypatch.setattr(ci, "_docker_error", "not probed yet")
    monkeypatch.delenv("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    yield


class _FlakyDaemon:
    """Unreachable for the first ``down_for`` probes, then reachable."""

    def __init__(self, down_for: int) -> None:
        self.down_for = down_for
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> tuple[bool, str]:
        with self._lock:
            self.calls += 1
            if self.calls <= self.down_for:
                return False, "ConnectionError: daemon not reachable"
            return True, ""


def test_import_does_not_probe_or_pin() -> None:
    # Nothing is decided at import: the first use probes.
    assert ci._docker_checked_at == 0.0
    assert ci._docker_ok is False


def test_failure_is_retried_and_success_is_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = _FlakyDaemon(down_for=1)
    monkeypatch.setattr(ci, "_probe_docker", daemon)
    assert ci._docker_available() is False
    # Within the retry window the failed probe is not repeated...
    assert ci._docker_available() is False
    assert daemon.calls == 1
    # ...after it, Docker is probed again and found.
    monkeypatch.setattr(ci, "_DOCKER_RETRY_SECONDS", 0.0)
    assert ci._docker_available() is True
    calls = daemon.calls
    for _ in range(20):
        assert ci._docker_available() is True
    assert daemon.calls == calls, "a reachable daemon is not re-probed on every call"


def test_concurrent_callers_after_recovery_all_see_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = _FlakyDaemon(down_for=3)
    monkeypatch.setattr(ci, "_probe_docker", daemon)
    monkeypatch.setattr(ci, "_DOCKER_RETRY_SECONDS", 0.0)
    for _ in range(3):
        assert ci._docker_available() is False
    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(ci._docker_available())) for _ in range(32)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 32
    assert daemon.calls == 4  # the lock serialises: one successful probe, then cached


async def test_execute_uses_the_sandbox_once_docker_is_back(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon = _FlakyDaemon(down_for=1)
    monkeypatch.setattr(ci, "_probe_docker", daemon)
    monkeypatch.setattr(ci, "_DOCKER_RETRY_SECONDS", 0.0)
    sandboxed: list[str] = []

    async def _fake_docker(self: Any, code: str, language: str, timeout: Any) -> ci.CodeResult:
        sandboxed.append(code)
        return ci.CodeResult(stdout="ok", stderr="", exit_code=0)

    monkeypatch.setattr(ci.CodeInterpreter, "_execute_docker", _fake_docker)
    interp = ci.CodeInterpreter()

    first = await interp.execute("print(1)")
    assert first.exit_code == 1
    # Honest: names the missing sandbox, not only the disabled fallback.
    assert "Docker sandbox unavailable" in first.stderr
    assert "daemon not reachable" in first.stderr

    results = await asyncio.gather(*[interp.execute(f"print({i})") for i in range(10)])
    assert all(r.success for r in results)
    assert len(sandboxed) == 10
