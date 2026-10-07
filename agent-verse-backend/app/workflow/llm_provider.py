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


def step_model_provider(provider: Any, model: str, step_id: str) -> Any:
    """The provider that serves the step's chosen *model*, through the Model Registry.

    The LLM Prompt node's Model dropdown lists the tenant's configured registry
    models (``GET /models/configured``). The chosen model is resolved through the
    registry dispatch: a model with its own endpoint / own key is called there,
    a model the platform provider serves stays on it. A model that is not a
    configured, usable text-generation model fails the step with a clear error —
    it used to be sent to whatever provider the run had (a hard-coded
    "gpt-4o" on a self-hosted endpoint 404'd). A tenant's own BYOK provider
    keeps serving model names of its own that the registry does not know.
    """
    from app.providers.llm_resolution import is_placeholder_provider
    from app.providers.model_dispatch import ModelDispatchProvider
    from app.providers.registry_llm import configured_text_model

    entry = configured_text_model(model)
    if entry is None:
        if getattr(provider, "_byok_tenant_id", None):
            return provider
        raise StepServiceUnavailableError(
            f"llm step {step_id!r}: model {model!r} is not a configured text-generation "
            "model in the Model Registry (or its provider has no API key); pick a "
            "configured model or 'Default (registry order)'"
        )
    dispatch = provider if isinstance(provider, ModelDispatchProvider) else (
        ModelDispatchProvider(provider) if provider is not None else None
    )
    if dispatch is None:
        from app.providers.registry_llm import registry_backed_provider

        dispatch = registry_backed_provider()
    target = dispatch.target_for(model)
    if target is dispatch.inner and is_placeholder_provider(target):
        raise StepServiceUnavailableError(
            f"llm step {step_id!r}: model {model!r} is configured in the Model Registry "
            "but nothing can serve it here (no endpoint URL, no saved API key and no "
            "provider key)"
        )
    return target
