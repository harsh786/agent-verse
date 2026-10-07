"""Workflow approval SLA timeouts: every ``timeout_action`` is applied, exactly once.

Live finding (tests/real_world/test_mongo_pipeline_failures.py::
test_approval_timeout_never_auto_proceeds): a 1-minute gate with
``timeout_action: escalate`` was not escalated 600 s later. Causes:

* the sweep ran every 900 s;
* only ``escalate`` was acted on — auto_approve / auto_reject / pause were
  accepted by the DSL and silently did nothing;
* an escalation was invisible on the approval (no ``escalated_at`` /
  ``escalation_level``), only a discussion entry;
* ``90s`` / ``1h30m`` step timeouts were 48 h / a crash.

Real-Postgres coverage: tests/workflow/test_hitl_timeout_sweep_pg.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.testing import capture_logs

from app.workflow import hitl_extension
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.hitl_extension import (
    CLAIM_APPLIED,
    CLAIM_NOT_PENDING,
    CLAIM_RUN_ENDED,
    CLAIM_RUN_NOT_WAITING,
    SLA_TIMEOUT_ACTOR,
    ApprovalAlreadyDecidedError,
    HITLWorkflowGateway,
    TimeoutPlan,
    WorkflowHITLRequest,
    effective_deadline,
)
from app.workflow.state import WorkflowRunStatus
from app.workflow.steps.hitl_step import HITLStepNode, parse_approval_timeout_hours

T = "tenant-1"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
PAST = (NOW - timedelta(minutes=2)).isoformat()


class _FakeEventRedis:
    """Async Redis double for the trigger bus (XADD + PUBLISH)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any], **_kw: Any) -> str:
        self.events.append((fields["channel"], json.loads(fields["data"])))
        return "1-0"

    async def publish(self, _channel: str, _data: str) -> int:
        return 1


def _gateway() -> tuple[HITLWorkflowGateway, list[WorkflowHITLRequest], _FakeEventRedis]:
    resumed: list[WorkflowHITLRequest] = []

    async def resume(req: WorkflowHITLRequest) -> None:
        resumed.append(req)

    events = _FakeEventRedis()
    gw = HITLWorkflowGateway(resume_callback=resume, event_redis=events)
    return gw, resumed, events


async def _pending(gw: HITLWorkflowGateway, **overrides: Any) -> WorkflowHITLRequest:
    fields: dict[str, Any] = {
        "tenant_id": T,
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "step_id": "gate",
        "assigned_to": "alice",
        "assignment_strategy": "specific_user",
        "deadline_at": PAST,
        "escalation_to_role": "managers",
    }
    fields.update(overrides)
    req = WorkflowHITLRequest(**fields)
    await gw._save(req)
    return req


async def _stored(gw: HITLWorkflowGateway, rid: str) -> WorkflowHITLRequest:
    got = await gw.get_request(rid, T)
    assert got is not None
    return got


# ── deadline derivation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("timeout", "hours"),
    [
        ("1m", 1 / 60),
        ("90s", 90 / 3600),
        ("2h", 2.0),
        ("1h30m", 1.5),
        ("3d", 72.0),
        ("1w", 168.0),
        ("45 min", 0.75),
        ("2H", 2.0),
        # Unset, the generic 60s step default, junk and non-positive values all
        # mean the 48 h approval default — never a shorter deadline.
        ("", 48.0),
        ("60s", 48.0),
        ("soon", 48.0),
        ("10", 48.0),
        ("0m", 48.0),
        ("1h30x", 48.0),
    ],
)
def test_approval_timeout_parsing(timeout: str, hours: float) -> None:
    assert parse_approval_timeout_hours(timeout) == pytest.approx(hours)


