"""End-to-end test for the shipped ``merchant-kyc`` workflow template.

Runs the REAL WorkflowRunner (real conditional routing + real HITL pause/resume)
with the OCR / RPA / LLM / HTTP steps mocked via ``is_test_run`` +
``mock_overrides`` — so the pipeline's *structure, data flow, risk branching,
human-review escalation and portal callback* are all exercised deterministically,
independent of any live model.

Two scenarios, matching the design:
  * low-risk merchant  → auto-approve, NO human gate, callback fires
  * high-risk merchant → pauses at the compliance-officer HITL gate; approving it
                         resumes the run and the callback fires with the
                         reviewer's decision

Mirrors tests/workflow/test_hitl_runner_integration.py for the runner wiring.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import WorkflowDefinition
from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest
from app.workflow.runner import WorkflowRunner
from app.workflow.state import WorkflowRunStatus
from app.workflow.template_store import SystemTemplateStore

pytestmark = pytest.mark.asyncio


# ── Minimal in-memory run-store double (only what trigger/resume touch) ───────
class _FakeRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._definitions: dict[str, WorkflowDefinition] = {}
        self.final_status: dict[str, Any] = {}  # run_id → latest run-level status

    def register_definition(self, definition: WorkflowDefinition) -> None:
        self._definitions[definition.id] = definition

    async def create(
        self, *, run_id: str, workflow_id: str, tenant_id: str, **_kwargs: Any
    ) -> None:
        self._runs[run_id] = {"workflow_id": workflow_id, "tenant_id": tenant_id}

    async def get_workflow_id(self, run_id: str, tenant_id: str | None = None) -> str:
        return str(self._runs[run_id]["workflow_id"])

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self._definitions[workflow_id].to_json()

    async def update_status(
        self, run_id: str, status: Any, *, tenant_id: str, **_kwargs: Any
    ) -> bool:
        # The runner finalizes the run-level status here (the graph checkpoint
        # only tracks step_outputs + halt states like WAITING_HITL).
        self.final_status[run_id] = status
        return True


def _make_resume_callback(runner: WorkflowRunner) -> Any:
    async def _resume(req: WorkflowHITLRequest) -> None:
        await runner.resume_from_hitl(
            run_id=req.run_id,
            step_id=req.step_id,
            action=req.action_taken or "",
            actor_id=req.reviewed_by or "",
            note=req.note,
            form_data=req.form_data,
            tenant_id=req.tenant_id,
        )

    return _resume


def _kyc_definition() -> WorkflowDefinition:
    """Load the ACTUAL shipped template (not a hand-rolled copy)."""
    definition = SystemTemplateStore().get("merchant-kyc").definition
    definition.id = "wf-merchant-kyc"
    return definition


def _build_runner(
    definition: WorkflowDefinition,
) -> tuple[WorkflowRunner, WorkflowCompiler, HITLWorkflowGateway, _FakeRunStore]:
    run_store = _FakeRunStore()
    run_store.register_definition(definition)
    hitl_gateway = HITLWorkflowGateway()
    compiler = WorkflowCompiler(
        context_resolver=ContextResolver(),
        run_store=run_store,
        hitl_workflow_gateway=hitl_gateway,
    )
    runner = WorkflowRunner(compiler=compiler, run_store=run_store)
    hitl_gateway._resume_callback = _make_resume_callback(runner)
    return runner, compiler, hitl_gateway, run_store


_INPUTS = {
    "merchant_id": "mch_test_001",
    "application": {
        "business_name": "Acme Widgets LLC",
        "registration_number": "REG-99887766",
        "address": "1 Market St, San Francisco, CA",
        "owner_name": "Jordan Lee",
    },
    "registration_doc": "ZmFrZS1yZWctYjY0",
    "owner_id_doc": "ZmFrZS1pZC1iNjQ=",
    "website_url": "https://acme-widgets.example.com",
    "callback_url": "https://portal.example.com/webhooks/kyc-result",
}


def _mocks(*, risk_tier: str) -> dict[str, Any]:
    """Mock every external (OCR/RPA/LLM/HTTP) step; logic steps run for real."""
    return {
        "ocr_registration": {"raw_text": "Acme Widgets LLC — REG-99887766 — 1 Market St"},
        "parse_registration": {
            "business_name": "Acme Widgets LLC",
            "registration_number": "REG-99887766",
            "address": "1 Market St, San Francisco, CA",
        },
        "ocr_owner_id": {"raw_text": "Jordan Lee — ID 55-2211 — exp 2031"},
        "parse_owner_id": {"owner_name": "Jordan Lee", "id_number": "55-2211", "expiry_date": "2031"},
        "scan_website": {"report": {"title": "Acme Widgets", "sections": ["home", "contact"]}},
        "assess_website": {
            "live": True,
            "matches_business": True,
            "has_contact_page": True,
            "has_privacy_page": True,
            "prohibited_flags": [],
        },
        "risk_assessment": {
            "risk_tier": risk_tier,
            "risk_score": 0.1 if risk_tier == "low" else 0.85,
            "consistency_ok": risk_tier == "low",
            "flags": [] if risk_tier == "low" else ["ownership_mismatch"],
            "recommendation": "approve" if risk_tier == "low" else "refer",
        },
        "callback": {"status_code": 200, "body": {"received": True}},
    }


async def _final_state(compiler: WorkflowCompiler, definition: WorkflowDefinition, run_id: str):
    compiled = compiler.compile(definition)
    snap = await compiled.aget_state({"configurable": {"thread_id": run_id}})
    return snap.values


async def test_low_risk_merchant_auto_approves_and_calls_back() -> None:
    definition = _kyc_definition()
    runner, compiler, hitl_gateway, store = _build_runner(definition)

    run_id = await runner.run(
        workflow_id=definition.id,
        tenant_id="t-kyc",
        inputs=_INPUTS,
        is_test_run=True,
        mock_overrides=_mocks(risk_tier="low"),
    )

    # No human gate for low risk — nothing is pending in the approvals inbox.
    _pending, total = await hitl_gateway.list_pending(tenant_id="t-kyc")
    assert total == 0

    # Run reached a terminal COMPLETE (finalized on the run row by the runner).
    assert store.final_status[run_id] == WorkflowRunStatus.COMPLETE

    values = await _final_state(compiler, definition, run_id)
    outs = values["step_outputs"]

    # Low-risk branch ran; human review did not.
    assert "set_decision_auto" in outs
    assert "set_decision_human" not in outs

    # The assembled result package carries the decision + evidence the portal gets.
    pkg = outs["assemble"]
    assert pkg["merchant_id"] == "mch_test_001"
    assert pkg["decision"] == "auto_approved"
    assert pkg["risk"]["risk_tier"] == "low"
    assert pkg["evidence"]["registration"]["registration_number"] == "REG-99887766"

    # Callback to the onboarding portal fired.
    assert outs["callback"]["status_code"] == 200


async def test_high_risk_merchant_escalates_then_callbacks_on_approve() -> None:
    definition = _kyc_definition()
    runner, compiler, hitl_gateway, store = _build_runner(definition)

    run_id = await runner.run(
        workflow_id=definition.id,
        tenant_id="t-kyc",
        inputs=_INPUTS,
        is_test_run=True,
        mock_overrides=_mocks(risk_tier="high"),
    )

    # High risk pauses at the compliance-officer HITL gate.
    values = await _final_state(compiler, definition, run_id)
    assert values["status"] == WorkflowRunStatus.WAITING_HITL

    pending, total = await hitl_gateway.list_pending(tenant_id="t-kyc")
    assert total == 1
    req = pending[0]
    assert req.run_id == run_id
    assert req.step_id == "human_review"

    # Reviewer approves → run resumes, decision recorded, callback fires.
    await hitl_gateway.decide(req.request_id, action="approve", actor_id="officer-7")

    values = await _final_state(compiler, definition, run_id)
    assert values["status"] != WorkflowRunStatus.WAITING_HITL
    assert store.final_status[run_id] == WorkflowRunStatus.COMPLETE
    outs = values["step_outputs"]
    assert outs["human_review"]["action"] == "approve"
    assert "set_decision_auto" not in outs
    assert outs["assemble"]["decision"] == "approve"
    assert outs["callback"]["status_code"] == 200

    _pending_after, total_after = await hitl_gateway.list_pending(tenant_id="t-kyc")
    assert total_after == 0
