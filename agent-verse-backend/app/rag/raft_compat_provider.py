"""RAFT fine-tuning on any OpenAI-compatible fine-tuning API (KB-32).

RAFT could only fine-tune on OpenAI itself (:mod:`app.rag.raft_openai_provider`,
built on the OpenAI SDK). Many vendors and gateways expose the same fine-tuning
REST surface — ``POST /files`` (multipart, ``purpose=fine-tune``),
``POST /fine_tuning/jobs`` and ``GET /fine_tuning/jobs/{id}`` — and serve the
resulting model through an OpenAI-compatible chat endpoint. This adapter speaks
that wire format directly over HTTP, so a deployment can point RAFT at such a
vendor by configuration alone:

* ``RAFT_COMPAT_FINE_TUNE_BASE_URL`` — the vendor's API root (``.../v1``),
* ``RAFT_COMPAT_FINE_TUNE_API_KEY`` — its key (Bearer auth),
* ``RAFT_COMPAT_FINE_TUNE_PROVIDER_ID`` — the id RAFT jobs reference
  (default ``openai_compatible``),
* ``RAFT_COMPAT_FINE_TUNE_USD_PER_EXAMPLE`` — the per-training-example price the
  operator sees in the cost preview before confirming a (billable) job,
* ``RAFT_COMPAT_FINE_TUNE_ALLOW_INTERNAL`` — trust a private-network endpoint
  (otherwise the URL must pass the SSRF guard).

Trained models are served by :mod:`app.rag.raft_inference` through the
``openai_compatible`` registry provider on the same base URL and key, so a
model trained on the vendor answers on the vendor.

The adapter is exercised only with mocked HTTP in this repository; it has never
been run against a real vendor fine-tune.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.observability.logging import get_logger
from app.rag.raft import (
    FineTuneCost,
    FineTuneJobState,
    FineTuneProvider,
    RAFTJobStatus,
    raft_chat_jsonl,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

DEFAULT_PROVIDER_ID = "openai_compatible"
_MIN_ESTIMATE = Decimal("0.010")

# OpenAI-compatible job statuses (OpenAI's names plus common vendor spellings).
_STATUS_MAP: Mapping[str, RAFTJobStatus] = {
    "validating_files": "submitted",
    "queued": "submitted",
    "pending": "submitted",
    "created": "submitted",
    "running": "running",
    "succeeded": "completed",
    "success": "completed",
    "completed": "completed",
    "failed": "failed",
    "error": "failed",
    "cancelled": "failed",
    "canceled": "failed",
}


class CompatFineTuneError(RuntimeError):
    """The vendor's fine-tuning API refused or returned something unusable."""


def map_compat_status(status: str) -> RAFTJobStatus:
    """Unknown statuses read as ``running`` — never silently terminal."""
    return _STATUS_MAP.get(status.strip().lower(), "running")