async def test_deadline_is_created_at_plus_the_step_timeout() -> None:
    gw = HITLWorkflowGateway()
    step = StepDefinition(
        id="finance_signoff",
        type="hitl",
        timeout="1m",
        timeout_action="escalate",
        escalation={"after": "1m", "to_role": "admin"},
    )
    node = HITLStepNode(step, ContextResolver(), hitl_workflow_gateway=gw)
    out = await node.execute({"run_id": "run-9", "tenant_id": T, "workflow_id": "wf-9"})
    assert out["status"] == WorkflowRunStatus.WAITING_HITL
    (req,) = gw._store.values()
    created = datetime.fromisoformat(req.created_at)
    deadline = datetime.fromisoformat(str(req.deadline_at))
    assert deadline - created == timedelta(minutes=1)
    assert req.timeout_action == "escalate" and req.escalation_to_role == "admin"
    assert effective_deadline(req) == deadline


def test_effective_deadline_falls_back_to_escalation_after_hours() -> None:
    req = WorkflowHITLRequest(created_at=NOW.isoformat(), escalation_after_hours=2.0)
    assert effective_deadline(req) == NOW + timedelta(hours=2)
    assert effective_deadline(WorkflowHITLRequest(escalation_after_hours=0)) is None


# ── each action ──────────────────────────────────────────────────────────────


async def test_escalate_reassigns_keeps_pending_and_never_resumes() -> None:
    gw, resumed, events = _gateway()
    req = await _pending(gw, timeout_action="escalate")
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["escalated"] == 1
    got = await _stored(gw, req.request_id)
    assert got.status == "pending"  # the run keeps waiting for a human
    assert got.assigned_role == "managers" and got.assigned_to is None
    assert got.escalated_at == NOW.isoformat() and got.escalation_level == 1
    assert got.timed_out_at == NOW.isoformat() and got.timeout_outcome == "escalated"
    assert got.discussion[-1]["type"] == "escalation"
    assert got.discussion[-1]["by"] == SLA_TIMEOUT_ACTOR
    assert resumed == [] and events.events == []


async def test_escalate_never_auto_proceeds_however_often_the_sweep_runs() -> None:
    gw, resumed, _events = _gateway()
    req = await _pending(gw, timeout_action="escalate")
    for minutes in range(0, 600, 60):
        await gw.check_and_escalate_overdue(now=NOW + timedelta(minutes=minutes))
    got = await _stored(gw, req.request_id)
    assert got.status == "pending" and got.action_taken is None and got.reviewed_by is None
    assert got.escalation_level == 1  # escalated once, not every sweep
    assert resumed == []


async def test_escalate_with_escalation_disabled_only_marks_the_timeout() -> None:
    gw, resumed, _events = _gateway()
    req = await _pending(gw, timeout_action="escalate", allow_escalate=False)
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["skipped"] == 1
    got = await _stored(gw, req.request_id)
    assert got.status == "pending" and got.assigned_to == "alice"
    assert got.timeout_outcome == "escalation_disallowed" and got.escalation_level == 0
    assert resumed == []
    # Marked handled: never re-queued (it would head the deadline-ordered batch).
    assert (await gw.check_and_escalate_overdue(now=NOW))["skipped"] == 0


async def test_auto_reject_decides_as_the_sla_actor_and_resumes_down_the_rejection() -> None:
    gw, resumed, events = _gateway()
    req = await _pending(gw, timeout_action="auto_reject")
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["auto_rejected"] == 1
    got = await _stored(gw, req.request_id)
    assert got.status == "rejected" and got.action_taken == "reject"
    assert got.reviewed_by == SLA_TIMEOUT_ACTOR and got.reviewed_at == NOW.isoformat()
    assert got.timeout_outcome == "auto_rejected" and "deadline" in got.note
    assert [r.action_taken for r in resumed] == ["reject"]
    assert resumed[0].reviewed_by == SLA_TIMEOUT_ACTOR
    # The same HITL trigger event a human rejection publishes.
    assert [(c, p["approver"], p["action"]) for c, p in events.events] == [
        ("hitl.rejected", SLA_TIMEOUT_ACTOR, "reject")
    ]


