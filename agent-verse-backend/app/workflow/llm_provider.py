"""Per-run LLM provider for workflow steps (BYOK-3).

Steps receive services from the compiler at compile time; a provider captured
there is process-wide, so a tenant's own key (Settings → LLM Providers) was
never used and, without a platform key, steps got the canned FakeProvider. With
an ``llm_provider_resolver`` service (``TenantLLMProviderResolver``, wired by the
API lifespan and the workflow worker) the provider is resolved for the run's
tenant on every execution: tenant BYOK → platform → "no LLM provider configured
for tenant" — the same order and resolver as goals.
"""

from __future__ import annotations

from typing import Any

from app.workflow.steps import StepServiceUnavailableError


async def step_llm_provider(
    *,
    resolver: Any,
    fallback: Any,
    state: Any,
    step_id: str,
    required: bool,
) -> Any | None:
    """The provider for this step execution.

    ``required`` steps (llm / rag) fail with :class:`StepServiceUnavailableError`
    when nothing is configured; optional users (OCR / RPA vision) get ``None`` and
    run without vision. A tenant whose BYOK config is unusable always fails —
    never a silent platform fallback.
    """
    if resolver is None:
        return fallback  # injected provider (tests / custom wiring)
    from app.providers.llm_resolution import NoLLMProviderConfiguredError
    from app.providers.tenant_provider import TenantProviderError

    tenant_id = str((state or {}).get("tenant_id") or "")
    try:
        return await resolver(tenant_id)
    except NoLLMProviderConfiguredError as exc:
        if not required:
            return None
        raise StepServiceUnavailableError(f"step {step_id!r}: {exc}") from exc
    except TenantProviderError as exc:
        raise StepServiceUnavailableError(f"step {step_id!r}: {exc}") from exc
