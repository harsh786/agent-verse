"""OpenAI fine-tuning adapter for RAFT (D-8).

RAFT (Retrieval-Augmented Fine-Tuning) needs a concrete
:class:`~app.rag.raft.FineTuneProvider` to be *usable*. Historically both live
gateways constructed ``RAFTService(..., providers={})``, so every RAFT operation
raised ``RAFTModelUnavailableError`` even when a real OpenAI key was configured.

This module provides:

* :class:`OpenAIFineTuneProvider` — a real ``FineTuneProvider`` backed by the
  OpenAI fine-tuning REST API (files → job create → job retrieve).
* :func:`build_raft_providers` — returns ``{"openai": OpenAIFineTuneProvider}``
  when an OpenAI key is configured, else ``{}`` (RAFT is then legitimately
  unavailable — a *configuration* state, not a structural bug).

Cost estimation and status mapping are deterministic and network-free so they
are safe to unit-test; only ``submit``/``status``/``evaluate`` touch the network
and only when actually driving a fine-tune.
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.rag.raft import (
    FineTuneCost,
    FineTuneEvaluation,
    FineTuneJobState,
    FineTuneProvider,
    RAFTJobStatus,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

_PROVIDER_ID = "openai"

# Rough per-example cost anchor (USD). OpenAI fine-tuning bills per 1M training
# tokens; without tokenising every example we anchor on a conservative average
# example size. This is a *preview* estimate, surfaced to the operator before
# they confirm the (real, billable) job — intentionally on the high side.
_USD_PER_TRAINING_EXAMPLE = Decimal("0.008")
_USD_PER_VALIDATION_EXAMPLE = Decimal("0.002")
_MIN_ESTIMATE = Decimal("0.010")

# OpenAI fine-tuning job status -> canonical RAFTJobStatus.
_STATUS_MAP: dict[str, RAFTJobStatus] = {
    "validating_files": "submitted",
    "queued": "submitted",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "failed",
}


def map_openai_status(openai_status: str) -> RAFTJobStatus:
    """Map an OpenAI fine-tuning job status to a canonical ``RAFTJobStatus``.

    An unrecognised status maps to ``"running"`` — a safe, non-terminal state so
    a future OpenAI status never silently reads as ``completed`` or ``failed``.
    """
    return _STATUS_MAP.get(openai_status, "running")


class OpenAIFineTuneProvider:
    """Real ``FineTuneProvider`` backed by OpenAI's fine-tuning API."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        if not api_key:
            raise ValueError("OpenAIFineTuneProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url

    @property
    def provider_id(self) -> str:
        return _PROVIDER_ID

    def _client(self) -> Any:
        # Imported lazily so importing this module never requires the SDK.
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return AsyncOpenAI(**kwargs)

    async def preview_cost(
        self,
        *,
        training_examples: int,
        validation_examples: int,
        base_model: str,
    ) -> FineTuneCost:
        """Deterministic, network-free cost preview for operator confirmation."""
        amount = (
            _USD_PER_TRAINING_EXAMPLE * Decimal(max(training_examples, 0))
            + _USD_PER_VALIDATION_EXAMPLE * Decimal(max(validation_examples, 0))
        )
        if amount < _MIN_ESTIMATE:
            amount = _MIN_ESTIMATE
        return FineTuneCost(currency="USD", estimated_amount=amount)

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        """Upload datasets and create a fine-tuning job; return the job id."""
        client = self._client()
        training_file = await client.files.create(
            file=("raft_train.jsonl", io.BytesIO(training_jsonl.encode("utf-8"))),
            purpose="fine-tune",
        )
        create_kwargs: dict[str, Any] = {
            "model": base_model,
            "training_file": training_file.id,
            "metadata": {"raft_idempotency_key": idempotency_key},
        }
        if validation_jsonl.strip():
            validation_file = await client.files.create(
                file=("raft_val.jsonl", io.BytesIO(validation_jsonl.encode("utf-8"))),
                purpose="fine-tune",
            )
            create_kwargs["validation_file"] = validation_file.id
        job = await client.fine_tuning.jobs.create(**create_kwargs)
        logger.info(
            "raft_openai_job_submitted",
            provider_job_id=job.id,
            base_model=base_model,
        )
        return str(job.id)

    async def status(self, provider_job_id: str) -> FineTuneJobState:
        client = self._client()
        job = await client.fine_tuning.jobs.retrieve(provider_job_id)
        return FineTuneJobState(
            status=map_openai_status(str(job.status)),
            fine_tuned_model=getattr(job, "fine_tuned_model", None),
            error=self._extract_error(job),
        )

    async def evaluate(
        self,
        *,
        model: str,
        test_jsonl: str,
    ) -> FineTuneEvaluation:
        """Return evaluation metrics for a fine-tuned model.

        OpenAI exposes training/validation metrics via the job's result files
        rather than an ad-hoc scoring endpoint, so we surface the example count
        as a minimal, honest signal here. Richer eval (running ``test_jsonl``
        through the fine-tuned model and scoring) is a deliberate follow-up; we
        never fabricate accuracy numbers.
        """
        example_count = sum(1 for line in test_jsonl.splitlines() if line.strip())
        return FineTuneEvaluation(metrics={"test_examples": float(example_count)})

    @staticmethod
    def _extract_error(job: Any) -> str | None:
        error = getattr(job, "error", None)
        if error is None:
            return None
        message = getattr(error, "message", None)
        if message:
            return str(message)
        return str(error) if error else None


def build_raft_providers(settings: Settings) -> dict[str, FineTuneProvider]:
    """Build the RAFT fine-tune provider registry from configured credentials.

    Returns ``{"openai": OpenAIFineTuneProvider}`` when an OpenAI API key is
    configured, otherwise ``{}`` — RAFT is then unavailable because no
    fine-tune-capable provider is configured, which is the correct behaviour
    (as opposed to the previous bug where it was hardcoded empty).
    """
    providers: dict[str, FineTuneProvider] = {}
    api_key = getattr(settings, "openai_api_key", "") or ""
    if api_key:
        base_url = getattr(settings, "openai_base_url", None) or None
        providers[_PROVIDER_ID] = OpenAIFineTuneProvider(
            api_key=api_key, base_url=base_url
        )
    return providers
