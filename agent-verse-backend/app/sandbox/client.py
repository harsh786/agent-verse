"""Client for the remote code-sandbox runner (:mod:`app.sandbox.runner`).

:class:`app.tools.code_interpreter.CodeInterpreter` uses it whenever
``CODE_SANDBOX_URL`` is configured — that is the production path in the shipped
compose stack and Helm charts, where the workers have no Docker daemon. A
configured runner is authoritative: if it is unreachable or rejects the call,
the execution fails with that reason; it never falls through to Docker or to
host subprocesses.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.observability.logging import get_logger
from app.tools.code_interpreter import CodeResult

__all__ = ["RemoteSandboxClient", "RemoteSandboxConfig", "remote_sandbox_config"]

_log = get_logger(__name__)

# = app.sandbox.runner.EXECUTE_PATH (not imported: the runner stays standalone).
EXECUTE_PATH = "/v1/execute"
# Response wait beyond the program's own timeout: queueing for a slot (the
# runner's CODE_SANDBOX_QUEUE_TIMEOUT_SECONDS, 10 s) + process start/cleanup.
_RESPONSE_MARGIN_S = 20.0
_CONNECT_TIMEOUT_S = 5.0
# Above the runner's two 1 MB output caps plus JSON escaping.
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class RemoteSandboxConfig:
    url: str
    token: str

    def __repr__(self) -> str:  # never log the token
        return f"RemoteSandboxConfig(url={self.url!r}, token={'set' if self.token else 'unset'})"


def remote_sandbox_config() -> RemoteSandboxConfig | None:
    """The configured runner (``CODE_SANDBOX_URL`` / ``CODE_SANDBOX_TOKEN``), or None."""
    from app.core.config import get_settings

    settings = get_settings()
    url = (settings.code_sandbox_url or "").strip().rstrip("/")
    if not url:
        return None
    return RemoteSandboxConfig(url=url, token=(settings.code_sandbox_token or "").strip())


def _failure(message: str, t0: float) -> CodeResult:
    return CodeResult(
        stdout="",
        stderr=f"code sandbox error: {message}",
        exit_code=1,
        timed_out=False,
        execution_time_ms=(time.monotonic() - t0) * 1000,
    )


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])[:300]
    return str(body)[:300]


class RemoteSandboxClient:
    """Run one program on the remote runner and map the answer to a CodeResult.

    Infrastructure failures (unreachable, bad token, runner error) come back as a
    failed CodeResult whose stderr says what is wrong, like the Docker path's
    "sandbox error"; a full runner raises
    :class:`app.tools.code_execution.CodeExecutionBusyError` (the API answers 429).
    """

    def __init__(
        self, config: RemoteSandboxConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport

    async def execute(self, code: str, language: str, timeout: float) -> CodeResult:
        t0 = time.monotonic()
        cfg = self._config
        if not cfg.token:
            return _failure(
                f"CODE_SANDBOX_URL is set ({cfg.url}) but CODE_SANDBOX_TOKEN is empty; set the "
                "runner's shared secret on this service (the same value the runner has)",
                t0,
            )
        payload: dict[str, Any] = {
            "language": language,
            "code": code,
            "timeout_seconds": float(timeout),
        }
        http_timeout = httpx.Timeout(
            float(timeout) + _RESPONSE_MARGIN_S, connect=_CONNECT_TIMEOUT_S
        )
        try:
            async with httpx.AsyncClient(
                timeout=http_timeout,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,  # never route tenant code through an env-configured proxy
            ) as client:
                response = await client.post(
                    cfg.url + EXECUTE_PATH,
                    json=payload,
                    headers={"Authorization": f"Bearer {cfg.token}"},
                )
        except httpx.TimeoutException as exc:
            if isinstance(exc, httpx.ConnectTimeout):
                return _failure(f"runner at {cfg.url} did not accept the connection", t0)
            # The program may have run: report it as timed out (it is audited as such).
            return CodeResult(
                stdout="",
                stderr=f"code sandbox error: no answer from {cfg.url} within "
                f"{float(timeout) + _RESPONSE_MARGIN_S:g}s",
                exit_code=124,
                timed_out=True,
                execution_time_ms=(time.monotonic() - t0) * 1000,
            )
        except httpx.HTTPError as exc:
            _log.warning("code_sandbox_unreachable", url=cfg.url, error=str(exc)[:200])
            return _failure(f"runner at {cfg.url} is unreachable ({type(exc).__name__}: {exc})", t0)

        if response.status_code == 429:
            from app.tools.code_execution import CodeExecutionBusyError

            limit = 0
            try:
                limit = int((response.json() or {}).get("limit") or 0)
            except (ValueError, AttributeError, TypeError):
                limit = 0
            raise CodeExecutionBusyError("sandbox", limit)
        if response.status_code == 401:
            return _failure(
                f"runner at {cfg.url} rejected the credentials: CODE_SANDBOX_TOKEN does not "
                "match the runner's",
                t0,
            )
        if response.status_code != 200:
            return _failure(
                f"runner at {cfg.url} answered HTTP {response.status_code}: "
                f"{_error_text(response)}",
                t0,
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            return _failure("runner answer exceeds the response size limit", t0)
        try:
            body = response.json()
            return CodeResult(
                stdout=str(body.get("stdout") or ""),
                stderr=str(body.get("stderr") or ""),
                exit_code=int(body.get("exit_code", 1)),
                timed_out=bool(body.get("timed_out", False)),
                execution_time_ms=float(body.get("duration_ms") or 0.0)
                or (time.monotonic() - t0) * 1000,
            )
        except (ValueError, AttributeError, TypeError) as exc:
            return _failure(f"runner answer is malformed ({exc})", t0)
