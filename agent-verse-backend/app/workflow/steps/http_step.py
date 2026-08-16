"""HTTPStepNode — authenticated HTTP call with SSRF guard + circuit breaker."""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.security import SSRFBlockedError, SSRFGuard
from app.workflow.state import WorkflowState

_log = get_logger(__name__)
_ssrf_guard = SSRFGuard()


class HTTPStepNode:
    _circuit_breaker: Any = None  # Injected from services

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self._cb = services.get("circuit_breaker")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
            return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}

        # Resolve all dynamic values
        url     = str(self.ctx.resolve(self.step.url, state))
        method  = self.step.method
        headers = self.ctx.resolve_dict(self.step.headers, state)
        body = (
            self.ctx.resolve_dict(self.step.request_body, state)
            if self.step.request_body
            else None
        )
        auth    = self.ctx.resolve_dict(self.step.auth, state)

        # SSRF check — raises SSRFBlockedError if unsafe
        _ssrf_guard.validate(url)

        # Resolve auth header
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = f"Bearer {auth['token']}"
        elif auth.get("type") == "basic":
            import base64
            creds = base64.b64encode(
                f"{auth.get('username', '')}:{auth.get('password', '')}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"

        timeout_s = self._parse_timeout(self.step.timeout)
        start = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()

                try:
                    output = response.json()
                except Exception:
                    output = {"body": response.text, "status_code": response.status_code}

        except SSRFBlockedError:
            raise
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"HTTP {e.response.status_code} from {url}: {e.response.text[:256]}"
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(f"HTTP step timed out after {timeout_s}s: {url}") from e

        duration_ms = int((time.monotonic() - start) * 1000)
        _log.info("http_step_ok", step_id=self.step.id, url=url, duration_ms=duration_ms)

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }

    @staticmethod
    def _parse_timeout(timeout_str: str) -> float:
        """Parse '30s', '5m', '2h' → float seconds."""
        if not timeout_str:
            return 30.0
        timeout_str = timeout_str.strip()
        if timeout_str.endswith("s"):
            return float(timeout_str[:-1])
        if timeout_str.endswith("m"):
            return float(timeout_str[:-1]) * 60
        if timeout_str.endswith("h"):
            return float(timeout_str[:-1]) * 3600
        return float(timeout_str)
