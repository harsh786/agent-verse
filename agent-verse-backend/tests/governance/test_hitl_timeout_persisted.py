"""HITL-09: an approval wait that times out is expired in the DB too.

The timeout wrote TIMED_OUT only in the waiting process's memory; until the beat
expiry ran, an approver could still approve the row (the CAS won, the API said
"approved") for an action the agent had already abandoned.
"""

from __future__ import annotations

from typing import Any

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-hitl-timeout", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _gateway(monkeypatch: Any, *, cas_wins: bool, db_status: str | None = None) -> Any:
    gw = HITLGateway()
    calls: list[tuple[str, str]] = []

    async def _resolve(
        request_id: str, tenant_id: str, status: str, approver: str = "", note: str = ""
    ) -> bool:
        calls.append((request_id, status))
        return cas_wins

    async def _read(request_id: str, tenant_id: str) -> str | None:
        return db_status

    monkeypatch.setattr(gw, "_db_update_resolution", _resolve)
    monkeypatch.setattr(gw, "_db_read_status", _read)
    gw.calls = calls
    return gw


async def test_timeout_is_written_to_the_db_row(monkeypatch: Any) -> None:
    gw = _gateway(monkeypatch, cas_wins=True)
    rid = gw.request_approval(goal_id="g", action="deploy", tenant_ctx=T)
    status = await gw.wait_for_approval(str(rid), tenant_ctx=T, timeout=0.05)
    assert status == ApprovalStatus.TIMED_OUT
    assert gw.calls == [(str(rid), "expired")]


async def test_a_decision_that_won_the_race_is_honoured(monkeypatch: Any) -> None:
    gw = _gateway(monkeypatch, cas_wins=False, db_status="approved")
    rid = gw.request_approval(goal_id="g", action="deploy", tenant_ctx=T)
    status = await gw.wait_for_approval(str(rid), tenant_ctx=T, timeout=0.05)
    assert status == ApprovalStatus.APPROVED