async def test_auto_reject_takes_the_steps_declared_reject_branch() -> None:
    gw, resumed, _events = _gateway()
    actions = [{"id": "release", "next": ""}, {"id": "deny", "next": "notify_customer"}]
    req = await _pending(gw, timeout_action="auto_reject", actions=actions)
    await gw.check_and_escalate_overdue(now=NOW)
    got = await _stored(gw, req.request_id)
    assert got.action_taken == "deny" and got.status == "rejected"
    assert [r.action_taken for r in resumed] == ["deny"]


async def test_auto_approve_is_logged_as_a_warning_and_resumes() -> None:
    gw, resumed, events = _gateway()
    req = await _pending(gw, timeout_action="auto_approve")
    log = structlog.get_logger(hitl_extension.__name__)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(hitl_extension, "_log", log)
        with capture_logs() as logs:
            result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["auto_approved"] == 1
    got = await _stored(gw, req.request_id)
    assert got.status == "approved" and got.action_taken == "approve"
    assert got.reviewed_by == SLA_TIMEOUT_ACTOR and got.timeout_outcome == "auto_approved"
    assert [r.action_taken for r in resumed] == ["approve"]
    assert [c for c, _ in events.events] == ["hitl.approved"]
    warned = [e for e in logs if e["event"] == "hitl_sla_auto_approved"]
    assert warned and warned[0]["log_level"] == "warning"
    assert warned[0]["request_id"] == req.request_id


async def test_pause_keeps_the_approval_pending_and_does_not_resume() -> None:
    gw, resumed, events = _gateway()
    req = await _pending(gw, timeout_action="pause")
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["paused"] == 1
    got = await _stored(gw, req.request_id)
    assert got.status == "pending" and got.timeout_outcome == "paused"
    assert got.assigned_to == "alice"  # still visible to its reviewer
    assert resumed == [] and events.events == []
    # A human can still decide the paused gate, which resumes the run.
    decided = await gw.decide(req.request_id, "approve", "alice", tenant_id=T)
    assert decided.status == "approved" and [r.reviewed_by for r in resumed] == ["alice"]


async def test_not_yet_due_and_non_pending_requests_are_untouched() -> None:
    gw, resumed, _events = _gateway()
    future = await _pending(gw, deadline_at=(NOW + timedelta(seconds=1)).isoformat())
    done = await _pending(gw, status="approved", timeout_action="auto_reject")
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert sum(result[k] for k in ("escalated", "auto_rejected", "paused")) == 0
    assert (await _stored(gw, future.request_id)).timed_out_at is None
    assert (await _stored(gw, done.request_id)).status == "approved"
    assert resumed == []


# ── exactly once ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["escalate", "auto_reject", "auto_approve", "pause"])
async def test_each_action_is_applied_once(action: str) -> None:
    gw, resumed, events = _gateway()
    req = await _pending(gw, timeout_action=action)
    first = await gw.check_and_escalate_overdue(now=NOW)
    second = await gw.check_and_escalate_overdue(now=NOW + timedelta(minutes=5))
    applied = ("escalated", "auto_rejected", "auto_approved", "paused")
    assert sum(first[k] for k in applied) == 1
    assert sum(second[k] for k in applied) == 0
    got = await _stored(gw, req.request_id)
    assert len([d for d in got.discussion if d.get("by") == SLA_TIMEOUT_ACTOR]) == 1
    assert len(resumed) == (1 if action.startswith("auto_") else 0)
    assert len(events.events) == (1 if action.startswith("auto_") else 0)


async def test_legacy_sla_escalation_is_not_repeated() -> None:
    gw, _resumed, _events = _gateway()
    req = await _pending(
        gw, discussion=[{"type": "escalation", "by": "system:sla", "at": PAST}]
    )
    assert (await gw.check_and_escalate_overdue(now=NOW))["escalated"] == 0
    assert (await _stored(gw, req.request_id)).escalation_level == 0


async def test_batch_is_bounded_and_most_overdue_first() -> None:
    gw, _resumed, _events = _gateway()
    reqs = [
        await _pending(gw, deadline_at=(NOW - timedelta(minutes=m)).isoformat())
        for m in (1, 30, 10)
    ]
    first = await gw.check_and_escalate_overdue(now=NOW, limit=2)
    assert first["checked"] == 2 and first["escalated"] == 2
    handled = {r.request_id for r in reqs if (gw._store[r.request_id]).timed_out_at}
    assert handled == {reqs[1].request_id, reqs[2].request_id}  # 30 and 10 min overdue
    assert (await gw.check_and_escalate_overdue(now=NOW, limit=2))["escalated"] == 1


