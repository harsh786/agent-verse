"""HITL-01: the remaining fire-and-forget approval filings are durable.

The executor gates already file through ``file_persisted_approval``; the pipeline
``hitl_gate``, the org approval-gate registration and the civilization governor's
breach approval still called the sync ``request_approval``, whose row is written
by a background task that only logs a failure — the gate was then invisible to
every other replica and to the inbox.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.hitl import HITLDeliveryError, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-hitl-file", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _DownSession:
    async def __aenter__(self) -> Any:
        raise ConnectionError("db down")

    async def __aexit__(self, *a: object) -> None:
        return None


def _gateway_with_dead_db() -> HITLGateway:
    gw = HITLGateway()
    gw._db_session_factory = lambda: _DownSession()
    return gw


async def test_pipeline_hitl_gate_fails_closed_when_the_request_is_not_persisted() -> None:
    from app.pipeline.steps import hitl_gate

    gw = _gateway_with_dead_db()
    with pytest.raises(HITLDeliveryError):
        await hitl_gate(action="deploy", risk_level="high", tenant_ctx=T, gateway=gw, goal_id="g")
    assert gw.list_pending(tenant_ctx=T) == []  # never a gate nobody else can see


async def test_civilization_breach_approval_is_filed_durably() -> None:
    from app.civilization.governor import Governor

    calls: list[dict[str, Any]] = []

    class _Gw:
        async def request_approval_async(self, **kw: Any) -> str:
            calls.append(kw)
            return "req-1"

        def request_approval(self, **kw: Any) -> Any:
            raise AssertionError("fire-and-forget filing used")

    gov = Governor.__new__(Governor)
    gov._hitl = _Gw()
    gov._tenant_id = T.tenant_id
    gov._civilization_id = "civ-1"
    await gov._raise_breach_approval(["budget exceeded"])
    assert calls and calls[0]["require_persisted"] is True
