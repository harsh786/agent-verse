"""Run-completion callbacks — POST a signed terminal-status payload to a URL.

Before this module ``WorkflowRunner.send_callback`` existed but was never called,
and a trigger's ``callback_url`` was only stashed in (unpersisted) run metadata,
so no caller was ever notified.

Delivery contract:
  * **SSRF-guarded** — ``assert_public_url`` at trigger time (reject early) and
    again right before every delivery attempt (DNS may have changed); redirects
    are never followed, so a public URL cannot 30x into the private network.
  * **Signed** — ``X-AgentVerse-Signature: sha256=<hex>`` is an HMAC-SHA256 over
    ``"<timestamp>.<body>"`` with a per-(tenant, workflow) key derived from
    ``WORKFLOW_WEBHOOK_SECRET`` (see ``webhook_tokens.callback_signing_secret``);
    ``X-AgentVerse-Timestamp`` lets receivers reject replays.
  * **Retried, bounded** — transient failures (network, 408/429/5xx) are retried
    with exponential backoff up to ``MAX_ATTEMPTS``; other 4xx and SSRF blocks
    are permanent. Via Celery when a broker is wired (survives a process crash),
    else as a tracked in-process asyncio task.
  * **Never blocks the run** — the run's terminal status is persisted first and
    dispatch errors are logged, never raised into the run.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.observability.logging import get_logger

_log = get_logger(__name__)

MAX_ATTEMPTS = 5
_TIMEOUT_S = 10.0
_BASE_BACKOFF_S = 5.0
_MAX_BACKOFF_S = 600.0

# Strong references to in-process delivery tasks (asyncio only keeps weak refs,
# so an untracked task can be garbage-collected mid-flight).
_INFLIGHT: set[asyncio.Task[Any]] = set()


class CallbackPermanentError(Exception):
    """Delivery must not be retried (SSRF-blocked URL, non-retryable 4xx)."""


class CallbackTransientError(Exception):
    """Delivery failed in a way worth retrying (network, 408/429/5xx)."""


def validate_callback_url(url: str) -> None:
    """Raise ``ValueError`` (SSRFError is one) unless ``url`` is a public http(s) URL."""
    from app.net.ssrf_guard import assert_public_url

    assert_public_url(url, context="workflow run callback")


def backoff_seconds(attempt: int) -> float:
    """Exponential backoff before retry number ``attempt`` (1-based)."""
    return float(min(_BASE_BACKOFF_S * (2 ** max(0, attempt - 1)), _MAX_BACKOFF_S))


def sign_payload(body: bytes, timestamp: str, tenant_id: str, workflow_id: str) -> str:
    from app.workflow.webhook_tokens import callback_signing_secret

    key = callback_signing_secret(tenant_id, workflow_id).encode()
    digest = hmac.new(key, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver_callback(
    url: str, payload: dict[str, Any], *, tenant_id: str, workflow_id: str
) -> int:
    """Attempt ONE delivery. Returns the HTTP status on 2xx.

    Raises :class:`CallbackPermanentError` or :class:`CallbackTransientError`.
    """
    from app.net.ssrf_guard import SSRFError

    try:
        validate_callback_url(url)
    except (SSRFError, ValueError) as exc:
        raise CallbackPermanentError(f"callback URL blocked: {exc}") from exc

    body = json.dumps(payload, default=str, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    try:
        signature = sign_payload(body, timestamp, tenant_id, workflow_id)
    except Exception as exc:  # signing secret not configured in production
        raise CallbackPermanentError(f"callback signing unavailable: {exc}") from exc
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AgentVerse-Workflow-Callback/1",
        "X-AgentVerse-Event": str(payload.get("event", "")),
        "X-AgentVerse-Timestamp": timestamp,
        "X-AgentVerse-Signature": signature,
        # Stable per (run, status) so receivers can dedupe our retries.
        "Idempotency-Key": f"{payload.get('run_id')}:{payload.get('status')}",
    }
    from app.net.ssrf_guard import public_async_client

    try:
        # IP-pinned: the host is re-resolved and re-checked at connect time and
        # the socket dials the checked address (a plain client re-resolved the
        # name after validation, so a rebinding DNS answer reached loopback).
        async with public_async_client(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, content=body, headers=headers)
    except SSRFError as exc:  # the name now resolves somewhere internal
        raise CallbackPermanentError(f"callback URL blocked at connect: {exc}") from exc
    except httpx.HTTPError as exc:
        raise CallbackTransientError(f"callback transport error: {exc}") from exc
    if 200 <= resp.status_code < 300:
        return resp.status_code
    if resp.status_code in (408, 429) or resp.status_code >= 500:
        raise CallbackTransientError(f"callback got HTTP {resp.status_code}")
    raise CallbackPermanentError(f"callback got HTTP {resp.status_code}")


async def deliver_with_retries(
    url: str,
    payload: dict[str, Any],
    *,
    tenant_id: str,
    workflow_id: str,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Any = asyncio.sleep,
) -> bool:
    """In-process delivery loop (no broker). Returns True once delivered."""
    for attempt in range(1, max_attempts + 1):
        try:
            await deliver_callback(url, payload, tenant_id=tenant_id, workflow_id=workflow_id)
            _log.info("workflow_callback_delivered", run_id=payload.get("run_id"), attempt=attempt)
            return True
        except CallbackPermanentError as exc:
            _log.warning(
                "workflow_callback_rejected", run_id=payload.get("run_id"), error=str(exc)
            )
            return False
        except CallbackTransientError as exc:
            _log.warning(
                "workflow_callback_retry",
                run_id=payload.get("run_id"),
                attempt=attempt,
                error=str(exc),
            )
            if attempt < max_attempts:
                await sleep(backoff_seconds(attempt))
    _log.error("workflow_callback_exhausted", run_id=payload.get("run_id"))
    return False


def build_payload(
    *, run_id: str, workflow_id: str, status: str, state: dict[str, Any] | None
) -> dict[str, Any]:
    st = state or {}
    return {
        "event": f"workflow.run.{status}",
        "run_id": run_id,
        "workflow_id": workflow_id,
        "status": status,
        "outputs": st.get("outputs") or {},
        "error": st.get("error"),
        "labels": st.get("labels") or {},
        "cost_usd": st.get("cost_usd") or 0.0,
        "tokens_used": st.get("tokens_used") or 0,
    }


def dispatch_callback(
    url: str,
    payload: dict[str, Any],
    *,
    tenant_id: str,
    workflow_id: str,
    celery_app: Any | None,
    queue: str | None = None,
) -> None:
    """Hand the delivery off without awaiting it. Never raises."""
    try:
        if celery_app is not None:
            from app.workflow.celery_tasks import deliver_workflow_callback

            opts: dict[str, Any] = {"queue": queue} if queue else {}
            deliver_workflow_callback.apply_async(
                args=[url, payload, tenant_id, workflow_id], **opts
            )
            return
        task = asyncio.get_running_loop().create_task(
            deliver_with_retries(url, payload, tenant_id=tenant_id, workflow_id=workflow_id)
        )
        _INFLIGHT.add(task)
        task.add_done_callback(_INFLIGHT.discard)
    except Exception as exc:
        _log.error(
            "workflow_callback_dispatch_failed", run_id=payload.get("run_id"), error=str(exc)
        )