# ── a human decision racing the sweep ───────────────────────────────────────


async def test_sweep_after_a_human_decision_does_nothing() -> None:
    gw, resumed, _events = _gateway()
    req = await _pending(gw, timeout_action="auto_reject")
    await gw.decide(req.request_id, "approve", "alice", tenant_id=T)
    result = await gw.check_and_escalate_overdue(now=NOW, candidates=[req])
    assert result["auto_rejected"] == 0 and result["skipped"] == 1  # lost the race
    got = await _stored(gw, req.request_id)
    assert got.status == "approved" and got.reviewed_by == "alice"
    assert [r.reviewed_by for r in resumed] == ["alice"]


async def test_human_decision_after_an_auto_reject_is_a_conflict() -> None:
    gw, resumed, _events = _gateway()
    req = await _pending(gw, timeout_action="auto_reject")
    await gw.check_and_escalate_overdue(now=NOW)
    with pytest.raises(ApprovalAlreadyDecidedError) as exc:
        await gw.decide(req.request_id, "approve", "alice", tenant_id=T)
    assert exc.value.request.reviewed_by == SLA_TIMEOUT_ACTOR
    assert [r.reviewed_by for r in resumed] == [SLA_TIMEOUT_ACTOR]


@pytest.mark.parametrize("action", ["auto_reject", "auto_approve"])
async def test_concurrent_human_decision_and_sweep_resume_exactly_once(action: str) -> None:
    for _ in range(25):
        gw, resumed, events = _gateway()
        req = await _pending(gw, timeout_action=action)
        human, sweep = await asyncio.gather(
            gw.decide(req.request_id, "approve", "alice", tenant_id=T),
            gw.check_and_escalate_overdue(now=NOW),
            return_exceptions=True,
        )
        got = await _stored(gw, req.request_id)
        assert len(resumed) == 1 and len(events.events) == 1
        assert resumed[0].reviewed_by == got.reviewed_by
        if got.reviewed_by == "alice":
            assert isinstance(human, WorkflowHITLRequest)
            assert isinstance(sweep, dict)
            assert sweep["auto_rejected"] + sweep["auto_approved"] == 0
        else:
            assert got.reviewed_by == SLA_TIMEOUT_ACTOR
            assert isinstance(human, ApprovalAlreadyDecidedError)


class _AtomicStore:
    """Durable-store double: compare-and-set decisions / timeouts with an await
    between read and write, so concurrent callers genuinely interleave."""

    def __init__(self) -> None:
        self.rows: dict[str, WorkflowHITLRequest] = {}
        self._lock = asyncio.Lock()

    async def save(self, req: WorkflowHITLRequest) -> None:
        self.rows[req.request_id] = dataclasses.replace(req)

    async def get(self, rid: str, tenant_id: str | None = None) -> WorkflowHITLRequest | None:
        row = self.rows.get(rid)
        return dataclasses.replace(row) if row is not None else None

    async def decide_if_pending(self, req: WorkflowHITLRequest) -> bool:
        await asyncio.sleep(0)
        async with self._lock:
            if self.rows[req.request_id].status != "pending":
                return False
            self.rows[req.request_id] = dataclasses.replace(req)
            return True

    async def claim_timeout(
        self, req: WorkflowHITLRequest, plan: TimeoutPlan, *, now: datetime
    ) -> tuple[str, WorkflowHITLRequest | None]:
        await asyncio.sleep(0)
        async with self._lock:
            row = self.rows[req.request_id]
            if row.status != "pending" or row.timed_out_at:
                return CLAIM_NOT_PENDING, None
            updated = dataclasses.replace(row, discussion=[*row.discussion, plan.entry])
            for key, value in plan.patch.items():
                setattr(updated, key, value)
            if plan.status:
                updated.status = plan.status
            self.rows[req.request_id] = updated
            return CLAIM_APPLIED, dataclasses.replace(updated)


