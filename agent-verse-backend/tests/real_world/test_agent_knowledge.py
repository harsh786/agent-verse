"""AGK-*: knowledge the platform itself produces (A12, P1e) on the live stack.

An ``agent_generated`` Source indexes into its collection the final answers of
completed goals, the human decisions on approvals (goal approvals and workflow
gates), the outputs of completed workflow runs and active reflexion lessons. The
scenarios drive real goals (configured LLM), a real supervised-agent approval and
a real workflow gate, then assert what a client sees:

* AGK-GOAL-OUTPUT     — two goals' answers become searchable within seconds of
  completing (event-triggered sync), cited ``agentverse://goals/<id>`` with the
  goal id in the citation's origin; a generated e-mail / mobile number never
  reaches the index (secrets: AGK-WORKFLOW — a goal asked to repeat a key is
  refused as high-risk in bounded-autonomous mode, by design); a re-sync indexes nothing again; a later goal is added
  incrementally; tenant B sees none of it.
* AGK-APPROVAL        — an approved and a rejected gate of a supervised agent
  become decision documents (decision, reviewer, note, goal id); a pending
  approval is never indexed; the reviewer's note is PII-screened.
* AGK-WORKFLOW        — a workflow gate's decision and the run's outputs become
  documents citing the approval / run id; secrets in the outputs are redacted;
  a disabled Source indexes nothing.
* AGK-GOVERNANCE      — a collection under legal hold: a reindex is refused (409)
  and a re-sync replaces nothing; the subject's erasure deletes the goal but
  keeps the held knowledge (reported); after release, reconciliation removes it;
  without a hold the erasure removes the goal's knowledge at once.
* AGK-FAILURES        — invalid configurations are refused on save (422) with the
  reason; a Source without a collection cannot sync; an empty first sync is an
  honest ``completed`` with 0 documents (it used to fail "push-only").

Needs: the configured LLM (goals), ``RW_SECOND_TENANT_FILE`` (isolation),
``RW_PG_CONTAINER`` / ``RW_REDIS_CONTAINER`` (tag a goal with a data principal and
release a legal hold: the API has neither route), an admin key for the erasure.
"""

from __future__ import annotations

import json
import os
import random
import string
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.real_world import goals as gx
from tests.real_world import kb as kbx
from tests.real_world import source_jobs as jobs
from tests.real_world import workflows as wfx
from tests.real_world.helpers import (
    LiveAPI,
    body_of,
    mask,
    register_secret,
    tag,
    wait_until,
)

DOC_TIMEOUT = float(os.getenv("RW_AGK_DOC_TIMEOUT", "180"))
FAMILY = "agent_generated"


# ── helpers ──────────────────────────────────────────────────────────────────


def _docker(container_env: str, default: str, *cmd: str) -> str:
    container = os.getenv(container_env, default)
    out = subprocess.run(["docker", "exec", container, *cmd], capture_output=True, text=True,
                         timeout=60, check=True)
    return out.stdout.strip()


def _psql(sql: str) -> str:
    return _docker("RW_PG_CONTAINER", "agentverse-backend-postgres-1", "psql", "-U",
                   "agentverse", "-d", "agentverse", "-Atc", sql)


def _hold(api: LiveAPI, cid: str, name: str) -> str:
    resp = api.post("/governance/legal-hold", json={
        "reason": f"P1e {name}", "name": f"rw-{name}-{tag()}", "resource_type": "collection",
        "resource_ids": [cid]})
    assert resp.status_code == 200, f"legal hold -> {resp.status_code}: {mask(resp.text)[:200]}"
    return str(resp.json()["id"])


def _release_hold(hold_id: str, tenant_id: str) -> None:
    _psql(f"UPDATE legal_holds SET status='released', released_at=now() WHERE id = '{hold_id}'")
    _docker("RW_REDIS_CONTAINER", "agentverse-backend-redis-1", "redis-cli", "DEL",
            f"legal_hold:{tenant_id}")


def _rand(n: int, alphabet: str = string.ascii_uppercase + string.digits) -> str:
    return "".join(random.choice(alphabet) for _ in range(n))


