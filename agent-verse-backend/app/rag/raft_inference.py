"""Serving fine-tuned RAFT models through the app's LLM provider registry.

A RAFT job produces a vendor model id (e.g. ``ft:gpt-4o-mini:org::abc``). It can
only answer queries through an LLM provider bound to the *same* vendor account
that trained it, so each fine-tune provider id maps to the registry provider type
that can serve its models. A fine-tune provider without such a mapping gets no
inference provider, and :class:`~app.rag.raft.RAFTService` then refuses to create
jobs for it (a paid model that could never answer).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.providers.base import CompletionRequest, Message
from app.rag.raft import (
    RAFT_INFERENCE_CAPABILITY,
    RAFT_SYSTEM_PROMPT,
    FineTunedInferenceProvider,
    FineTuneProvider,
    raft_user_prompt,
)

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

# fine-tune provider id -> provider-registry type that serves its fine-tuned models
_SERVING_PROVIDER_TYPES: Mapping[str, str] = {"openai": "openai"}


class LLMFineTunedInferenceProvider:
    """Answer RAFT queries with a fine-tuned model id on a registry LLM provider.

    The prompt is the same system + user shape the training export uses
    (:func:`app.rag.raft.raft_chat_jsonl`), and ``model`` is always the job's
    fine-tuned model id — never the provider's default model.
    """

    def __init__(self, *, provider_id: str, llm: Any, max_tokens: int = 1024) -> None:
        complete = getattr(llm, "complete", None)
        if not inspect.iscoroutinefunction(complete):
            raise TypeError("RAFT inference requires an LLM provider with async complete()")
        self._provider_id = provider_id
        self._llm = llm
        self._max_tokens = max_tokens

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capability(self) -> str:
        return RAFT_INFERENCE_CAPABILITY

    async def infer(
        self,
        *,
        query: str,
        evidence: tuple[str, ...],
        fine_tuned_model_id: str,
        tenant_id: str | None = None,
    ) -> str:
        if not fine_tuned_model_id.strip():
            raise ValueError("fine_tuned_model_id is required")
        from app.providers.guarded_completion import (
            complete_decision,
            generation_timeout_seconds,
        )

        # Charged to the tenant (budget + ledger) and circuit-broken with a
        # bounded timeout; a budget refusal raises to the caller.
        response = await complete_decision(
            self._llm,
            CompletionRequest(
                messages=[
                    Message(role="system", content=RAFT_SYSTEM_PROMPT),
                    Message(role="user", content=raft_user_prompt(query, evidence)),
                ],
                model=fine_tuned_model_id,
                max_tokens=self._max_tokens,
                temperature=0.0,
            ),
            role="rag_raft_inference",
            tenant_id=tenant_id,
            timeout_seconds=generation_timeout_seconds(),
        )
        return str(getattr(response, "content", "") or "").strip()


def build_raft_inference_providers(
    settings: Settings,
    fine_tune_providers: Mapping[str, FineTuneProvider],
) -> dict[str, FineTunedInferenceProvider]:
    """One serving provider per fine-tune provider the registry can serve.

    Uses :func:`app.providers.registry.instantiate_configured_provider` with the
    same credentials the fine-tune adapter trains with, so the model id resolves
    on the account that owns it.
    """
    from app.providers.registry import instantiate_configured_provider
    from app.rag.raft_compat_provider import OpenAICompatibleFineTuneProvider

    providers: dict[str, FineTunedInferenceProvider] = {}
    for provider_id, fine_tune_provider in fine_tune_providers.items():
        model = ""
        if isinstance(fine_tune_provider, OpenAICompatibleFineTuneProvider):
            # A model trained on an OpenAI-compatible vendor is served by that
            # vendor's chat endpoint, with the key it was trained with.
            provider_type: str | None = "openai_compatible"
            credentials: tuple[str, str] | None = (
                str(getattr(settings, "raft_compat_fine_tune_api_key", "") or ""),
                fine_tune_provider.base_url,
            )
            # The registry demands a model for generic endpoints; RAFT always
            # sends the job's fine-tuned model id per request, never this one.
            model = "raft-fine-tuned-model-per-request"
        else:
            provider_type = _SERVING_PROVIDER_TYPES.get(provider_id)
            credentials = _credentials(settings, provider_id)
        if provider_type is None:
            logger.warning("raft_no_serving_provider_type", provider_id=provider_id)
            continue
        if credentials is None or not credentials[0]:
            continue
        api_key, base_url = credentials
        try:
            llm = instantiate_configured_provider(
                provider_type,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        except Exception as exc:
            logger.warning(
                "raft_serving_provider_unavailable",
                provider_id=provider_id,
                error_type=type(exc).__name__,
            )
            continue
        if llm is None:
            continue
        providers[provider_id] = LLMFineTunedInferenceProvider(provider_id=provider_id, llm=llm)
    return providers


def _credentials(settings: Settings, provider_id: str) -> tuple[str, str] | None:
    if provider_id == "openai":
        api_key = str(getattr(settings, "openai_api_key", "") or "")
        base_url = str(getattr(settings, "openai_base_url", "") or "")
        return (api_key, base_url) if api_key else None
    return None


__all__ = ["LLMFineTunedInferenceProvider", "build_raft_inference_providers"]
