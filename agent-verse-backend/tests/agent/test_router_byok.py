"""CORE-32: auto-routing's LLM scoring runs on the tenant's BYOK provider.

The router always used the platform provider captured at startup, so a BYOK
tenant's goal text went to — and was billed by — the platform vendor. It now
resolves the provider per call from the tenant; a BYOK config that cannot be
read or built skips LLM scoring (keyword routing, reason recorded) instead of
falling back to platform spend.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.agent.router import AgentRouter
from app.providers import guarded_completion as gc
from app.providers.tenant_provider import TenantProviderError
from app.services.llm_config_store import LLMConfigReadError
from app.tenancy.context import PlanTier, TenantContext
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_CTX = TenantContext(tenant_id="t-byok-route", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_AGENTS = [
    {"agent_id": "a1", "name": "Billing", "goal_template": "pay invoices"},
    {"agent_id": "a2", "name": "Support", "goal_template": "answer tickets"},
]
_ANSWER = '{"best_agent_id": "a1", "confidence": 0.9, "reasoning": "billing"}'


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    gc.set_platform_cost_services(lambda: (RecordingController(), None))
    yield
    gc.set_platform_cost_services(saved)


def _router(platform: Any) -> AgentRouter:
    return AgentRouter(agent_store=None, llm_provider=platform, app_state=SimpleNamespace())


async def test_byok_tenant_is_scored_by_its_own_provider() -> None:
    platform, tenant = ScriptedProvider(_ANSWER), ScriptedProvider(_ANSWER)
    with patch(
        "app.providers.tenant_provider.resolve_tenant_byok_provider", return_value=tenant
    ) as resolve:
        decision = await _router(platform).route(
            "pay the invoice", tenant_ctx=_CTX, available_agents=_AGENTS
        )
    assert resolve.call_args.args[1] == _CTX.tenant_id
    assert len(tenant.requests) == 1
    assert platform.requests == []  # never the platform vendor
    assert decision.agent_id == "a1" and decision.llm_scoring == ""


async def test_tenant_without_byok_uses_the_platform_provider() -> None:
    platform = ScriptedProvider(_ANSWER)
    with patch(
        "app.providers.tenant_provider.resolve_tenant_byok_provider", return_value=None
    ):
        await _router(platform).route("pay the invoice", tenant_ctx=_CTX, available_agents=_AGENTS)
    assert len(platform.requests) == 1


@pytest.mark.parametrize(
    "error", [TenantProviderError("decrypt failed"), LLMConfigReadError("db down")]
)
async def test_unusable_byok_skips_llm_scoring_instead_of_platform_spend(
    error: Exception,
) -> None:
    platform = ScriptedProvider(_ANSWER)
    with patch(
        "app.providers.tenant_provider.resolve_tenant_byok_provider", side_effect=error
    ):
        decision = await _router(platform).route(
            "pay the invoice", tenant_ctx=_CTX, available_agents=_AGENTS
        )
    assert platform.requests == []
    assert decision.llm_scoring.startswith("byok_unusable")
    # Keyword/history scoring still decides (no LLM blend).
    assert all(not s.reasons for s in decision.all_scores)


def test_main_wires_the_router_with_app_state() -> None:
    import inspect

    import app.main as main_mod

    src = inspect.getsource(main_mod)
    start = src.index("_agent_router = AgentRouter(")
    assert "app_state=app.state" in src[start : start + 300]