def _secrets() -> dict[str, str]:
    """Generated at run time (never a literal in the repository)."""
    values = {
        "aws_key": "AKIA" + _rand(16),
        "password": _rand(6, string.ascii_lowercase) + _rand(6, string.digits),
        "email": f"coldchain.{_rand(6, string.ascii_lowercase)}@example.com",
    }
    for value in values.values():
        register_secret(value)
    return values


def _collection(api: LiveAPI, cleanup: Any, prefix: str) -> str:
    body = api.json_ok("POST", "/knowledge/collections",
                       json={"name": f"{prefix}-{tag()}", "embedder_type": "default"})
    cid = str(body.get("collection_id") or body.get("id"))
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    return cid


def _source(api: LiveAPI, cleanup: Any, cid: str, config: dict[str, Any], *,
            expect: int = 201, **extra: Any) -> dict[str, Any]:
    """An agent_generated Source with the platform defaults (PII redaction on)."""
    body = {"name": f"rw-agk-{tag()}", "family": FAMILY, "source_type": "agent_generated",
            "connection_config": config, "collection_id": cid, "sync_mode": "streaming",
            "sync_interval_seconds": 86400, **extra}
    resp = api.post("/sources", json=body)
    assert resp.status_code == expect, (
        f"POST /sources -> {resp.status_code} (expected {expect}): {mask(resp.text[:400])}")
    out = dict(resp.json()) if resp.content else {}
    if resp.status_code < 300:
        sid = str(out.get("source_id") or out.get("id"))
        cleanup("DELETE", f"/sources/{sid}")
        out["id"] = sid
    out["_http"] = resp.status_code
    return out


def _docs(api: LiveAPI, cid: str) -> list[dict[str, Any]]:
    body = api.json_ok("GET", f"/knowledge/collections/{cid}/documents",
                       params={"limit": 100})
    return list(body.get("documents", []) if isinstance(body, dict) else body)


def _doc_url(doc: dict[str, Any]) -> str:
    return str(doc.get("source_url") or doc.get("url") or (doc.get("metadata") or {}).get(
        "source_url") or "")


def _wait_docs(api: LiveAPI, cid: str, urls: set[str], timeout: float = DOC_TIMEOUT
               ) -> tuple[list[dict[str, Any]], float]:
    """Until every URL in ``urls`` is a document of the collection; (docs, seconds)."""
    started = time.monotonic()
    docs = wait_until(lambda: _docs(api, cid), timeout=timeout, interval=3,
                      desc=f"documents {sorted(urls)} in collection {cid}",
                      done=lambda ds: urls <= {_doc_url(d) for d in ds})
    return list(docs), round(time.monotonic() - started, 1)


def _search(api: LiveAPI, cid: str, q: str, top_k: int = 5) -> list[dict[str, Any]]:
    body = api.json_ok("GET", "/knowledge/search",
                       params={"q": q, "collection_id": cid, "top_k": top_k})
    return list(body if isinstance(body, list) else body.get("results", []))


def _hit_for(hits: list[dict[str, Any]], url: str) -> tuple[int, dict[str, Any] | None]:
    for rank, hit in enumerate(hits, start=1):
        if hit.get("source_url") == url:
            return rank, hit
    return 0, None


def _all_text(api: LiveAPI, cid: str, queries: list[str]) -> str:
    """Every chunk text reachable by these searches (the redaction probe)."""
    seen: dict[str, str] = {}
    for q in queries:
        for hit in _search(api, cid, q, top_k=10):
            seen[str(hit.get("chunk_id"))] = str(hit.get("content") or "")
    return "\n".join(seen.values())


def _cite_url(citation: dict[str, Any]) -> str:
    meta = citation.get("metadata") or {}
    return str(citation.get("source_url") or meta.get("source_url") or "")


def _event_jobs(api: LiveAPI, sid: str) -> list[dict[str, Any]]:
    return [j for j in jobs.jobs(api, sid) if j.get("triggered_by") == "event"]


def _submit(api: LiveAPI, cleanup: Any, goal: str, agent_id: str) -> str:
    return str(gx.submit(api, cleanup, goal, agent_id=agent_id)["goal_id"])


def _complete(api: LiveAPI, goal_id: str) -> dict[str, Any]:
    goal = gx.wait_terminal(api, goal_id)
    assert goal.get("status") == "complete", (
        f"goal {goal_id} ended {goal.get('status')}: {mask(goal.get('failure_reason'))}")
    return goal


