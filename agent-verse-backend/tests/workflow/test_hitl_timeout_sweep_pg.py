"""The approval SLA sweep end to end on real Postgres + Redis (testcontainers).

A real ``workflow_approvals`` row past its deadline, on a real ``waiting_hitl``
run, swept by the Celery task ``workflow.check_hitl_escalations`` through the
least-privilege app role (RLS enforced; the cross-tenant scan on the BYPASSRLS
maintenance role, as in production). Asserts the approval, the run (resumed by
the real engine for auto decisions, paused for ``pause``), the audit row and the
HITL trigger event in Redis. A human decision racing the sweep has one winner.

Live finding: tests/real_world/test_mongo_pipeline_failures.py::
test_approval_timeout_never_auto_proceeds. Unit coverage:
tests/workflow/test_hitl_timeout_actions.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflow.hitl_extension import SLA_TIMEOUT_ACTOR, WorkflowHITLRequest

pytestmark = pytest.mark.integration

APP_ROLE = "hitl_sla_app"


@pytest.fixture(scope="module")
def sla_db(pg_url: str) -> Iterator[tuple[str, str]]:
    """An isolated migrated database (the sweep scans every tenant) and an
    RLS-bound app-role DSN for it: (admin_url, app_url)."""
    from app.db.app_role import AppRoleSpec, ensure_app_role
    from tests._test_backends import fresh_migrated_database

    with fresh_migrated_database(pg_url) as admin_url:
        password = secrets.token_urlsafe(18)

        async def _bootstrap() -> None:
            engine = create_async_engine(admin_url)
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(
                        ensure_app_role, AppRoleSpec(role=APP_ROLE, password=password)
                    )
            finally:
                await engine.dispose()

        asyncio.run(_bootstrap())
        app_url = (
            make_url(admin_url)
            .set(username=APP_ROLE, password=password)
            .render_as_string(hide_password=False)
        )
        yield admin_url, app_url


@pytest.fixture
def backends(
    sla_db: tuple[str, str], redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, str, str]]:
    """Point the process (settings, global engines, Celery's REDIS_URL) at the
    containers: app role for tenant work, maintenance role for the scan."""
    from tests._test_backends import reset_db_singletons

    admin_url, app_url = sla_db
    monkeypatch.setenv("DATABASE_URL", app_url)
    monkeypatch.setenv("MAINTENANCE_DATABASE_URL", admin_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    import app.scaling.celery_app as celery_app_mod
    import app.scaling.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "REDIS_URL", redis_url, raising=False)
    monkeypatch.setattr(celery_app_mod, "REDIS_URL", redis_url, raising=False)
    reset_db_singletons()
    yield admin_url, app_url, redis_url
    reset_db_singletons()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _admin(admin_url: str, sql: str, params: dict[str, Any]) -> list[Any]:
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return list(result.mappings().all()) if result.returns_rows else []
    finally:
        await engine.dispose()


def _definition() -> Any:
    from app.workflow.dsl import WorkflowDefinition

    return WorkflowDefinition.from_json(
        {
            "id": "wf-sla",
            "name": "refund-release",
            "steps": [
                {"id": "gate", "type": "hitl", "timeout": "1m"},
                {
                    "id": "release",
                    "type": "set_variable",
                    "depends_on": ["gate"],
                    "var_name": "released",
                    "var_value": "yes",
                },
            ],
        }
    )


class _EngineResume:
    """Stands in for the broker hop: the decision a resume callback dispatches is
    executed by the REAL worker path (``execute_resume_fresh``) on the real run
    store, exactly as ``execute_workflow_run(resume=True, hitl_decision=...)``."""

    def __init__(self, app_url: str) -> None:
        self.app_url = app_url
        self.calls: list[dict[str, Any]] = []

    async def resume_from_hitl(self, **kwargs: Any) -> None:
        from unittest.mock import AsyncMock

        from app.workflow.compiler import WorkflowCompiler
        from app.workflow.context import ContextResolver
        from app.workflow.run_store import PostgresWorkflowRunStore
        from app.workflow.runner import WorkflowRunner

        self.calls.append(kwargs)
        engine = create_async_engine(self.app_url)
        try:
            run_store = PostgresWorkflowRunStore(async_sessionmaker(engine, expire_on_commit=False))
            runner = WorkflowRunner(
                compiler=WorkflowCompiler(context_resolver=ContextResolver()),
                run_store=run_store,
            )
            runner._load_run_definition = AsyncMock(  # type: ignore[method-assign]
                return_value=_definition()
            )
            await runner.execute_resume_fresh(
                kwargs["run_id"],
                "wf-sla",
                kwargs["tenant_id"],
                step_id=kwargs["step_id"],
                action=kwargs["action"],
                actor_id=kwargs["actor_id"],
                note=kwargs["note"],
                form_data=kwargs["form_data"],
            )
        finally:
            await engine.dispose()


def _seed(admin_url: str, app_url: str, *, action: str, overdue: bool = True) -> WorkflowHITLRequest:
    """A waiting_hitl run and its pending approval (deadline 2 min ago)."""
    from app.workflow.approval_store import PostgresWorkflowApprovalStore

    tenant, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    _run(
        _admin(
            admin_url,
            "INSERT INTO workflow_runs (id, tenant_id, status, started_at) "
            "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 'waiting_hitl', NOW())",
            {"id": run_id, "t": tenant},
        )
    )
    created = datetime.now(UTC) - timedelta(minutes=3)
    delta = timedelta(minutes=1) if overdue else timedelta(hours=1)
    req = WorkflowHITLRequest(
        tenant_id=tenant,
        run_id=run_id,
        workflow_id="wf-sla",
        step_id="gate",
        assigned_to="finance-1",
        assignment_strategy="specific_user",
        created_at=created.isoformat(),
        deadline_at=(created + delta).isoformat(),
        timeout_action=action,  # type: ignore[arg-type]
        escalation_to_role="admin",
        actions=[{"id": "approve"}, {"id": "reject"}],
    )

    async def _save() -> None:
        engine = create_async_engine(app_url)
        try:
            await PostgresWorkflowApprovalStore(
                async_sessionmaker(engine, expire_on_commit=False)
            ).save(req)
        finally:
            await engine.dispose()

    _run(_save())
    return req


def _state(admin_url: str, req: WorkflowHITLRequest) -> dict[str, Any]:
    approval = _run(
        _admin(
            admin_url,
            "SELECT status, payload, deadline_at, timeout_handled_at FROM workflow_approvals "
            "WHERE request_id = :r",
            {"r": req.request_id},
        )
    )[0]
    run = _run(
        _admin(
            admin_url,
            "SELECT status, error, run_metadata FROM workflow_runs WHERE id = CAST(:r AS uuid)",
            {"r": req.run_id},
        )
    )[0]
    audit = _run(
        _admin(
            admin_url,
            "SELECT tool_name, outcome, approver, api_key_id, step_id, note FROM audit_log "
            "WHERE goal_id = :r AND tenant_id = :t ORDER BY created_at",
            {"r": req.run_id, "t": uuid.UUID(req.tenant_id).hex},
        )
    )
    payload = approval["payload"]
    meta = run["run_metadata"]
    return {
        "approval_status": approval["status"],
        "approval": json.loads(payload) if isinstance(payload, str) else payload,
        "deadline_at": approval["deadline_at"],
        "handled_at": approval["timeout_handled_at"],
        "run_status": run["status"],
        "run_error": run["error"],
        "run_metadata": (json.loads(meta) if isinstance(meta, str) else meta) or {},
        "audit": [dict(a) for a in audit],
    }


def _sweep(monkeypatch: pytest.MonkeyPatch, app_url: str) -> tuple[dict[str, int], _EngineResume]:
    from app.workflow import celery_tasks as ct

    resume = _EngineResume(app_url)
    monkeypatch.setattr(ct, "_get_runner", lambda: resume)
    return ct.check_hitl_escalations.run(), resume


def _trigger_events(redis_url: str, request_id: str) -> list[tuple[str, dict[str, Any]]]:
    import redis

    from app.core.config import get_settings
    from app.triggers.bus import stream_for_channel

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    out = []
    try:
        streams = {stream_for_channel(c, get_settings()) for c in ("hitl.approved", "hitl.rejected")}
        for stream in sorted(streams):
            for _id, fields in client.xrange(stream):
                data = json.loads(fields["data"])
                if data.get("request_id") == request_id:
                    out.append((fields["channel"], data))
    finally:
        client.close()
    return out


def _sla_audit(state: dict[str, Any], event: str) -> dict[str, Any]:
    rows = [a for a in state["audit"] if a["tool_name"] == f"workflow.approval.timeout_{event}"]
    assert len(rows) == 1, state["audit"]
    assert rows[0]["approver"] == SLA_TIMEOUT_ACTOR and rows[0]["api_key_id"] == SLA_TIMEOUT_ACTOR
    assert rows[0]["step_id"] == "gate"
    return rows[0]


def test_escalate_reassigns_and_the_run_keeps_waiting(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, redis_url = backends
    req = _seed(admin_url, app_url, action="escalate")
    not_due = _seed(admin_url, app_url, action="escalate", overdue=False)

    result, resume = _sweep(monkeypatch, app_url)

    assert result["escalated"] == 1 and result["failed"] == 0
    state = _state(admin_url, req)
    assert state["approval_status"] == "pending"
    approval = state["approval"]
    assert approval["assigned_role"] == "admin" and approval["assigned_to"] is None
    assert approval["escalation_level"] == 1 and approval["escalated_at"]
    assert approval["timeout_outcome"] == "escalated" and state["handled_at"] is not None
    assert state["deadline_at"] is not None
    # Never auto-proceeds: the run still waits for a human, nothing resumed it.
    assert state["run_status"] == "waiting_hitl" and resume.calls == []
    assert state["run_metadata"]["hitl_timeouts"]["gate"]["outcome"] == "escalated"
    assert "to_role=admin" in _sla_audit(state, "escalated")["note"]
    assert _trigger_events(redis_url, req.request_id) == []
    untouched = _state(admin_url, not_due)
    assert untouched["handled_at"] is None and untouched["approval"]["escalation_level"] == 0

    # Exactly once: the next sweep leaves it alone.
    again, _ = _sweep(monkeypatch, app_url)
    assert again["escalated"] == 0
    assert _state(admin_url, req)["approval"]["escalation_level"] == 1


def test_auto_reject_decides_audits_and_the_run_fails_down_the_rejection(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, redis_url = backends
    req = _seed(admin_url, app_url, action="auto_reject")

    result, resume = _sweep(monkeypatch, app_url)

    assert result["auto_rejected"] == 1 and result["failed"] == 0
    state = _state(admin_url, req)
    assert state["approval_status"] == "rejected"
    assert state["approval"]["reviewed_by"] == SLA_TIMEOUT_ACTOR
    assert state["approval"]["action_taken"] == "reject"
    assert [c["action"] for c in resume.calls] == ["reject"]
    # The real engine resumed the run: a rejection with no branch stops it.
    assert state["run_status"] == "failed" and "rejected" in (state["run_error"] or "")
    assert state["run_metadata"]["hitl_timeouts"]["gate"]["outcome"] == "auto_rejected"
    _sla_audit(state, "auto_rejected")
    assert any(a["tool_name"] == "workflow.run.failed" for a in state["audit"])
    events = _trigger_events(redis_url, req.request_id)
    assert [(c, p["approver"]) for c, p in events] == [("hitl.rejected", SLA_TIMEOUT_ACTOR)]

    again, resume_again = _sweep(monkeypatch, app_url)
    assert again["auto_rejected"] == 0 and resume_again.calls == []


def test_auto_approve_decides_and_the_run_completes(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, redis_url = backends
    req = _seed(admin_url, app_url, action="auto_approve")

    result, resume = _sweep(monkeypatch, app_url)

    assert result["auto_approved"] == 1
    state = _state(admin_url, req)
    assert state["approval_status"] == "approved"
    assert state["approval"]["reviewed_by"] == SLA_TIMEOUT_ACTOR
    assert [c["action"] for c in resume.calls] == ["approve"]
    assert state["run_status"] == "complete"
    # Recorded on the run: it proceeded on the author's opt-in, not a reviewer.
    record = state["run_metadata"]["hitl_timeouts"]["gate"]
    assert record["outcome"] == "auto_approved" and record["by"] == SLA_TIMEOUT_ACTOR
    _sla_audit(state, "auto_approved")
    assert [c for c, _ in _trigger_events(redis_url, req.request_id)] == ["hitl.approved"]


def test_pause_pauses_the_run_and_a_later_decision_resumes_it(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, _redis_url = backends
    req = _seed(admin_url, app_url, action="pause")

    result, resume = _sweep(monkeypatch, app_url)

    assert result["paused"] == 1
    state = _state(admin_url, req)
    assert state["approval_status"] == "pending"  # still visible and decidable
    assert state["approval"]["timeout_outcome"] == "paused"
    assert state["run_status"] == "paused" and resume.calls == []
    assert state["run_metadata"]["hitl_timeout_pause"]["step_id"] == "gate"
    _sla_audit(state, "paused")
    assert any(a["tool_name"] == "workflow.run.paused" for a in state["audit"])

    # A human approves the paused gate: the run resumes and completes.
    from app.workflow.approval_store import PostgresWorkflowApprovalStore
    from app.workflow.hitl_extension import HITLWorkflowGateway

    async def _decide() -> None:
        engine = create_async_engine(app_url)
        try:
            gw = HITLWorkflowGateway(
                approval_store=PostgresWorkflowApprovalStore(
                    async_sessionmaker(engine, expire_on_commit=False)
                ),
                resume_callback=lambda r: resume.resume_from_hitl(
                    run_id=r.run_id, step_id=r.step_id, action=r.action_taken,
                    actor_id=r.reviewed_by, note=r.note, form_data=r.form_data,
                    tenant_id=r.tenant_id,
                ),
            )
            await gw.decide(req.request_id, "approve", "finance-1", tenant_id=req.tenant_id)
        finally:
            await engine.dispose()

    _run(_decide())
    after = _state(admin_url, req)
    assert after["approval_status"] == "approved" and after["run_status"] == "complete"


def test_human_decision_racing_the_sweep_has_one_winner(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The human's conditional UPDATE and the sweep's compare-and-set hit the
    same row concurrently; exactly one decision is recorded and audited."""
    from app.workflow.approval_store import PostgresWorkflowApprovalStore
    from app.workflow.hitl_extension import HITLWorkflowGateway

    admin_url, app_url, _redis_url = backends
    winners: list[str] = []
    for _ in range(8):
        req = _seed(admin_url, app_url, action="auto_reject")

        async def _race(req: WorkflowHITLRequest = req) -> None:
            engine = create_async_engine(app_url, pool_size=4)
            try:
                store = PostgresWorkflowApprovalStore(
                    async_sessionmaker(engine, expire_on_commit=False)
                )
                human = dataclasses.replace(
                    req, status="approved", action_taken="approve", reviewed_by="finance-1"
                )
                gw = HITLWorkflowGateway(approval_store=store)
                await asyncio.gather(
                    store.decide_if_pending(human),
                    gw.check_and_escalate_overdue(candidates=[req]),
                )
            finally:
                await engine.dispose()

        _run(_race())
        state = _state(admin_url, req)
        reviewer = state["approval"]["reviewed_by"]
        assert reviewer in ("finance-1", SLA_TIMEOUT_ACTOR)
        assert state["approval_status"] == ("approved" if reviewer == "finance-1" else "rejected")
        sla_rows = [a for a in state["audit"] if a["tool_name"].startswith("workflow.approval.")]
        assert len(sla_rows) == (1 if reviewer == SLA_TIMEOUT_ACTOR else 0)
        assert ("hitl_timeouts" in state["run_metadata"]) == (reviewer == SLA_TIMEOUT_ACTOR)
        winners.append(reviewer)
    assert set(winners) <= {"finance-1", SLA_TIMEOUT_ACTOR}


def test_a_terminal_runs_approval_is_expired_not_decided(
    backends: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_url, app_url, _redis_url = backends
    req = _seed(admin_url, app_url, action="auto_approve")
    _run(
        _admin(
            admin_url,
            "UPDATE workflow_runs SET status = 'cancelled' WHERE id = CAST(:r AS uuid)",
            {"r": req.run_id},
        )
    )
    result, resume = _sweep(monkeypatch, app_url)
    assert result["expired"] == 1 and resume.calls == []
    state = _state(admin_url, req)
    assert state["approval_status"] == "expired" and state["run_status"] == "cancelled"


def test_overdue_scan_uses_the_partial_deadline_index(backends: tuple[str, str, str]) -> None:
    admin_url, _app_url, _redis_url = backends
    rows = _run(
        _admin(
            admin_url,
            "SELECT indexdef FROM pg_indexes WHERE indexname = "
            "'ix_workflow_approvals_pending_deadline'",
            {},
        )
    )
    assert rows and "deadline_at" in rows[0]["indexdef"]
    assert "timeout_handled_at IS NULL" in rows[0]["indexdef"]


def test_migration_backfills_deadlines_and_legacy_escalations(pg_url: str) -> None:
    """f3b5d7e9a1c4 on rows written before it: the deadline comes from the
    payload (else created_at + escalation_after_hours) and an approval the old
    sweep already escalated is marked handled (never escalated twice)."""
    import os
    import subprocess
    import sys

    from tests._test_backends import BACKEND_ROOT, fresh_migrated_database

    def alembic(url: str, *args: str) -> None:
        env = {**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"}
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_ROOT, env=env, check=True, capture_output=True, text=True,
        )

    with fresh_migrated_database(pg_url) as url:
        alembic(url, "downgrade", "e1f3a5c7b9d2")
        tenant = str(uuid.uuid4())
        rows = {
            "with-deadline": {"deadline_at": "2026-10-07T10:00:00+00:00", "discussion": []},
            "age-based": {"deadline_at": None, "escalation_after_hours": 2, "discussion": []},
            "legacy-escalated": {
                "deadline_at": "2026-10-01T00:00:00+00:00",
                "discussion": [{"type": "escalation", "by": "system:sla"}],
            },
        }
        for rid, payload in rows.items():
            _run(
                _admin(
                    url,
                    "INSERT INTO workflow_approvals (request_id, tenant_id, run_id, step_id, "
                    " status, payload, created_at) VALUES (:r, CAST(:t AS uuid), 'run', 'g', "
                    " 'pending', CAST(:p AS jsonb), '2026-10-07T08:00:00+00:00')",
                    {"r": rid, "t": tenant, "p": json.dumps(payload)},
                )
            )
        alembic(url, "upgrade", "head")
        got = {
            r["request_id"]: r
            for r in _run(
                _admin(
                    url,
                    "SELECT request_id, deadline_at, timeout_handled_at FROM workflow_approvals",
                    {},
                )
            )
        }
        assert got["with-deadline"]["deadline_at"] == datetime(2026, 10, 7, 10, tzinfo=UTC)
        assert got["age-based"]["deadline_at"] == datetime(2026, 10, 7, 10, tzinfo=UTC)
        assert got["with-deadline"]["timeout_handled_at"] is None
        assert got["legacy-escalated"]["timeout_handled_at"] is not None
