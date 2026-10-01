"""The LLM provider an HTTP request should use: the tenant's own (BYOK) first.

Endpoints that make LLM calls for a tenant (skills, insights, multimodal, OCR,
debate / supervisor submission) used ``app.state._app_provider`` directly, so a
tenant with its own key had those calls sent to — and paid by — the platform
provider, bypassing its vendor and data-handling choice. Resolution here matches
the goal path: the pinned override, else the tenant's BYOK config read strictly
from the durable store, else the app-wide provider. A BYOK config that cannot be
built (422) or read (503) refuses the request — never a silent fallback to
platform spend.

The call itself still goes through
:func:`app.providers.guarded_completion.complete_decision` (budget preflight,
charge, circuit breaker, timeout).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status


async def tenant_llm_provider(
    request: Request, tenant: Any, *, fallback_attr: str = "_app_provider"
) -> Any:
    """The tenant's provider (BYOK), else ``app.state.<fallback_attr>`` (may be None)."""
    from app.providers.tenant_provider import (
        TenantProviderError,
        resolve_tenant_byok_provider,
    )
    from app.services.llm_config_store import LLMConfigReadError

    state = request.app.state
    try:
        provider = await resolve_tenant_byok_provider(state, tenant.tenant_id)
    except LLMConfigReadError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Your LLM provider configuration could not be read; try again shortly.",
        ) from exc
    except TenantProviderError as exc:
        raise HTTPException(
            422,  # (the starlette constant name is deprecated)
            f"Your LLM provider configuration is unusable: {exc}",
        ) from exc
    if provider is not None:
        return provider
    return getattr(state, fallback_attr, None)
