"""Feed real LLM call outcomes into the model registry's provider health (PROV-16).

``AIRouter.record_call`` had no callers, so provider health only changed when an
operator ran the admin test ping. Every decision call
(:func:`app.providers.guarded_completion.complete_decision`) and every executor
step now reports its outcome here.

A tenant's own key (BYOK) is never recorded: its revoked or over-quota key must
not mark the shared platform provider unhealthy for everyone. A call whose
provider cannot be identified is not recorded either (better unverified than
attributed to the wrong provider).
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def provider_label(provider: Any, model: str = "") -> str | None:
    """The platform provider name a call ran on, or ``None`` when unknown / BYOK."""
    if provider is not None:
        if getattr(provider, "_byok_tenant_id", None):
            return None
        for attr in ("_agentverse_provider_type", "_provider_name"):
            value = getattr(type(provider), attr, None) or getattr(provider, attr, None)
            if isinstance(value, str) and value:
                return value
    if model:
        from app.ai_router.registry import model_registry

        for endpoint in model_registry.list_configured():
            if endpoint.model_id == model:
                return endpoint.provider
    return None


def record_llm_outcome(
    *, provider: Any = None, model: str = "", ok: bool, latency_ms: float, error: str = ""
) -> None:
    """Record one call outcome against its provider's health. Never raises."""
    try:
        label = provider_label(provider, model)
        if not label:
            return
        from app.ai_router.router import ai_router

        ai_router.record_call(label, latency_ms, ok, error[:300])
    except Exception as exc:
        _log.debug("provider_health_record_failed: %s", str(exc)[:160])