async def test_store_path_race_has_one_winner() -> None:
    for _ in range(25):
        resumed: list[str] = []

        async def resume(req: WorkflowHITLRequest, resumed: list[str] = resumed) -> None:
            resumed.append(str(req.reviewed_by))

        store = _AtomicStore()
        gw = HITLWorkflowGateway(approval_store=store, resume_callback=resume)
        req = WorkflowHITLRequest(
            tenant_id=T, run_id="run-1", step_id="gate", deadline_at=PAST,
            timeout_action="auto_reject",
        )
        await store.save(req)
        await asyncio.gather(
            gw.decide(req.request_id, "approve", "alice", tenant_id=T),
            gw.check_and_escalate_overdue(now=NOW, candidates=[req]),
            return_exceptions=True,
        )
        final = store.rows[req.request_id]
        assert resumed == [final.reviewed_by]
        assert final.status == ("approved" if final.reviewed_by == "alice" else "rejected")


@pytest.mark.parametrize(
    ("claim", "outcome"),
    [
        ((CLAIM_NOT_PENDING, None), "skipped"),
        ((CLAIM_RUN_NOT_WAITING, None), "skipped"),
        ((CLAIM_RUN_ENDED, WorkflowHITLRequest(status="expired")), "expired"),
    ],
)
async def test_store_claim_results_never_resume(
    claim: tuple[str, WorkflowHITLRequest | None], outcome: str
) -> None:
    store = AsyncMock()
    store.claim_timeout = AsyncMock(return_value=claim)
    resume = AsyncMock()
    gw = HITLWorkflowGateway(approval_store=store, resume_callback=resume)
    req = WorkflowHITLRequest(tenant_id=T, deadline_at=PAST, timeout_action="auto_approve")
    result = await gw.check_and_escalate_overdue(now=NOW, candidates=[req])
    assert result[outcome] == 1
    resume.assert_not_awaited()


async def test_overdue_scan_uses_the_indexed_store_query() -> None:
    store = AsyncMock()
    store.list_overdue_pending = AsyncMock(return_value=[])
    gw = HITLWorkflowGateway(approval_store=store)
    result = await gw.check_and_escalate_overdue(now=NOW, limit=7)
    store.list_overdue_pending.assert_awaited_once_with(now=NOW, limit=7)
    assert result["checked"] == 0


async def test_a_resume_failure_is_counted_and_logged() -> None:
    async def boom(_req: WorkflowHITLRequest) -> None:
        raise RuntimeError("broker down")

    gw = HITLWorkflowGateway(resume_callback=boom)
    await _pending(gw, timeout_action="auto_reject")
    result = await gw.check_and_escalate_overdue(now=NOW)
    assert result["failed"] == 1


# ── resume of an SLA-paused run ─────────────────────────────────────────────


class _PausedRunStore:
    """Run-store double honouring ``only_from`` compare-and-set."""

    def __init__(self, status: str, run_metadata: dict[str, Any]) -> None:
        self.status = status
        self.record = {"inputs": {}, "labels": {}, "run_metadata": run_metadata}

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        return {**self.record, "status": self.status}

    async def get_workflow_id(self, run_id: str, tenant_id: str) -> str:
        return "wf-1"

    async def update_status(
        self, run_id: str, status: Any, *, tenant_id: str, only_from: Any = None, **_kw: Any
    ) -> bool:
        if only_from is not None and self.status not in set(only_from):
            return False
        self.status = str(getattr(status, "value", status))
        return True


