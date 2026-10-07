"""Agent test fixtures.

Hermetic model routing: agent unit tests assert the router's *logic* (provider
precedence, complexity downgrades, per-role selection). That logic must not depend
on whatever provider deployment the developer's machine happens to have exported
(a real NVIDIA / on-prem vLLM env sets OPENAI_BASE_URL, NVIDIA_API_KEY,
DEFAULT_MODEL, … which resolve_reasoning honours — silently overriding the
models the tests expect). That is the
"passes in a clean env, fails on my machine" contamination.

The autouse fixture below clears those deployment-config vars for every agent
test, so results are deterministic regardless of the ambient env. Tests that
deliberately exercise env-override behaviour still set their own values via
``monkeypatch`` after this fixture runs, so they are unaffected.
"""

from __future__ import annotations

import pytest

# Deployment/provider-selection env vars read by app.agent.model_router. Clearing
# them yields the pristine provider-profile routing the unit tests assert.
_ROUTER_ENV_VARS = (
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "NVIDIA_API_KEY",
    "NVIDIA_MODEL",
    "DEFAULT_MODEL",
    "DEFAULT_PLANNING_MODEL",
    "DEFAULT_EXECUTION_MODEL",
    "DEFAULT_VERIFICATION_MODEL",
)


@pytest.fixture(autouse=True)
def _hermetic_model_router_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ROUTER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Second contamination source: the process-wide model_registry is auto-seeded
    # from deployment config, and resolve_reasoning (behind every router) consults
    # it and the deployment role map. Neutralise both so routing is pristine;
    # monkeypatch restores them per test, and tests exercising the registry live
    # under tests/ai_router, not here.
    import app.ai_router.deployment_roles as _roles
    import app.ai_router.resolve as _resolve
    import app.ai_router.selection as _selection

    monkeypatch.setattr(_selection, "ordered_configured_models", lambda *a, **k: [])
    monkeypatch.setattr(_roles, "deployment_role_models", lambda *a, **k: {})
    monkeypatch.setattr(_resolve, "_registry_has_text_models", lambda: False)
