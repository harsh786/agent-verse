"""P1e-1: agent_generated Sources index what the platform produced (real Postgres).

The connector used to be push-only with nothing pushing to it: a manual sync
failed with "push-only" and no goal output, approval decision, workflow result
or lesson ever reached a knowledge collection. It now reads them from the
platform tables (keyset pages, a JSON cursor per stream) under the tenant's RLS
context, as the least-privilege app role.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/connectors/test_agent_generated_connector_integration.py \\
        -m integration
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ingestion.base_connector import stable_doc_id
from app.ingestion.connectors.agent_generated_connector import AgentGeneratedConnector
from app.ingestion.source_config import SourceConfig, SourceFamily
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _config(tid: str, **cfg: Any) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        tenant_id=tid,
        name="agent knowledge",
        family=SourceFamily.AGENT_GENERATED,
        source_type="agent_generated",
        collection_id="col-1",
        connection_config=cfg,
    )


async def _goal(
    pg: str, tid: str, gid: str, text: str, answer: str | None, *, at: datetime,
    status: str = "complete", dry_run: bool = False, parent: str | None = None,
    answer_in: str = "goal_complete",
) -> None:
    await admin_exec(
        pg,
        "INSERT INTO goals (id, tenant_id, goal_text, status, dry_run, completed_at, "
        "parent_goal_id) VALUES (:id, :t, :g, :s, :d, :c, :p)",
        {"id": gid, "t": tid, "g": text, "s": status, "d": dry_run,
         "c": at if status == "complete" else None, "p": parent},
    )
    events: list[tuple[str, dict[str, Any]]] = [("goal_started", {"type": "goal_started"})]
    if answer is not None and answer_in == "step_complete":
        events.append(("step_complete", {"type": "step_complete", "output": "draft"}))
        events.append(("step_complete", {"type": "step_complete", "output": answer}))
        events.append(("goal_complete", {"type": "goal_complete"}))
    elif answer is not None:
        events.append(("step_complete", {"type": "step_complete", "output": "intermediate"}))
        events.append(("goal_complete", {"type": "goal_complete", "answer": answer}))
    for seq, (etype, payload) in enumerate(events, start=1):
        await admin_exec(
            pg,
            "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, payload, "
            "created_at) VALUES (:id, :t, :g, :s, :e, CAST(:p AS json), :c)",
            {"id": uuid.uuid4().hex, "t": tid, "g": gid, "s": seq, "e": etype,
             "p": json.dumps(payload), "c": at},
        )


async def _approval(
    pg: str, tid: str, aid: str, gid: str, action: str, status: str, *, at: datetime,
    approver: str = "ops-lead", note: str = "",
) -> None:
    await admin_exec(
        pg,
        "INSERT INTO approval_requests (id, tenant_id, goal_id, action, risk_level, status, "
        "approver, note, resolved_at) VALUES (:id, :t, :g, :a, 'high', :s, :ap, :n, :r)",
        {"id": aid, "t": tid, "g": gid, "a": action, "s": status, "ap": approver, "n": note,
         "r": at if status != "pending" else None},
    )


async def _workflow(pg: str, tid: str, name: str) -> str:
    wid = str(uuid.uuid4())
    await admin_exec(
        pg,
        "INSERT INTO workflow_definitions (id, tenant_id, name, slug) "
        "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :n, :s)",
        {"id": wid, "t": tid, "n": name, "s": f"{name}-{wid[:6]}"},
    )
    return wid


async def _run(
    pg: str, tid: str, wid: str, outputs: Any, *, at: datetime, status: str = "complete",
    test_run: bool = False,
) -> str:
    rid = str(uuid.uuid4())
    await admin_exec(
        pg,
        "INSERT INTO workflow_runs (id, tenant_id, workflow_id, status, inputs, outputs, "
        "completed_at, is_test_run) VALUES (CAST(:id AS uuid), CAST(:t AS uuid), "
        "CAST(:w AS uuid), :s, CAST(:i AS jsonb), CAST(:o AS jsonb), :c, :tr)",
        {"id": rid, "t": tid, "w": wid, "s": status, "i": json.dumps({"week": "2026-W40"}),
         "o": json.dumps(outputs), "c": at if status == "complete" else None, "tr": test_run},
    )
    return rid


async def _wf_approval(
    pg: str, tid: str, run_id: str, wid: str, status: str, *, at: datetime, note: str
) -> str:
    rid = uuid.uuid4().hex
    payload = {
        "note": note, "status": status, "action_taken": "approve" if status == "approved"
        else "reject", "step_name": "Engineering manager sign-off",
        "workflow_name": "weekly-report", "reviewed_by": "manager-7",
        "reviewed_at": at.isoformat(),
        "context": [{"label": "Report title", "value": "Payments Platform W40"}],
    }
    await admin_exec(
        pg,
        "INSERT INTO workflow_approvals (request_id, tenant_id, run_id, workflow_id, step_id, "
        "status, payload, updated_at) VALUES (:r, CAST(:t AS uuid), :run, :w, 'manager', :s, "
        "CAST(:p AS jsonb), :u)",
        {"r": rid, "t": tid, "run": run_id, "w": wid, "s": status, "p": json.dumps(payload),
         "u": at},
    )
    return rid


async def _lesson(
    pg: str, tid: str, text: str, *, at: datetime, state: str = "active",
    classification: str = "internal", goal_id: str = "g-src",
) -> str:
    mid = uuid.uuid4().hex
    await admin_exec(
        pg,
        "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, safe_summary, "
        "source_goal_id, source_execution_id, classification, confidence, lifecycle_state, "
        "embedding_model, retention_policy_id, idempotency_key, updated_at, created_at, "
        "sealed_content) VALUES (:id, :t, 'reflexion', :ref, :s, :g, 'exec', :c, 80, :l, 'm', "
        "'reflexion-standard', :k, :u, :u, :sealed)",
        {"id": mid, "k": mid, "t": tid, "ref": f"memory://{mid}", "s": text, "g": goal_id,
         "c": classification, "l": state, "u": at,
         "sealed": "sealed" if classification in ("confidential", "restricted") else None},
    )
    return mid


async def _tenant(pg: str) -> str:
    tid = uuid.uuid4().hex
    await seed_tenant(pg, tid)
    return tid


async def _drain(connector: AgentGeneratedConnector, config: SourceConfig,
                 cursor: str | None) -> tuple[list[Any], str | None]:
    docs = []
    last = cursor
    async for doc, nxt in connector.get_delta(config, cursor):
        docs.append(doc)
        last = nxt
    return docs, connector.completed_cursor or last


async def test_goal_outputs_decisions_runs_and_lessons_are_read(pg_url: str) -> None:
    tid = await _tenant(pg_url)
    other = await _tenant(pg_url)
    gid = uuid.uuid4().hex
    await _goal(pg_url, tid, gid, "Summarise the Pune cold-room calibration policy",
                "Calibrate every 45 days; escalate to the cold-chain lead.", at=T0)
    step_gid = uuid.uuid4().hex
    await _goal(pg_url, tid, step_gid, "Draft the Q3 freight memo", "Freight is 9,800 INR per box.",
                at=T0 + timedelta(minutes=1), answer_in="step_complete")
    # Never indexed: failed, dry run, subgoal, no answer.
    await _goal(pg_url, tid, uuid.uuid4().hex, "failed goal", "x", at=T0, status="failed")
    await _goal(pg_url, tid, uuid.uuid4().hex, "dry goal", "dry answer", at=T0, dry_run=True)
    await _goal(pg_url, tid, uuid.uuid4().hex, "sub goal", "sub answer", at=T0, parent=gid)
    await _goal(pg_url, tid, uuid.uuid4().hex, "silent goal", None, at=T0)
    # Another tenant's goal and approval are never read.
    await _goal(pg_url, other, uuid.uuid4().hex, "tenant B secret", "B-only answer", at=T0)
    await _approval(pg_url, other, uuid.uuid4().hex, "", "B action", "approved", at=T0)

    aid = uuid.uuid4().hex
    await _approval(pg_url, tid, aid, gid, "Delete order ORD-1153 in prod", "approved",
                    at=T0 + timedelta(minutes=2), note="Verified cancelled test order")
    await _approval(pg_url, tid, uuid.uuid4().hex, gid, "pending action", "pending", at=T0)
    await _approval(pg_url, tid, uuid.uuid4().hex, gid, "expired action", "expired", at=T0)

    wid = await _workflow(pg_url, tid, "weekly-report")
    run_id = await _run(pg_url, tid, wid, {"report": {"summary": "UPI autopay retries shipped"}},
                        at=T0 + timedelta(minutes=3))
    await _run(pg_url, tid, wid, {"report": "test"}, at=T0, test_run=True)
    await _run(pg_url, tid, wid, {"x": 1}, at=T0, status="failed")
    wf_aid = await _wf_approval(pg_url, tid, run_id, wid, "approved",
                                at=T0 + timedelta(minutes=4), note="Ship it")
    lesson = await _lesson(pg_url, tid, "Check deletedCount before reporting a delete.",
                           at=T0 + timedelta(minutes=5), goal_id=gid)
    await _lesson(pg_url, tid, "quarantined lesson", at=T0, state="quarantined")
    await _lesson(pg_url, tid, "secret lesson", at=T0, classification="restricted")

    engine = await app_engine(pg_url)
    try:
        connector = AgentGeneratedConnector(db_factory=sessions(engine))
        config = _config(tid, source_types=[
            "goal_output", "hitl_decision", "workflow_output", "learning"])
        docs, cursor = await _drain(connector, config, None)
        by_url = {d.source_url: d for d in docs}
        assert set(by_url) == {
            f"agentverse://goals/{gid}",
            f"agentverse://goals/{step_gid}",
            f"agentverse://approvals/{aid}",
            f"agentverse://workflow-approvals/{wf_aid}",
            f"agentverse://workflow-runs/{run_id}",
            f"agentverse://memories/{lesson}",
        }
        goal_doc = by_url[f"agentverse://goals/{gid}"]
        text = goal_doc.content.decode()
        assert "Calibrate every 45 days" in text and "Pune cold-room" in text
        assert "intermediate" not in text  # the terminal answer, not a step output
        assert goal_doc.doc_id == stable_doc_id(config, "goal_output", gid)
        assert goal_doc.metadata["origin"] == {"kind": "goal_output", "goal_id": gid}
        assert goal_doc.tenant_id == tid and goal_doc.content_type == "text/markdown"
        assert "9,800 INR per box" in by_url[f"agentverse://goals/{step_gid}"].content.decode()
        decision = by_url[f"agentverse://approvals/{aid}"]
        dtext = decision.content.decode()
        assert "APPROVED" in dtext and "ops-lead" in dtext and "ORD-1153" in dtext
        assert "Verified cancelled test order" in dtext and gid in dtext
        assert decision.metadata["origin"]["goal_id"] == gid
        assert decision.metadata["origin"]["approval_id"] == aid
        wf_text = by_url[f"agentverse://workflow-approvals/{wf_aid}"].content.decode()
        assert "Engineering manager sign-off" in wf_text and "Ship it" in wf_text
        assert run_id in wf_text
        run_text = by_url[f"agentverse://workflow-runs/{run_id}"].content.decode()
        assert "UPI autopay retries shipped" in run_text and "weekly-report" in run_text
        assert by_url[f"agentverse://workflow-runs/{run_id}"].metadata["origin"] == {
            "kind": "workflow_output", "workflow_run_id": run_id, "workflow_id": wid}
        assert "deletedCount" in by_url[f"agentverse://memories/{lesson}"].content.decode()
        assert all("tenant B" not in d.content.decode() for d in docs)

        # Re-run from the cursor: nothing new beyond the overlap window's rows.
        again, cursor2 = await _drain(connector, config, cursor)
        assert {d.doc_id for d in again} <= {d.doc_id for d in docs}
        # New records after the cursor are read; the old ones are not re-read
        # once they are older than the overlap window.
        late = T0 + timedelta(hours=1)
        new_gid = uuid.uuid4().hex
        await _goal(pg_url, tid, new_gid, "Late goal", "Late answer 77", at=late)
        third, cursor3 = await _drain(connector, config, cursor2)
        assert f"agentverse://goals/{new_gid}" in {d.source_url for d in third}
        assert {d.doc_id for d in third} - {d.doc_id for d in docs} == {
            stable_doc_id(config, "goal_output", new_gid)}
        fourth, _ = await _drain(connector, config, cursor3)
        assert [d.source_url for d in fourth if "goals/" in d.source_url] == [
            f"agentverse://goals/{new_gid}"]  # only the overlap window is read again

        # Live listing = what is still eligible; a deleted goal drops out.
        live = {doc_id async for doc_id in connector.iter_live_doc_ids(config)}
        assert stable_doc_id(config, "goal_output", gid) in live
        await admin_exec(pg_url, "DELETE FROM goals WHERE id = :g", {"g": gid})
        live = {doc_id async for doc_id in connector.iter_live_doc_ids(config)}
        assert stable_doc_id(config, "goal_output", gid) not in live
        assert stable_doc_id(config, "goal_output", new_gid) in live
        assert stable_doc_id(config, "hitl_decision", aid) in live
    finally:
        await engine.dispose()


async def test_kinds_filters_and_per_sync_bound(pg_url: str) -> None:
    tid = await _tenant(pg_url)
    for i in range(7):
        await _goal(pg_url, tid, f"g{i:02d}-{uuid.uuid4().hex[:6]}", f"goal {i}", f"answer {i}",
                    at=T0 + timedelta(seconds=i * 300))
    await _approval(pg_url, tid, uuid.uuid4().hex, "", "an action", "rejected", at=T0)
    engine = await app_engine(pg_url)
    try:
        connector = AgentGeneratedConnector(db_factory=sessions(engine))
        only_goals = _config(tid, source_types=["goal_output"], max_items_per_sync=3)
        first, cursor = await _drain(connector, only_goals, None)
        assert len(first) == 3 and connector.truncated is True
        assert all(d.source_url.startswith("agentverse://goals/") for d in first)
        second, cursor = await _drain(connector, only_goals, cursor)
        # The next sync continues where the bounded one stopped (plus the overlap).
        assert {d.doc_id for d in second} - {d.doc_id for d in first}
        seen = {d.doc_id for d in first} | {d.doc_id for d in second}
        third, _ = await _drain(connector, only_goals, cursor)
        seen |= {d.doc_id for d in third}
        assert len(seen) == 7
        decisions = _config(tid, source_types=["hitl_decision"])
        docs, _ = await _drain(connector, decisions, None)
        assert len(docs) == 1 and "REJECTED" in docs[0].content.decode()
    finally:
        await engine.dispose()


async def test_eval_score_floor(pg_url: str) -> None:
    tid = await _tenant(pg_url)
    good, bad, unscored = (uuid.uuid4().hex for _ in range(3))
    for gid in (good, bad, unscored):
        await _goal(pg_url, tid, gid, f"goal {gid[:4]}", f"answer {gid}", at=T0)
    for gid, score in ((good, 0.92), (bad, 0.31)):
        await admin_exec(
            pg_url,
            "INSERT INTO eval_scorecards (id, goal_id, tenant_id, overall_score, "
            "strategy_execution_id) VALUES (:id, :g, :t, :s, :x)",
            {"id": uuid.uuid4().hex, "g": gid, "t": tid, "s": score, "x": gid},
        )
    engine = await app_engine(pg_url)
    try:
        connector = AgentGeneratedConnector(db_factory=sessions(engine))
        docs, _ = await _drain(connector, _config(tid, min_eval_score=0.7), None)
        urls = {d.source_url for d in docs}
        assert urls == {f"agentverse://goals/{good}", f"agentverse://goals/{unscored}"}
        assert next(d for d in docs if good in d.source_url).metadata["origin"][
            "eval_score"] == "0.92"
        strict = _config(tid, min_eval_score=0.7, require_eval_score=True)
        docs, _ = await _drain(connector, strict, None)
        assert {d.source_url for d in docs} == {f"agentverse://goals/{good}"}
    finally:
        await engine.dispose()


async def test_agent_filter_and_since(pg_url: str) -> None:
    tid = await _tenant(pg_url)
    agent_a, agent_b = (f"agent-{uuid.uuid4().hex[:8]}" for _ in range(2))
    for agent in (agent_a, agent_b):
        await admin_exec(pg_url, "INSERT INTO agents (id, tenant_id, name) VALUES (:a, :t, :a)",
                         {"a": agent, "t": tid})
    goals = {}
    for i, agent in enumerate((agent_a, agent_b, agent_a)):
        gid = uuid.uuid4().hex
        goals[gid] = agent
        await _goal(pg_url, tid, gid, f"goal {i}", f"answer {i}", at=T0 + timedelta(hours=i))
        await admin_exec(pg_url, "UPDATE goals SET agent_id = :a WHERE id = :g",
                         {"a": agent, "g": gid})
        await _approval(pg_url, tid, uuid.uuid4().hex, gid, f"action {i}", "approved",
                        at=T0 + timedelta(hours=i))
    engine = await app_engine(pg_url)
    try:
        connector = AgentGeneratedConnector(db_factory=sessions(engine))
        cfg = _config(tid, agent_ids=[agent_a])
        docs, _ = await _drain(connector, cfg, None)
        assert len(docs) == 4  # 2 goals + 2 decisions of agent A
        assert all(goals[d.metadata["origin"]["goal_id"]] == agent_a for d in docs)
        live = {x async for x in connector.iter_live_doc_ids(cfg)}
        assert live == {d.doc_id for d in docs}
        since = _config(tid, since=(T0 + timedelta(minutes=90)).isoformat())
        docs, _ = await _drain(connector, since, None)
        assert sorted(d.content.decode().count("answer 2") for d in docs if
                      "goals/" in d.source_url) == [1]
        assert len(docs) == 2  # goal 2 + its decision, both after "since"
    finally:
        await engine.dispose()


async def test_run_step_results_and_producer_scoping(pg_url: str) -> None:
    """A run without declared outputs is indexed from its step results; a Source
    scoped to workflows reads no goals and one scoped to agents reads no runs."""
    tid = await _tenant(pg_url)
    wid = await _workflow(pg_url, tid, "vendor-onboarding")
    run_id = await _run(pg_url, tid, wid, {}, at=T0)
    for step_id, name, stype, output, minute in (
        ("finance_signoff", "Finance sign-off", "hitl", {"action": "approve"}, 1),
        ("onboarding_record", "Onboarding record", "code", {"status": "onboarded",
                                                             "credit_limit_inr": "450000"}, 2),
        ("onboarding_record", "Onboarding record", "code", {"status": "draft"}, 0),
    ):
        await admin_exec(
            pg_url,
            "INSERT INTO workflow_step_results (run_id, tenant_id, step_id, step_type, "
            "step_name, status, output, completed_at) VALUES (CAST(:r AS uuid), "
            "CAST(:t AS uuid), :s, :ty, :n, 'complete', CAST(:o AS jsonb), :c)",
            {"r": run_id, "t": tid, "s": step_id, "ty": stype, "n": name,
             "o": json.dumps(output), "c": T0 + timedelta(minutes=minute)},
        )
    await _goal(pg_url, tid, uuid.uuid4().hex, "a goal", "an answer", at=T0)
    engine = await app_engine(pg_url)
    try:
        connector = AgentGeneratedConnector(db_factory=sessions(engine))
        wf_only = _config(tid, source_types=["goal_output", "workflow_output"],
                          workflow_ids=[wid])
        docs, _ = await _drain(connector, wf_only, None)
        assert [d.source_url for d in docs] == [f"agentverse://workflow-runs/{run_id}"]
        text = docs[0].content.decode()
        assert "Onboarding record (code)" in text and "450000" in text
        assert '"draft"' not in text  # only the latest attempt of a step
        assert text.index("Finance sign-off") < text.index("Onboarding record")
        agents_only = _config(tid, source_types=["goal_output", "workflow_output"],
                              agent_ids=["agent-x"])
        docs, _ = await _drain(connector, agents_only, None)
        assert docs == []  # no goal of agent-x, and runs are out of scope
        unscoped = _config(tid, source_types=["goal_output", "workflow_output"])
        docs, _ = await _drain(connector, unscoped, None)
        assert len(docs) == 2
    finally:
        await engine.dispose()