# ── AGK-GOAL-OUTPUT ──────────────────────────────────────────────────────────


@pytest.mark.scenario("AGK-GOAL-OUTPUT")
def test_goal_outputs_become_cited_knowledge(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
        second_tenant_api: LiveAPI | None) -> None:
    soft: list[str] = []
    secret = _secrets()
    days, rate = random.randint(31, 59), random.randint(4100, 9800)
    code = f"DD-{_rand(5)}"
    agent = gx.create_agent(api, cleanup, "rw-agk-writer", system_prompt=(
        "You write short internal notes. Use only the facts in the request, verbatim; "
        "never call tools."))
    cid = _collection(api, cleanup, "rw-agk-goals")
    src = _source(api, cleanup, cid, {
        "source_types": ["goal_output", "hitl_decision", "learning"], "agent_ids": [agent]})
    sid = src["id"]
    evidence["source"] = {k: src.get(k) for k in ("pii_action", "min_quality_score",
                                                  "sync_mode")}

    first = jobs.sync(api, sid, timeout=180)
    evidence["empty_sync"] = first
    assert str(first["status"]).lower() == "completed", f"empty first sync: {first}"
    assert first["docs_failed"] == 0 and first["docs_indexed"] == 0, first

    mobile = "+91 9" + _rand(9, string.digits)
    register_secret(mobile)
    personal = {"email": secret["email"], "mobile": mobile}
    g1 = _submit(api, cleanup, (
        "Without using any tools, write a three-sentence internal note for the Pune "
        f"warehouse team stating exactly: the cold-room probe calibration interval is {days} "
        f"days; the duty supervisor is reachable at {secret['email']} or {mobile}; logs are "
        "reviewed every Monday."), agent)
    g2 = _submit(api, cleanup, (
        "Without using any tools, state in two sentences that dock door "
        f"{code} at the Hosur yard handles reefer containers only and takes at most {rate} kg "
        "per pallet from 1 November."),
        agent)
    evidence["goals"] = [g1, g2]
    done_at = {}
    for gid in (g1, g2):
        _complete(api, gid)
        done_at[gid] = time.monotonic()
    urls = {f"agentverse://goals/{g1}", f"agentverse://goals/{g2}"}
    docs, waited = _wait_docs(api, cid, urls)
    evidence["seconds_after_last_goal"] = waited
    goal_docs = [d for d in docs if _doc_url(d).startswith("agentverse://goals/")]
    evidence["documents"] = sorted(_doc_url(d) for d in docs)
    assert {_doc_url(d) for d in goal_docs} == urls, evidence["documents"]
    if waited > 90:
        soft.append(f"goal outputs became searchable {waited}s after completion (> 90 s)")
    ev_jobs = _event_jobs(api, sid)
    evidence["event_jobs"] = [jobs.mask_job({k: j.get(k) for k in jobs.JOB_FIELDS})
                              for j in ev_jobs[:3]]
    assert ev_jobs, "no event-triggered sync ran (the documents came from elsewhere?)"
    assert all(int(j.get("docs_failed") or 0) == 0 for j in ev_jobs), evidence["event_jobs"]

    hits = _search(api, cid, f"How often is the Pune cold-room probe calibrated? {days} days")
    rank, hit = _hit_for(hits, f"agentverse://goals/{g1}")
    evidence["calibration_rank"] = rank
    assert hit is not None, f"g1 not found: {[h.get('source_url') for h in hits]}"
    assert str(days) in str(hit.get("content")), mask(hit.get("content"))[:300]
    assert (hit.get("origin") or {}).get("goal_id") == g1, hit.get("origin")
    assert (hit.get("origin") or {}).get("kind") == "goal_output", hit.get("origin")
    rank2, hit2 = _hit_for(_search(api, cid, f"dock door {code} pallet weight limit"),
                           f"agentverse://goals/{g2}")
    evidence["surcharge_rank"] = rank2
    assert hit2 is not None and str(rate) in str(hit2.get("content")).replace(",", ""), hit2

    text = _all_text(api, cid, ["duty supervisor contact e-mail mobile",
                                f"calibration interval {days} days", secret["email"]])
    leaked = [k for k, v in personal.items() if v in text or v.replace(" ", "") in text]
    evidence["leaked"] = leaked
    assert not leaked, f"generated {leaked} reached the index"

    status, rbody, ms = kbx.rag_query(api, cid, f"What is the pallet weight limit at dock door {code}?")
    cites = [_cite_url(c) for c in rbody.get("citations") or []]
    evidence["rag"] = {"http": status, "ms": round(ms), "citations": cites,
                       "answer": mask(rbody.get("answer"))[:300]}
    if status != 200:
        soft.append(f"RAG query -> {status}")
    else:
        if f"agentverse://goals/{g2}" not in cites:
            soft.append(f"RAG answer does not cite goal {g2}: {cites}")
        if str(rate) not in str(rbody.get("answer")).replace(",", ""):
            soft.append(f"RAG answer misses {rate} kg: {mask(rbody.get('answer'))[:200]}")

    lessons = [_doc_url(d) for d in _docs(api, cid)
               if _doc_url(d).startswith("agentverse://memories/")]
    evidence["lesson_documents"] = lessons  # reflexion lessons of the agent's goals
    time.sleep(10)  # let the lesson events' syncs settle before the idempotence check

    # Re-sync: everything is already indexed, nothing changes.
    before = {d.get("document_id") or d.get("id"): d.get("chunk_count") for d in goal_docs}
    again = jobs.sync(api, sid, timeout=180)
    evidence["resync"] = again
    assert again["docs_indexed"] == 0 and again["docs_failed"] == 0, again
    after_docs = [d for d in _docs(api, cid) if _doc_url(d).startswith("agentverse://goals/")]
    assert len(after_docs) == 2, [_doc_url(d) for d in after_docs]

    # Incremental: one more goal is added, the others are untouched.
    g3 = _submit(api, cleanup, (
        "Without using any tools, write one sentence: the Chakan dock opens at 06:30 IST "
        "from Monday."), agent)
    _complete(api, g3)
    docs3, waited3 = _wait_docs(api, cid, urls | {f"agentverse://goals/{g3}"})
    evidence["incremental_seconds"] = waited3
    now = {d.get("document_id") or d.get("id"): d.get("chunk_count") for d in docs3
           if _doc_url(d) in urls}
    assert now == before, f"the earlier documents changed: {before} -> {now}"

    # Tenant isolation.
    if second_tenant_api is None:
        soft.append("RW_SECOND_TENANT_FILE not set: tenant isolation not checked")
    else:
        other = second_tenant_api
        s_search = other.get("/knowledge/search", params={"q": "cold-room calibration",
                                                         "collection_id": cid})
        s_source = other.get(f"/sources/{sid}")
        evidence["tenant_b"] = {"search": s_search.status_code, "source": s_source.status_code}
        assert s_search.status_code in (403, 404) or not body_of(s_search), (
            f"tenant B searched A's collection: {s_search.status_code}")
        assert s_source.status_code == 404, f"tenant B read A's source: {s_source.status_code}"
        cleanup_b = _CleanupFor(other)
        b_cid = _collection(other, cleanup_b, "rw-agk-b")
        try:
            b_src = _source(other, cleanup_b, b_cid, {
                "source_types": ["goal_output", "hitl_decision", "learning"],
                "since": (datetime.now(UTC) - timedelta(minutes=30)).isoformat()})
            b_job = jobs.sync(other, b_src["id"], timeout=300)
            evidence["tenant_b"]["sync"] = b_job
            b_text = _all_text(other, b_cid, [f"calibration {days} days", code,
                                              "Chakan dock 06:30"])
            assert b_job["docs_failed"] == 0, b_job
            assert code not in b_text and "Chakan" not in b_text, "tenant B indexed A's goals"
        finally:
            cleanup_b.run()
    assert not soft, "; ".join(soft)


