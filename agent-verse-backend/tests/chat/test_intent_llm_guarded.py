"""Chat intent disambiguation is charged and circuit-broken; failures keep regex."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.chat.intent import Intent, IntentRouter
from app.providers import guarded_completion as gc
from tests.providers._decision_fakes import RecordingController, ScriptedProvider


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


class _Tenant:
    tenant_id = "t1"


async def test_disambiguation_is_charged() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    intent = await IntentRouter()._llm_disambiguate(
        "hmm the thing", ScriptedProvider('{"intent": "goal"}'), tenant_ctx=_Tenant()
    )
    assert intent is Intent.GOAL
    assert [t for _, t in ctrl.recorded] == ["t1"]


async def test_hung_or_refused_disambiguation_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    router = IntentRouter()
    hung = await asyncio.wait_for(
        router._llm_disambiguate("x y", ScriptedProvider(hang=True), tenant_ctx=_Tenant()),
        timeout=5,
    )
    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    refused = await router._llm_disambiguate(
        "x y", ScriptedProvider('{"intent": "goal"}'), tenant_ctx=_Tenant()
    )
    assert hung is None and refused is None
