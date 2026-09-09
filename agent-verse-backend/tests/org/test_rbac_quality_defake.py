"""WS-2c: finish the RBAC dependency + quality-gate peer-review / evaluator TODOs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.org.quality_gates import GateResult, QualityGateSystem
from app.org.rbac import OrgRole, _resolve_actor_role, enforce_org_role


class _FakeRequest:
    def __init__(self, role: str | None) -> None:
        state = SimpleNamespace()
        if role is not None:
            state.org_role = role
        self.state = state


def test_resolve_actor_role_reads_request_state() -> None:
    assert _resolve_actor_role(_FakeRequest("dept_admin")) == "dept_admin"
    # Missing role → most restrictive default (viewer), never an implicit admin.
    assert _resolve_actor_role(_FakeRequest(None)) == OrgRole.VIEWER


def test_enforce_org_role_denies_insufficient() -> None:
    # viewer cannot meet a team_lead minimum.
    with pytest.raises(HTTPException) as ei:
        enforce_org_role(_FakeRequest("viewer"), OrgRole.TEAM_LEAD)
    assert ei.value.status_code == 403


def test_enforce_org_role_allows_sufficient() -> None:
    # org_admin clears any minimum.
    enforce_org_role(_FakeRequest("org_admin"), OrgRole.DEPT_ADMIN)


@pytest.mark.asyncio
async def test_peer_review_gate_runs_with_provider() -> None:
    """Gate 4 must actually run (not SKIP) when a peer-reviewer provider exists."""
    from app.providers.fake import FakeProvider

    qgs = QualityGateSystem(run_peer_review=True, llm_provider=FakeProvider())
    score = await qgs.evaluate(
        "A sufficiently detailed answer covering the requested points in depth.",
        {"output_type": "text"},
    )
    gate4 = next(g for g in score.gates if g.gate_id == 4)
    assert gate4.result != GateResult.SKIP, "peer review must run when a provider is wired"


@pytest.mark.asyncio
async def test_peer_review_gate_explicit_skip_without_provider() -> None:
    """Without a provider, Gate 4 explicitly SKIPs (honest) rather than faking a score."""
    qgs = QualityGateSystem(run_peer_review=True, llm_provider=None)
    score = await qgs.evaluate("Some output text that is long enough.", {})
    gate4 = next(g for g in score.gates if g.gate_id == 4)
    assert gate4.result == GateResult.SKIP
    assert gate4.score == 0.0


@pytest.mark.asyncio
async def test_evaluator_gate_uses_llm_when_available() -> None:
    from app.providers.fake import FakeProvider

    qgs = QualityGateSystem(run_evaluator=True, llm_provider=FakeProvider())
    score = await qgs.evaluate("A detailed, structured answer.", {"output_type": "text"})
    gate3 = next(g for g in score.gates if g.gate_id == 3)
    assert gate3.result in (GateResult.PASS, GateResult.FAIL)
    assert "llm" in gate3.details.lower() or gate3.score > 0