class _CleanupFor:
    """A tiny cleanup stack for the second tenant's objects."""

    def __init__(self, api: LiveAPI) -> None:
        self.api = api
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str) -> None:
        self.calls.append((method, path))

    def run(self) -> None:
        for method, path in reversed(self.calls):
            try:
                self.api.request(method, path)
            except Exception:  # best-effort cleanup
                pass


# ── AGK-APPROVAL ─────────────────────────────────────────────────────────────

HIGH_RISK_GOAL = (
    "Demo list (in-memory sample data, no external systems or tools needed): "
    "rec-101 env=staging last_used=2025-01-03; rec-102 env=production last_used=2026-09-28; "
    "rec-103 env=staging last_used=2025-02-11. Delete the stale staging records "
    "(env=staging, last_used before 2026) from the demo list and report which IDs were "
    "removed and which remain."
)


def _gate(api: LiveAPI, cleanup: Any, agent: str) -> tuple[str, dict[str, Any]]:
    gid = _submit(api, cleanup, HIGH_RISK_GOAL, agent)
    goal, pending = gx.wait_gate_or_end(api, gid)
    assert pending, f"no approval was requested; goal ended {goal.get('status')}"
    return gid, pending[0]


@pytest.mark.scenario("AGK-APPROVAL")
def test_goal_approval_decisions_become_knowledge(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    soft: list[str] = []
    ticket, freeze = f"CHG-{_rand(6)}", f"FRZ-{_rand(6)}"
    reviewer_mail = f"ops.lead.{_rand(5, string.ascii_lowercase)}@example.com"
    register_secret(reviewer_mail)
    body = api.json_ok("POST", "/agents", json={
        "name": f"rw-agk-supervised-{tag()}", "autonomy_mode": "supervised",
        "system_prompt": "You are a careful data-hygiene operator. Work only on data given "
                         "in the goal; never call external systems.",
        "max_iterations": 8, "timeout_seconds": 420})
    agent = str(body.get("agent_id") or body.get("id"))
    cleanup("DELETE", f"/agents/{agent}")
    cid = _collection(api, cleanup, "rw-agk-decisions")
    sid = _source(api, cleanup, cid, {"source_types": ["hitl_decision"],
                                      "agent_ids": [agent]})["id"]

    approved_goal, gate1 = _gate(api, cleanup, agent)
    rid1 = str(gate1["request_id"])
    evidence["approval_1"] = {"goal": approved_goal, "request": rid1}
    time.sleep(8)
    assert not _docs(api, cid), "a pending approval was indexed"
    resp = api.post(f"/governance/approvals/{rid1}/approve", json={
        "approver": "rw-ops-lead",
        "note": f"Change ticket {ticket} approved; questions to {reviewer_mail}."})
    assert resp.status_code == 200, f"approve -> {resp.status_code}: {mask(resp.text)[:300]}"
    docs, waited = _wait_docs(api, cid, {f"agentverse://approvals/{rid1}"})
    evidence["approve_doc_seconds"] = waited

    rejected_goal, gate2 = _gate(api, cleanup, agent)
    rid2 = str(gate2["request_id"])
    evidence["approval_2"] = {"goal": rejected_goal, "request": rid2}
    resp = api.post(f"/governance/approvals/{rid2}/reject", json={
        "approver": "rw-ops-lead", "note": f"Change freeze {freeze} - not this week."})
    assert resp.status_code == 200, f"reject -> {resp.status_code}: {mask(resp.text)[:300]}"
    docs, waited2 = _wait_docs(api, cid, {f"agentverse://approvals/{rid1}",
                                          f"agentverse://approvals/{rid2}"})
    evidence["reject_doc_seconds"] = waited2
    evidence["documents"] = sorted(_doc_url(d) for d in docs)

    rank, hit = _hit_for(_search(api, cid, f"Was change ticket {ticket} approved?"),
                         f"agentverse://approvals/{rid1}")
    evidence["approved_rank"] = rank
    assert hit is not None, "the approval decision is not searchable"
    content = str(hit.get("content"))
    origin = hit.get("origin") or {}
    assert origin.get("approval_id") == rid1 and origin.get("goal_id") == approved_goal, origin
    assert origin.get("decision") == "approved", origin
    whole = _all_text(api, cid, [ticket, "approved decided by", "questions to e-mail"])
    # The approver is the authenticated caller (the API ignores a claimed name).
    if "APPROVED" not in whole or "Decided by: unknown" in whole:
        soft.append(f"decision / reviewer missing: {mask(content)[:300]}")
    assert reviewer_mail not in whole, "the reviewer's e-mail reached the index"
    rank2, hit2 = _hit_for(_search(api, cid, f"Why was the change rejected? {freeze}"),
                           f"agentverse://approvals/{rid2}")
    evidence["rejected_rank"] = rank2
    assert hit2 is not None and (hit2.get("origin") or {}).get("decision") == "rejected", hit2
    for gid in (approved_goal, rejected_goal):
        gx.wait_terminal(api, gid)
    assert _event_jobs(api, sid), "no event-triggered sync"
    assert not soft, "; ".join(soft)


# ── AGK-WORKFLOW ─────────────────────────────────────────────────────────────


def _vendor_workflow(name: str) -> str:
    return f"""\
name: {name}
description: Vendor onboarding - finance sign-off, then the onboarding record.
tags: [real-world, agent-knowledge]
trigger:
  type: api
inputs:
  vendor: {{type: string, required: true}}
  contact: {{type: string, required: true}}
  api_key: {{type: string, required: true}}
  limit_inr: {{type: string, required: true}}
steps:
  - id: finance_signoff
    name: Finance sign-off
    type: hitl
    timeout: 24h
    timeout_action: escalate
    context:
      - label: Vendor
        value: "{{{{inputs.vendor}}}}"
        display_type: text
      - label: Contact
        value: "{{{{inputs.contact}}}}"
        display_type: text
      - label: Credit limit (INR)
        value: "{{{{inputs.limit_inr}}}}"
        display_type: text
    actions:
      - id: approve
        label: Approve vendor
        style: success
      - id: reject
        label: Reject
        style: danger
  - id: onboarding_record
    name: Onboarding record
    type: code
    runtime: python
    depends_on: [finance_signoff]
    input:
      vendor: "{{{{inputs.vendor}}}}"
      contact: "{{{{inputs.contact}}}}"
      api_key: "{{{{inputs.api_key}}}}"
      limit_inr: "{{{{inputs.limit_inr}}}}"
      decision: "{{{{steps.finance_signoff.output.action}}}}"
    code: |
      output = {{
          "vendor": inputs.get("vendor"),
          "status": "onboarded",
          "credit_limit_inr": inputs.get("limit_inr"),
          "contact": inputs.get("contact"),
          "integration_key": inputs.get("api_key"),
          "decision": inputs.get("decision"),
      }}
"""


@pytest.mark.scenario("AGK-WORKFLOW")
def test_workflow_gate_and_result_become_knowledge(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    soft: list[str] = []
    secret = _secrets()
    vendor = f"Shakti Cold Chain {_rand(4)}"
    limit = str(random.randint(150, 950) * 1000)
    wf_id = wfx.import_yaml(api, cleanup, _vendor_workflow(f"rw-agk-vendor-{tag()}"))
    cid = _collection(api, cleanup, "rw-agk-workflow")
    src = _source(api, cleanup, cid, {"source_types": ["hitl_decision", "workflow_output"],
                                      "workflow_ids": [wf_id]})
    sid = src["id"]
    run_id = wfx.trigger_run(api, cleanup, wf_id, {
        "vendor": vendor, "contact": secret["email"], "api_key": secret["aws_key"],
        "limit_inr": limit})
    evidence["run_id"] = run_id
    run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    assert run.get("status") == "waiting_hitl", run.get("status")
    approval = wfx.find_approval(api, run_id)
    rid = str(approval["request_id"])
    evidence["approval"] = rid
    time.sleep(6)
    assert not _docs(api, cid), "a pending workflow gate was indexed"
    wfx.decide(api, rid, "approve", f"Credit limit {limit} INR approved for {vendor}.")
    run = wfx.wait_status(api, run_id, {"complete"}, wfx.FINISH_TIMEOUT)
    assert run.get("status") == "complete", f"run ended {run.get('status')}"
    urls = {f"agentverse://workflow-approvals/{rid}", f"agentverse://workflow-runs/{run_id}"}
    docs, waited = _wait_docs(api, cid, urls)
    evidence["seconds"] = waited
    evidence["documents"] = sorted(_doc_url(d) for d in docs)
    assert {_doc_url(d) for d in docs} == urls, evidence["documents"]

    rank, hit = _hit_for(_search(api, cid, f"What credit limit was approved for {vendor}?"),
                         f"agentverse://workflow-approvals/{rid}")
    evidence["decision_rank"] = rank
    assert hit is not None, "the workflow decision is not searchable"
    origin = hit.get("origin") or {}
    assert origin.get("approval_id") == rid and origin.get("workflow_run_id") == run_id, origin
    rank, hit = _hit_for(_search(api, cid, f"{vendor} onboarding status integration"),
                         f"agentverse://workflow-runs/{run_id}")
    evidence["run_rank"] = rank
    assert hit is not None and (hit.get("origin") or {}).get("workflow_id") == wf_id, hit
    text = _all_text(api, cid, [vendor, "integration key contact", "Credit limit approved"])
    leaked = [k for k, v in secret.items() if v in text]
    evidence["leaked"] = leaked
    assert not leaked, f"{leaked} reached the index"
    if "REDACTED" not in text:
        soft.append("no [REDACTED] marker where the contact / key were")

    # A disabled Source indexes nothing new.
    upd = api.patch(f"/sources/{sid}", json={"enabled": False})
    assert upd.status_code == 200, upd.text
    before_jobs = len(jobs.jobs(api, sid))
    run2 = wfx.trigger_run(api, cleanup, wf_id, {
        "vendor": vendor + " II", "contact": "accounts@example.com", "api_key": "none",
        "limit_inr": "1000"})
    wfx.wait_status(api, run2, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
    wfx.decide(api, str(wfx.find_approval(api, run2)["request_id"]), "reject", "No.")
    time.sleep(20)
    evidence["disabled_jobs"] = len(jobs.jobs(api, sid)) - before_jobs
    assert len(jobs.jobs(api, sid)) == before_jobs, "a disabled Source synced"
    assert {_doc_url(d) for d in _docs(api, cid)} == urls
    assert not soft, "; ".join(soft)


# ── AGK-GOVERNANCE ───────────────────────────────────────────────────────────


def _tag_principal(goal_id: str, principal: str) -> None:
    """A goal submitted on behalf of a data principal (channel user, …)."""
    _psql("UPDATE goals SET execution_context = (execution_context::jsonb || "
          f"jsonb_build_object('data_principal_id', '{principal}'))::json "
          f"WHERE id = '{goal_id}'")


@pytest.mark.scenario("AGK-GOVERNANCE")
def test_agent_knowledge_governance(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any], tenant_id: str) -> None:
    soft: list[str] = []
    agent = gx.create_agent(api, cleanup, "rw-agk-gov")
    cid = _collection(api, cleanup, "rw-agk-gov")
    sid = _source(api, cleanup, cid, {"source_types": ["goal_output"],
                                      "agent_ids": [agent]})["id"]
    held_goal = _submit(api, cleanup, (
        "Without using any tools, write two sentences: dock inspection IR-4471 at the Hosur "
        "yard found three cracked pallets, and the re-inspection is on Friday."), agent)
    open_goal = _submit(api, cleanup, (
        "Without using any tools, write one sentence: dock inspection IR-5590 at the Chakan "
        "yard found the seal photos missing for container 12."), agent)
    for gid in (held_goal, open_goal):
        _complete(api, gid)
    urls = {f"agentverse://goals/{held_goal}", f"agentverse://goals/{open_goal}"}
    docs, _ = _wait_docs(api, cid, urls)
    ids_before = sorted(str(d.get("document_id") or d.get("id")) for d in docs)

    hold_id = _hold(api, cid, "agk-gov")
    try:
        # 1. Reindex (delete + re-sync) under hold is refused; nothing changes.
        resp = api.post(f"/sources/{sid}/reindex")
        evidence["reindex_under_hold"] = {"http": resp.status_code,
                                          "detail": mask(body_of(resp))[:200]}
        assert resp.status_code == 409 and "legal hold" in resp.text.lower(), (
            evidence["reindex_under_hold"])
        again = jobs.sync(api, sid, timeout=300)  # a plain re-sync replaces nothing
        evidence["resync_under_hold"] = again
        assert again["docs_failed"] == 0 and again["docs_indexed"] == 0, again
        after = _docs(api, cid)
        assert sorted(str(d.get("document_id") or d.get("id")) for d in after) == ids_before

        # 2. Erasure of the claimant: the goal goes, its held knowledge stays (reported).
        principal = f"inspector-{_rand(8, string.ascii_lowercase)}"
        _tag_principal(held_goal, principal)
        er = api.post(f"/compliance/dpdp/erasure/{principal}/execute")
        evidence["erasure_held"] = {"http": er.status_code, "body": mask(body_of(er))[:600]}
        if er.status_code == 403:
            pytest.skip("the suite's key is not an admin: erasure not exercised")
        assert er.status_code == 200, evidence["erasure_held"]
        receipt = er.json()
        assert receipt.get("per_store", {}).get("goals") == 1, receipt
        assert "knowledge_chunks_held" in (receipt.get("notes") or {}), receipt
        assert api.get(f"/goals/{held_goal}").status_code == 404
        assert f"agentverse://goals/{held_goal}" in {_doc_url(d) for d in _docs(api, cid)}
    finally:
        _release_hold(hold_id, tenant_id)

    # 3. After release, reconciliation removes the knowledge of the deleted goal.
    jobs.reconcile(api, sid)
    remaining = wait_until(lambda: {_doc_url(d) for d in _docs(api, cid)}, timeout=180,
                           interval=4, desc="reconcile to remove the erased goal's document",
                           done=lambda u: f"agentverse://goals/{held_goal}" not in u)
    evidence["after_reconcile"] = sorted(remaining)
    assert f"agentverse://goals/{open_goal}" in remaining

    # 4. Without a hold the erasure removes the goal's knowledge at once.
    principal2 = f"inspector-{_rand(8, string.ascii_lowercase)}"
    _tag_principal(open_goal, principal2)
    er2 = api.post(f"/compliance/dpdp/erasure/{principal2}/execute")
    assert er2.status_code == 200, mask(er2.text)[:300]
    receipt2 = er2.json()
    evidence["erasure_open"] = {k: receipt2.get(k) for k in ("per_store", "verified")}
    assert receipt2.get("per_store", {}).get("knowledge_chunks_goal_derived", 0) >= 1, receipt2
    assert not _docs(api, cid), [_doc_url(d) for d in _docs(api, cid)]
    hits = _search(api, cid, "inspection IR-5590 seal photos")
    assert not [h for h in hits if "IR-5590" in str(h.get("content"))], "erased text served"
    assert not soft, "; ".join(soft)


# ── AGK-FAILURES ─────────────────────────────────────────────────────────────


@pytest.mark.scenario("AGK-FAILURES")
def test_agent_knowledge_honest_failures(
        api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = _collection(api, cleanup, "rw-agk-fail")
    refused = {}
    for name, cfg in {
        "unknown kind": {"source_types": ["goal_output", "chat_transcript"]},
        "empty kinds": {"source_types": []},
        "score > 1": {"min_eval_score": 3},
        "bad since": {"since": "last tuesday"},
        "agent ids not a list": {"agent_ids": "agent-1"},
        "per-sync bound": {"max_items_per_sync": 50_000},
    }.items():
        out = _source(api, cleanup, cid, cfg, expect=422)
        refused[name] = mask(out.get("detail"))[:160]
    evidence["refused"] = refused
    assert "chat_transcript" in refused["unknown kind"]
    ok = jobs.validate(api, family=FAMILY, source_type="agent_generated",
                       config={"source_types": ["goal_output"]}, collection_id=cid)
    evidence["validate"] = ok
    assert ok.get("valid") is True and (ok.get("connection") or {}).get("ok") is True, ok
    no_col = _source(api, cleanup, "", {"source_types": ["goal_output"]})
    resp = api.post(f"/sources/{no_col['id']}/sync")
    evidence["no_collection_sync"] = {"http": resp.status_code,
                                      "detail": mask(body_of(resp))[:200]}
    assert resp.status_code == 422, evidence["no_collection_sync"]
    fresh = _source(api, cleanup, cid, {"source_types": ["workflow_output"],
                                        "since": datetime.now(UTC).isoformat()})
    job = jobs.sync(api, fresh["id"], timeout=180)
    evidence["empty_sync"] = job
    assert str(job["status"]).lower() == "completed" and job["docs_failed"] == 0, job
    assert json.loads(str(job.get("cursor_after") or "{}")).get("v") == 1, job