async def _resume_paused(run_metadata: dict[str, Any]) -> tuple[_PausedRunStore, Any]:
    from app.workflow.dsl import WorkflowDefinition
    from app.workflow.runner import WorkflowRunner

    store = _PausedRunStore("paused", run_metadata)
    compiled = AsyncMock()
    compiled.ainvoke = AsyncMock(return_value={"status": WorkflowRunStatus.COMPLETE})
    compiled.adelete_thread = AsyncMock()
    compiler = type("C", (), {"compile": lambda self, d: compiled})()
    runner = WorkflowRunner(compiler=compiler, run_store=store)
    runner._load_run_definition = AsyncMock(  # type: ignore[method-assign]
        return_value=WorkflowDefinition(id="wf-1", name="wf")
    )
    await runner.execute_resume_fresh(
        "run-1", "wf-1", T, step_id="gate", action="approve", actor_id="alice"
    )
    return store, compiled


async def test_decision_resumes_a_run_the_sla_paused_at_that_step() -> None:
    store, compiled = await _resume_paused({"hitl_timeout_pause": {"step_id": "gate"}})
    compiled.ainvoke.assert_awaited_once()
    assert store.status == "complete"


async def test_decision_does_not_resume_an_otherwise_paused_run() -> None:
    store, compiled = await _resume_paused({"hitl_timeout_pause": {"step_id": "other"}})
    compiled.ainvoke.assert_not_awaited()
    assert store.status == "paused"


async def test_operator_resume_of_an_sla_paused_run_returns_it_to_the_gate() -> None:
    from app.workflow.service import WorkflowService

    store = _PausedRunStore("paused", {"hitl_timeout_pause": {"step_id": "gate"}})
    svc = WorkflowService(AsyncMock(), run_store=store)
    assert await svc.resume_run(T, "run-1") is True
    assert store.status == "waiting_hitl"  # re-running would open a second approval

    plain = _PausedRunStore("paused", {})
    assert await WorkflowService(AsyncMock(), run_store=plain).resume_run(T, "run-1")
    assert plain.status == "running"


# ── Celery task / beat ──────────────────────────────────────────────────────


def test_sweep_runs_every_minute_by_default() -> None:
    from app.scaling.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["workflow-check-hitl-escalations"]
    assert entry["task"] == "workflow.check_hitl_escalations"
    assert float(entry["schedule"]) == 60.0
    assert 0 < float(entry["options"]["expires"]) <= 60.0


def test_sweep_task_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.scaling.beat_guard as beat_guard
    from app.workflow import celery_tasks as ct

    sweep = AsyncMock(return_value={"checked": 0})
    monkeypatch.setattr(HITLWorkflowGateway, "check_and_escalate_overdue", sweep)
    locks = beat_guard._guard_client("", None)
    locks.held["beat_guard:check_hitl_escalations"] = "another-replica"
    assert ct.check_hitl_escalations.run() == {"skipped": True, "reason": "overlap"}
    sweep.assert_not_awaited()


async def test_sweep_task_wires_store_resume_and_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workflow import celery_tasks as ct
    from app.workflow.approval_store import PostgresWorkflowApprovalStore

    seen: dict[str, Any] = {}

    async def sweep(self: HITLWorkflowGateway, **kwargs: Any) -> dict[str, int]:
        seen.update(kwargs, gw=self)
        return {"checked": 0}

    monkeypatch.setattr(HITLWorkflowGateway, "check_and_escalate_overdue", sweep)
    assert await ct.check_hitl_timeouts_async() == {"checked": 0}
    assert seen["limit"] == 200
    gw = seen["gw"]
    assert isinstance(gw._approval_store, PostgresWorkflowApprovalStore)
    assert gw._resume_callback is ct._resume_after_sla_decision
    assert gw._event_redis is not None


async def test_sla_decision_resumes_through_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workflow import celery_tasks as ct

    runner = AsyncMock()
    monkeypatch.setattr(ct, "_get_runner", lambda: runner)
    req = WorkflowHITLRequest(
        tenant_id=T, run_id="run-1", step_id="gate", action_taken="reject",
        reviewed_by=SLA_TIMEOUT_ACTOR, note="timed out",
    )
    await ct._resume_after_sla_decision(req)
    runner.resume_from_hitl.assert_awaited_once_with(
        run_id="run-1", step_id="gate", action="reject", actor_id=SLA_TIMEOUT_ACTOR,
        note="timed out", form_data=None, tenant_id=T,
    )
