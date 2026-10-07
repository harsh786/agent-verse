"""PERC-02: a perception vision call is charged to the tenant and refused when the
tenant's budget is exhausted (nothing asserted either before)."""

from __future__ import annotations

from typing import Any

import pytest

from app.perception.browser_agent import BrowserAgent
from app.providers import guarded_completion as gc
from app.providers.guarded_completion import DecisionBudgetExceededError, tenant_charge_scope
from app.tenancy.context import PlanTier, TenantContext
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

# Vision availability is decided by the Model Registry.
pytestmark = pytest.mark.usefixtures("registry_vision")

T = TenantContext(tenant_id="t-vision", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _VisionProvider(ScriptedProvider):
    def supports_vision(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_vision_analysis_is_charged_to_the_requesting_tenant() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    provider = _VisionProvider("A login page.")
    agent = BrowserAgent(vision_provider=provider)
    with tenant_charge_scope(T):
        text = await agent.analyze_screenshot("aW1n", "What is this?", raise_errors=True)
    assert text == "A login page."
    assert [t for _, t in ctrl.recorded] == ["t-vision"]
    assert provider.requests[0].messages[-1].image_data == "aW1n"


async def test_exhausted_budget_refuses_before_calling_the_model() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    provider = _VisionProvider("never")
    agent = BrowserAgent(vision_provider=provider)
    with tenant_charge_scope(T), pytest.raises(DecisionBudgetExceededError):
        await agent.analyze_screenshot("aW1n", "What is this?", raise_errors=True)
    assert provider.requests == []


async def test_a_charge_that_exhausts_the_budget_is_reported() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(allow=False), None))
    agent = BrowserAgent(vision_provider=_VisionProvider("x"))
    with tenant_charge_scope(T), pytest.raises(DecisionBudgetExceededError):
        await agent.analyze_screenshot("aW1n", "q", raise_errors=True)


async def test_unattributed_vision_call_is_refused_when_costs_are_enforced() -> None:
    gc.set_platform_cost_services(lambda: (RecordingController(), None))
    agent = BrowserAgent(vision_provider=_VisionProvider("x"))
    with pytest.raises(DecisionBudgetExceededError, match="no tenant"):
        await agent.analyze_screenshot("aW1n", "q", raise_errors=True)
