"""OCR-VISION-BUDGET: concurrent vision OCR calls can never spend past the budget.

The guard only asked "any budget left?" before a call and charged after it: up
to OCR_VISION_CONCURRENCY pages fell back to LLM vision at once, every one passed
the check before the first was charged, and together they overshot the budget.
Each vision call now reserves its worst-case cost atomically before it runs
(refused when the remaining budget cannot cover it), settles to the real cost
afterwards and gives the reservation back when the call fails.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from app.governance.cost import CostController
from app.intelligence.cost_tracker import calculate_cost
from app.ocr import concurrency as oc
from app.ocr.engine import OcrEngine, vision_reserve_usd
from app.providers import guarded_completion as gc
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.guarded_completion import (
    DecisionBudgetExceededError,
    complete_decision,
    tenant_charge_scope,
)
from app.tenancy.context import PlanTier, TenantContext

_MODEL = "gpt-4o-mini"
_IN, _OUT = 1_200, 300  # what one page's vision call really uses


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t-vision", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _VisionProvider:
    def __init__(self, *, fail: bool = False, out_tokens: int = _OUT) -> None:
        self._default_model = _MODEL
        self.calls = 0
        self.fail = fail
        self.out_tokens = out_tokens

    def supports_vision(self) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        await asyncio.sleep(0.05)  # every call is in flight before any is charged
        if self.fail:
            raise RuntimeError("vision model down")
        return CompletionResponse(
            content="page text", model=_MODEL, input_tokens=_IN, output_tokens=self.out_tokens
        )


@pytest.fixture(autouse=True)
def _setup() -> Any:
    saved = gc._platform_services
    oc.reset_ocr_concurrency(oc.OcrLimits(4, 3, 4, 300))  # 4 vision calls at once
    with (
        patch("app.ocr.engine._ocr_model", lambda: _MODEL),
        patch("app.ocr.engine._ocr_fallback_models", lambda _m: []),
    ):
        yield
    oc.reset_ocr_concurrency()
    gc.set_platform_cost_services(saved)


def _page() -> Image.Image:
    return Image.new("L", (850, 1100), 255)


def _controller(budget: float) -> CostController:
    controller = CostController(per_goal_usd=1_000.0, per_tenant_daily_usd=budget)
    gc.set_platform_cost_services(lambda: (controller, None))
    return controller


async def test_concurrent_vision_pages_never_spend_past_the_budget() -> None:
    reserve = vision_reserve_usd(_page(), [_MODEL], max_output_tokens=4096)
    actual = calculate_cost(_MODEL, _IN, _OUT)
    assert 0 < actual < reserve
    controller = _controller(budget=reserve * 2.5)  # room for two calls, not four
    provider = _VisionProvider()
    engine = OcrEngine()

    with tenant_charge_scope(_ctx()):
        results = await asyncio.gather(
            *(engine._llm_vision_ocr(_page(), provider=provider) for _ in range(4)),
            return_exceptions=True,
        )

    refused = [r for r in results if isinstance(r, DecisionBudgetExceededError)]
    read = [r for r in results if isinstance(r, tuple)]
    # Before: all four passed the "budget left?" check and all four were made.
    assert provider.calls == 2
    assert len(read) == 2 and len(refused) == 2
    assert all(text == "page text" for text, _c, _e in read)
    # Settled to the real cost: the unused part of each reservation came back.
    assert controller.daily_total(tenant_ctx=_ctx()) == pytest.approx(2 * actual)


async def test_a_failed_vision_call_gives_its_reservation_back() -> None:
    controller = _controller(budget=10.0)
    provider = _VisionProvider(fail=True)
    with tenant_charge_scope(_ctx()):
        text, _conf, engine_used = await OcrEngine()._llm_vision_ocr(_page(), provider=provider)
    assert (text, engine_used) == ("", "llm_vision")
    assert provider.calls == 1
    assert controller.daily_total(tenant_ctx=_ctx()) == 0.0


async def test_a_call_costlier_than_its_reservation_is_charged_in_full() -> None:
    controller = _controller(budget=10.0)
    request = CompletionRequest(messages=[Message(role="user", content="q")], model=_MODEL)
    with tenant_charge_scope(_ctx()):
        await complete_decision(
            _VisionProvider(), request, role="ocr_vision", reserve_usd=1e-7
        )
    assert controller.daily_total(tenant_ctx=_ctx()) == pytest.approx(
        calculate_cost(_MODEL, _IN, _OUT)
    )


async def test_a_reservation_the_budget_cannot_cover_is_refused_before_the_call() -> None:
    controller = _controller(budget=0.001)
    provider = _VisionProvider()
    request = CompletionRequest(messages=[Message(role="user", content="q")], model=_MODEL)
    with tenant_charge_scope(_ctx()), pytest.raises(DecisionBudgetExceededError):
        await complete_decision(provider, request, role="ocr_vision", reserve_usd=0.01)
    assert provider.calls == 0
    assert controller.daily_total(tenant_ctx=_ctx()) == 0.0


async def test_goal_scoped_reservation_latches_the_goal_when_refused() -> None:
    from types import SimpleNamespace

    controller = CostController(per_goal_usd=0.001, per_tenant_daily_usd=10.0)
    graph = SimpleNamespace(_cost_controller=controller, _cost_tracker=None, _state_lock=None)
    state = SimpleNamespace(goal_id="g-1", context={})
    provider = _VisionProvider()
    request = CompletionRequest(messages=[Message(role="user", content="q")], model=_MODEL)
    with gc.goal_charge_scope(graph, state, _ctx()), pytest.raises(DecisionBudgetExceededError):
        await complete_decision(provider, request, role="ocr_vision", reserve_usd=0.01)
    assert provider.calls == 0
    assert state.context["_budget_exhausted"] is True


def test_the_reservation_covers_the_priciest_failover_model_and_the_full_output() -> None:
    page = _page()
    cheap = vision_reserve_usd(page, [_MODEL], max_output_tokens=4096)
    both = vision_reserve_usd(page, [_MODEL, "gpt-4o"], max_output_tokens=4096)
    assert both > cheap
    assert vision_reserve_usd(page, [_MODEL], max_output_tokens=100) < cheap
    # A real page's usage fits inside the reservation.
    assert calculate_cost(_MODEL, _IN, _OUT) < cheap


async def test_in_memory_refund_never_goes_below_zero() -> None:
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    assert await controller.check_and_record(goal_id="g", cost_usd=0.5, tenant_ctx=_ctx())
    await controller.refund_async(goal_id="g", cost_usd=0.2, tenant_ctx=_ctx())
    assert controller.daily_total(tenant_ctx=_ctx()) == pytest.approx(0.3)
    assert controller.goal_total("g", tenant_ctx=_ctx()) == pytest.approx(0.3)
    await controller.refund_async(goal_id="g", cost_usd=5.0, tenant_ctx=_ctx())
    assert controller.daily_total(tenant_ctx=_ctx()) == 0.0