class OpenAICompatibleFineTuneProvider:
    """``FineTuneProvider`` over the OpenAI fine-tuning wire format (plain HTTP)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        provider_id: str = DEFAULT_PROVIDER_ID,
        usd_per_training_example: Decimal = Decimal("0.008"),
        usd_per_validation_example: Decimal = Decimal("0.002"),
        allow_internal: bool = False,
        timeout_seconds: float = 60.0,
        client: Any = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("an OpenAI-compatible fine-tuning base_url is required")
        if not api_key:
            raise ValueError("an OpenAI-compatible fine-tuning api_key is required")
        if not provider_id.strip():
            raise ValueError("provider_id cannot be empty")
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key
        self._provider_id = provider_id.strip()
        self._usd_train = usd_per_training_example
        self._usd_validation = usd_per_validation_example
        self._allow_internal = allow_internal
        self._timeout = timeout_seconds
        self._client = client  # injectable httpx.AsyncClient (tests)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def base_url(self) -> str:
        return self._base_url

    async def preview_cost(
        self,
        *,
        training_examples: int,
        validation_examples: int,
        base_model: str,
    ) -> FineTuneCost:
        """Deterministic, network-free preview at the configured per-example price."""
        del base_model
        amount = self._usd_train * Decimal(max(training_examples, 0)) + (
            self._usd_validation * Decimal(max(validation_examples, 0))
        )
        return FineTuneCost(currency="USD", estimated_amount=max(amount, _MIN_ESTIMATE))

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        """Upload the chat-format splits and create the job; return the vendor job id."""
        training_chat = raft_chat_jsonl(training_jsonl)
        validation_chat = raft_chat_jsonl(validation_jsonl)
        if not training_chat.strip():
            raise ValueError("RAFT training split is empty")
        async with self._session() as client:
            training_file = await self._upload(client, "raft_train.jsonl", training_chat)
            body: dict[str, Any] = {
                "model": base_model,
                "training_file": training_file,
                "metadata": {"raft_idempotency_key": idempotency_key},
            }
            if validation_chat.strip():
                body["validation_file"] = await self._upload(
                    client, "raft_val.jsonl", validation_chat
                )
            data = await self._request(
                client,
                "POST",
                "/fine_tuning/jobs",
                json=body,
                # Vendors that honour it dedupe a retried create.
                headers={"Idempotency-Key": idempotency_key},
            )
        job_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise CompatFineTuneError("fine-tuning job response has no id")
        logger.info(
            "raft_compat_job_submitted",
            provider_id=self._provider_id,
            provider_job_id=job_id,
            base_model=base_model,
        )
        return job_id

    async def status(self, provider_job_id: str) -> FineTuneJobState:
        if not provider_job_id.strip():
            raise ValueError("provider_job_id is required")
        async with self._session() as client:
            data = await self._request(client, "GET", f"/fine_tuning/jobs/{provider_job_id}")
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise CompatFineTuneError("fine-tuning job response has no status")
        status = map_compat_status(data["status"])
        model = data.get("fine_tuned_model")
        fine_tuned_model = model if isinstance(model, str) and model else None
        if status == "completed" and fine_tuned_model is None:
            # A "succeeded" job with no model id cannot be served: not complete.
            status = "running"
        return FineTuneJobState(
            status=status, fine_tuned_model=fine_tuned_model, error=_error_text(data)
        )

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _session(self) -> Any:
        if self._client is not None:
            return _Borrowed(self._client)
        from app.net.ssrf_guard import public_async_client

        allowlist = [(urlparse(self._base_url).hostname or "").lower()] if (
            self._allow_internal
        ) else None
        return public_async_client(allowed_domains=allowlist, timeout=self._timeout)

    async def _upload(self, client: Any, filename: str, content: str) -> str:
        data = await self._request(
            client,
            "POST",
            "/files",
            data={"purpose": "fine-tune"},
            files={"file": (filename, content.encode("utf-8"), "application/jsonl")},
        )
        file_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise CompatFineTuneError("file upload response has no id")
        return file_id

    async def _request(
        self, client: Any, method: str, path: str, *, headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if not self._allow_internal and self._client is None:
            from app.net.ssrf_guard import SSRFError, assert_public_url_async

            try:
                await assert_public_url_async(url, context="raft_fine_tune")
            except (SSRFError, ValueError) as exc:
                raise CompatFineTuneError(f"fine-tuning endpoint blocked: {exc}") from exc
        all_headers = {"Authorization": f"Bearer {self._api_key}", **(headers or {})}
        response = await client.request(method, url, headers=all_headers, **kwargs)
        if response.status_code >= 400:
            raise CompatFineTuneError(
                f"{method} {path} failed with HTTP {response.status_code}: "
                f"{_error_text(_json_or_none(response)) or response.text[:200]}"
            )
        body = _json_or_none(response)
        if body is None:
            raise CompatFineTuneError(f"{method} {path} returned a non-JSON body")
        return body


class _Borrowed:
    """Async context manager over an injected client that it does not close."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def __aenter__(self) -> Any:
        return self._client

    async def __aexit__(self, *exc: object) -> None:
        return None


def _json_or_none(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _error_text(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else None
    if isinstance(error, str) and error:
        return error
    return None


def _decimal_setting(value: object, default: Decimal) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return parsed if parsed.is_finite() and parsed >= 0 else default


def build_compat_fine_tune_provider(settings: Settings) -> FineTuneProvider | None:
    """The configured OpenAI-compatible fine-tune provider, or None when unset."""
    base_url = str(getattr(settings, "raft_compat_fine_tune_base_url", "") or "").strip()
    api_key = str(getattr(settings, "raft_compat_fine_tune_api_key", "") or "")
    if not base_url or not api_key:
        return None
    return OpenAICompatibleFineTuneProvider(
        base_url=base_url,
        api_key=api_key,
        provider_id=str(
            getattr(settings, "raft_compat_fine_tune_provider_id", "") or DEFAULT_PROVIDER_ID
        ),
        usd_per_training_example=_decimal_setting(
            getattr(settings, "raft_compat_fine_tune_usd_per_example", "0.008"), Decimal("0.008")
        ),
        allow_internal=bool(getattr(settings, "raft_compat_fine_tune_allow_internal", False)),
    )


__all__ = [
    "DEFAULT_PROVIDER_ID",
    "CompatFineTuneError",
    "OpenAICompatibleFineTuneProvider",
    "build_compat_fine_tune_provider",
    "map_compat_status",
]
