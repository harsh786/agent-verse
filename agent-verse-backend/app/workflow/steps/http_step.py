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
        url = str(self.ctx.resolve(self.step.url, state))
        method = self.step.method
        headers = self.ctx.resolve_dict(self.step.headers, state)
        body = (
            self.ctx.resolve_dict(self.step.request_body, state) if self.step.request_body else None
        )
        auth = self.ctx.resolve_dict(self.step.auth, state)

        # P8b-2: guardrails on the outgoing request (tenant rules + baseline,
        # tool_args layer) BEFORE anything is sent. Screened as rendered without
        # the vault, so the author's own secrets are not the policed traffic;
        # injection rules look only at the interpolated (untrusted) values.
        # Headers / auth are credentials by design and are not screened.
        # SSRF check first (a refused URL is never evaluated, never sent) —
        # raises SSRFBlockedError if unsafe; again if a redaction changed it.
        _ssrf_guard.validate(url)
        screened_url, body = await self._screen_request(state, method, url, body)
        if screened_url != url:
            url = screened_url
            _ssrf_guard.validate(url)

        # WF-14: the same key on every execution of this step in this run, so a
        # replay after a worker crash is recognisable by the receiver. A key the
        # workflow author set explicitly wins.
        if not any(str(h).lower() == "idempotency-key" for h in headers):
            from app.workflow.idempotency import step_idempotency_key

            headers["Idempotency-Key"] = step_idempotency_key(state, self.step.id)

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

        from app.net.ssrf_guard import SSRFError, public_async_client

        try:
            # Pinned client: the socket dials the address the central guard
            # checked at connect time (DNS rebinding), redirects are not followed.
            async with public_async_client(timeout=timeout_s) as client:
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
        except SSRFError as e:
            raise SSRFBlockedError(str(e)) from e
        except httpx.HTTPStatusError as e:
            # The error body is response content too: never PII / secrets in the
            # step error the run API serves (P8b-2).
            from app.guardrails_v2.output_screening import redact_baseline

            raise RuntimeError(
                f"HTTP {e.response.status_code} from {url}: "
                f"{redact_baseline(e.response.text[:256])}"
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(f"HTTP step timed out after {timeout_s}s: {url}") from e

        # P8b-2: the response body is screened (tool_output layer) before any
        # later step or the run's output sees it: block fails the step,
        # redact returns the redacted body.
        from app.guardrails_v2.models import GuardrailLayer
        from app.workflow.guardrails import screen_step_json

        output = await screen_step_json(
            output,
            layer=GuardrailLayer.TOOL_OUTPUT,
            state=state,
            step_id=self.step.id,
            step_type="http",
            direction="response",
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        _log.info("http_step_ok", step_id=self.step.id, url=url, duration_ms=duration_ms)

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }

    async def _screen_request(
        self, state: WorkflowState, method: str, url: str, body: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any] | None]:
        from app.guardrails_v2.models import GuardrailLayer
        from app.workflow.guardrails import interpolated_text, screen_step_json, unmask_vault

        unvaulted = ContextResolver()  # renders {{vault://X}} as "[vault:X]"
        shown_url = str(unvaulted.resolve(self.step.url, state))
        shown_body = (
            unvaulted.resolve_dict(self.step.request_body, state)
            if self.step.request_body
            else None
        )
        untrusted = "\n".join(
            filter(
                None,
                (
                    interpolated_text(self.step.url, shown_url),
                    interpolated_text(self.step.request_body or {}, shown_body or {}),
                ),
            )
        )
        request = {"method": method, "url": shown_url, "body": shown_body}
        screened = await screen_step_json(
            request,
            layer=GuardrailLayer.TOOL_ARGS,
            state=state,
            step_id=self.step.id,
            step_type="http",
            direction="request",
            untrusted=untrusted,
        )
        if screened == request:
            return url, body

        def _secret(name: str) -> Any:
            return self.ctx.resolve("{{vault://" + name + "}}", state)

        redacted = unmask_vault(screened, _secret)
        return str(redacted.get("url") or url), redacted.get("body")

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
