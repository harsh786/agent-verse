"""MONGO-PIPELINE-*: one realistic payments / commerce pipeline, end to end, on the live stack.

Real components only — the throwaway MongoDB replica set ``rw-mongo``, the stack's
MongoDB knowledge Source, its real embedder / reranker / LLM, the built-in MongoDB
MCP connector, the workflow engine, the approval inbox, the audit trail and SSE.
Nothing is mocked; a missing prerequisite SKIPS with the variable it needs.

The dataset (``commerce_seed``: customers, orders, payment events, multilingual
support tickets, postmortems; ``RW_MONGO_PIPELINE_DOCS`` documents, default 3,000,
deterministic, with planted known answers and planted PII) is seeded into
``rw_p1c`` (the Source's database, read as ``rwreader``) and its orders also into
``rw_shop`` (the operational database the MCP connector reads / writes as ``rwtool``).

Scenarios, in order (they share one synced collection; later ones change data):

* MONGO-PIPELINE-SYNC — full sync: EXACT document set (every source document once, no
  extra, no duplicate ids / URLs, chunk accounting matches the collection), provenance
  (Source id, ``mongodb://rw-mongo:27017/rw_p1c/<collection>/<_id>`` citations whose
  document id matches the inventory), content checksums of sampled documents, BSON
  fidelity (Decimal128, Int64 > 2^53, nulls, the 100-item array window and its marker,
  7-level nesting), PII redaction of exactly the planted ticket.
* MONGO-PIPELINE-RETRIEVAL — 27 hard known-answer questions (numeric, date, nested,
  arrays, cross-collection hops, Hindi / Japanese / Spanish / German, negation):
  hit@1/5/10, MRR, answer and citation accuracy against thresholds; abstention on a
  question with no evidence; no duplicate chunks in any result list.
* MONGO-PIPELINE-WORKFLOW — rag brief over the KB -> read-only ``mongodb_aggregate`` ->
  branch on its real result -> HITL gate -> platform-gated ``mongodb_insert_one``:
  approved once (exactly one ledger document, counted in MongoDB), rejected once (none),
  below-threshold run takes the other branch (no gate, none). Run / step states,
  approval records, audit rows, cost, SSE events.
* MONGO-PIPELINE-INCREMENTAL — inserts (``_id`` scan), in-place updates (change stream),
  deletes (reconcile): the KB matches MongoDB exactly; an unchanged re-sync indexes 0
  and keeps every content hash.
* MONGO-PIPELINE-ISOLATION — a second tenant sees none of the Source, collection,
  documents, search results or connector.

Environment: ``RW_MONGO_ROOT_PASSWORD``, ``RW_MONGO_READER_PASSWORD``,
``RW_MONGO_TOOL_PASSWORD`` (+ ``RW_MONGO_SEED_PORT``); ``RW_SECOND_TENANT_*`` for
ISOLATION; sizes / thresholds ``RW_MONGO_PIPELINE_DOCS``, ``RW_PIPELINE_SYNC_TIMEOUT``,
``RW_PIPELINE_K``, ``RW_PIPELINE_HIT5_MIN``, ``RW_PIPELINE_ANSWER_MIN``,
``RW_PIPELINE_CITATION_MIN``.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import commerce_seed as cs
from tests.real_world import kb
from tests.real_world import live_mongo as lm
from tests.real_world import source_jobs as sj
from tests.real_world import wf_mongo as wfm
from tests.real_world import workflows as wfx
from tests.real_world.helpers import (
    LiveAPI,
    SSECollector,
    env_float,
    load_api_key,
    mask,
    same_id,
    tag,
    wait_until,
)
from tests.real_world.metrics import answer_correct, norm, record
from tests.real_world.retrieval_eval import score

DOCS = int(os.getenv("RW_MONGO_PIPELINE_DOCS", "3000"))
SYNC_TIMEOUT = env_float("RW_PIPELINE_SYNC_TIMEOUT", 3600)
K = int(os.getenv("RW_PIPELINE_K", "10"))
HIT5_MIN = env_float("RW_PIPELINE_HIT5_MIN", 0.7)
ANSWER_MIN = env_float("RW_PIPELINE_ANSWER_MIN", 0.6)
CITATION_MIN = env_float("RW_PIPELINE_CITATION_MIN", 0.6)
DB = lm.SOURCE_DB


class _Cleanup:
    def __init__(self, api: LiveAPI) -> None:
        self.api = api
        self.todo: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str) -> None:
        self.todo.append((method, path))

    def run(self) -> None:
        for method, path in reversed(self.todo):
            with contextlib.suppress(Exception):
                self.api.request(method, path)


@pytest.fixture(scope="module")
def pipeline(api: LiveAPI) -> Iterator[dict[str, Any]]:
    """Seed MongoDB, create the collection + Source (pii_action=redact) + connector, run
    the full sync once. Shared by every MONGO-PIPELINE-* scenario of this module."""
    lm.env_secret("RW_MONGO_READER_PASSWORD")
    lm.env_secret("RW_MONGO_TOOL_PASSWORD")
    client = lm.seed_client()
    t = tag()
    data = cs.generate(DOCS)
    names: dict[str, str] = {}
    shop_names: dict[str, str] = {}
    done = _Cleanup(api)
    try:
        started = time.monotonic()
        names = cs.seed(client, DB, t, data)
        shop_names = {"orders": f"orders_{t}", "ledger": f"returns_ledger_{t}"}
        client[lm.TOOL_DB][shop_names["orders"]].insert_many(data.docs["orders"], ordered=False)
        seed_s = round(time.monotonic() - started, 1)
        body = api.json_ok("POST", "/knowledge/collections", json={
            "name": f"rw-mongo-pipeline-{t}", "embedder_type": "default",
            "description": "MONGO-PIPELINE commerce knowledge base"})
        cid = str(body.get("collection_id") or body.get("id"))
        done("DELETE", f"/knowledge/collections/{cid}")
        cfg = lm.reader_config(list(names.values()), max_documents_per_sync=data.total + 1000)
        src = lm.create_source(api, done, cid, cfg, pii_action="redact")
        connector = lm.register_connector(api, done, f"commerce-db-{t}", lm.tool_dsn())
        job = sj.sync(api, src["id"], timeout=SYNC_TIMEOUT)
        expected = lm.expected_tails(DB, names, {lg: data.keys(lg) for lg in cs.LOGICAL})
        yield {"data": data, "names": names, "shop": shop_names, "client": client,
               "collection_id": cid, "source_id": src["id"], "job1": job, "tag": t,
               "expected": expected, "server_id": connector["server_id"], "seed_s": seed_s,
               "questions": cs.questions(data)}
    finally:
        done.run()
        with contextlib.suppress(Exception):
            cs.drop(client, DB, names)
            for name in shop_names.values():
                client[lm.TOOL_DB].drop_collection(name)
        client.close()


def _search(api: LiveAPI, cid: str, q: str, k: int = K) -> list[dict[str, Any]]:
    return kb.search(api, cid, q, top_k=min(k, 20))


def _hit_from(hits: list[dict[str, Any]], names: dict[str, str], logical: str, key: str
              ) -> list[dict[str, Any]]:
    return [h for h in hits if cs.hit_is(h, DB, names[logical], key)]


# ── MONGO-PIPELINE-SYNC ─────────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-PIPELINE-SYNC")
def test_pipeline_full_sync_exact(api: LiveAPI, pipeline: dict[str, Any],
                                  evidence: dict[str, Any]) -> None:
    p = pipeline
    data: cs.CommerceData = p["data"]
    cid, sid, names = p["collection_id"], p["source_id"], p["names"]
    job = p["job1"]
    evidence.update(collection_id=cid, source_id=sid, counts=data.counts, sync=job,
                    dataset_digest=cs.digest(data)[:16])
    soft: list[str] = []
    if str(job.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"full sync {job.get('status')}: {job.get('error_message')}")
    if int(job.get("docs_failed") or 0):
        soft.append(f"{job.get('docs_failed')} documents failed in the full sync")
    inv = lm.inventory(api, sid, cid, p["expected"])
    evidence["inventory"] = {k: v for k, v in inv.items() if k != "content_hash"}
    soft += lm.exactness_problems(inv)
    if inv.get("collection_listing_total") not in (None, data.total):
        soft.append(f"collection listing total {inv['collection_listing_total']} != "
                    f"{data.total}")

    # Provenance: citations name the MongoDB document; document ids match the inventory.
    docs = {lm.url_tail(str(d.get("source_url"))): d for d in lm.source_documents(api, sid)}
    if any(not sj._same(d.get("source_id"), sid) for d in docs.values()):
        soft.append("documents listed for the Source carry another source_id")
    probes = [("customers", "CUS-F0002", "Rohan D'Souza Goa boutique billing locality"),
              ("postmortems", "PM-2026-014", "settlement webhook outage root cause"),
              ("orders", str(data.planted["order_total"]), "double-boxed Bidriware vases")]
    prov: dict[str, Any] = {}
    for logical, key, q in probes:
        hits = _hit_from(_search(api, cid, q), names, logical, key)
        tail = cs.doc_path(DB, names[logical], key)
        if not hits:
            soft.append(f"{logical}/{key} not retrievable for {q!r}")
            continue
        url = cs.hit_url(hits[0])
        prov[f"{logical}/{key}"] = url
        if not url.startswith(f"mongodb://{lm.RS_HOST}/{DB}/"):
            soft.append(f"citation {url!r} does not name the MongoDB document")
        listed = docs.get(tail)
        if listed and hits[0].get("document_id") and not same_id(
                hits[0]["document_id"], listed.get("doc_id")):
            soft.append(f"{tail}: search document_id {hits[0]['document_id']} != inventory "
                        f"{listed.get('doc_id')}")
    evidence["provenance"] = prov

    # Content checksums of sampled small documents (every string leaf served verbatim).
    sample = [("customers", k) for k in data.keys("customers")[4:12]] + \
             [("postmortems", k) for k in data.keys("postmortems")[2:6]]
    checks: dict[str, Any] = {}
    for logical, key in sample:
        doc = data.find(logical, key)
        q = str(doc.get("name") or doc.get("title")) + " " + str(doc.get("notes") or
                                                                 doc.get("summary") or "")
        text = " ".join(str(h.get("content")) for h in
                        _hit_from(_search(api, cid, q, 20), names, logical, key))
        leaves = cs.leaf_values(doc)
        found = [v for v in leaves if norm(v) in norm(text)]
        checks[key] = {"expected": cs.content_checksum(leaves)[:12],
                       "served": cs.content_checksum(found)[:12],
                       "missing": [v for v in leaves if v not in found][:4]}
        if found != leaves:
            soft.append(f"{logical}/{key}: content checksum mismatch, missing "
                        f"{checks[key]['missing']}")
    evidence["content_checksums"] = checks

    # BSON fidelity + the array window.
    fidelity = {"decimal128": ("double-boxed Bidriware vases order total", "48213.75",
                               "orders", str(data.planted["order_total"])),
                "int64>2^53": ("settlement batch SB-5521 ledger sequence", str(cs.LEDGER_SEQ),
                               "payment_events", str(data.planted["pay_ledger"])),
                "7-level nesting": ("Dock 7B night slot Bhiwandi consolidation hub", "Dock 7B",
                                    "orders", str(data.planted["order_dock"])),
                "array item 7 of 140": ("Channapatna lacquer toy train museum shop",
                                        "Channapatna lacquer toy train", "orders",
                                        str(data.planted["order_bulk"])),
                "array marker": ("bulk order handicraft items more items not indexed",
                                 "40 more item(s) of 140 not indexed", "orders",
                                 str(data.planted["order_bulk"]))}
    ranks: dict[str, Any] = {}
    for name, (q, needle, logical, key) in fidelity.items():
        r = cs.rank_in(_search(api, cid, q, 20), DB, names[logical], key, needle)
        ranks[name] = r
        if r is None:
            soft.append(f"BSON fidelity: {name} ({needle!r}) not served")
    beyond = [h for h in _search(api, cid, cs.BEYOND_WINDOW_ITEM, 20)
              if norm(cs.BEYOND_WINDOW_ITEM) in norm(h.get("content"))]
    if beyond:
        soft.append("array item 131 of 140 was indexed although arrays are bounded at 100")
    evidence["fidelity_ranks"] = ranks

    # PII: exactly the planted ticket is redacted; the raw identifiers are never served.
    pii_tail = cs.doc_path(DB, names["support_tickets"], str(data.planted["ticket_pii"]))
    flagged = inv["pii_redacted_docs"]
    evidence["pii_flagged"] = flagged[:10]
    if pii_tail not in flagged:
        soft.append("the planted PII ticket is not flagged has_pii_redacted")
    if len([f for f in flagged if f != pii_tail]):
        soft.append(f"{len(flagged) - 1} generic documents flagged as PII-redacted "
                    f"(e.g. {[f for f in flagged if f != pii_tail][:3]})")
    leaked: list[str] = []
    for q in ("Neha Kulkarni duplicate debit saree order", cs.PII["email"], cs.PII["pan"]):
        for h in _search(api, cid, q, 20):
            content = str(h.get("content"))
            for raw in (cs.PII["email"], cs.PII["pan"], cs.PII["card"],
                        cs.PII["card"].replace(" ", "")):
                if raw in content:
                    leaked.append(raw[:6] + "…")
    if leaked:
        soft.append(f"raw PII served by search: {sorted(set(leaked))}")
    pii_hits = _hit_from(_search(api, cid, "Neha Kulkarni duplicate debit saree order", 20),
                         names, "support_tickets", str(data.planted["ticket_pii"]))
    if not pii_hits:
        soft.append("the PII ticket's remaining text is not searchable after redaction")
    else:
        body = str(pii_hits[0].get("content"))
        evidence["pii_chunk"] = body[:300]
        missing_markers = [c for c in cs.PII["categories"] if f"[REDACTED:{c}]" not in body]
        if missing_markers:
            soft.append(f"redaction markers missing for {missing_markers}")
    wall = float(job.get("wall_s") or 0)
    record(evidence, documents=data.total, seed_s=p["seed_s"], sync_s=wall,
           docs_per_s=round(data.total / wall, 2) if wall else 0.0,
           chunks=inv["chunks_by_documents"], chunks_per_s=round(
               inv["chunks_by_documents"] / wall, 2) if wall else 0.0,
           missing=inv["missing_count"], extra=inv["extra_count"])
    assert not soft, "; ".join(soft)


# ── MONGO-PIPELINE-RETRIEVAL ────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-PIPELINE-RETRIEVAL")
def test_pipeline_known_answers(api: LiveAPI, pipeline: dict[str, Any],
                                evidence: dict[str, Any]) -> None:
    p = pipeline
    cid, names = p["collection_id"], p["names"]
    questions: list[cs.Question] = p["questions"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    dup_lists: list[str] = []
    for q in questions:
        row: dict[str, Any] = {"id": q.id, "kind": q.kind}
        started = time.monotonic()
        hits = _search(api, cid, q.question, K)
        row["search_ms"] = (time.monotonic() - started) * 1000
        row["rank"] = cs.rank_in(hits, DB, names[q.collection], q.key, q.must_contain)
        if cs.duplicate_hits(hits):
            dup_lists.append(q.id)
        status, body, ms = kb.rag_query(api, cid, q.question, strategy="hybrid", top_k=5)
        row["rag_ms"] = ms
        if status != 200:
            errors.append(f"{q.id}: /rag/query -> {status} {mask(body)[:160]}")
            row.update(answered=False, cited=False)
            rows.append(row)
            continue
        answer = kb.answer_text(body)
        row["answered"] = answer_correct(answer, q.as_eval()) if answer.strip() else False
        row["answer_head"] = answer[:140]
        cites = list(body.get("citations") or [])
        row["cited"] = cs.rank_in(cites, DB, names[q.collection], q.key, q.must_contain) \
            is not None
        rows.append(row)
    metrics = score(rows)
    hit10 = round(sum(1 for r in rows if r.get("rank") and r["rank"] <= 10) / len(rows), 3)
    record(evidence, **{k: v for k, v in metrics.items() if k != "by_kind"}, hit_at_10=hit10)
    evidence.update(by_kind=metrics["by_kind"],
                    misses=[r["id"] for r in rows if not r.get("rank")],
                    wrong_answers={r["id"]: r.get("answer_head") for r in rows
                                   if r.get("answered") is False},
                    bad_citations=[r["id"] for r in rows if r.get("cited") is False])

    # Abstention: no evidence exists for this question.
    status, body, _ = kb.rag_query(api, cid, cs.ABSTAIN_QUESTION, strategy="hybrid", top_k=5)
    abstained = cs.abstained(status, body)
    evidence["abstention"] = {"http": status, "abstained": abstained,
                              "answer": kb.answer_text(body)[:200] if status == 200 else
                              mask(body)[:200]}
    soft: list[str] = []
    if len(questions) < 25:
        soft.append(f"only {len(questions)} questions")
    if errors:
        soft.append(f"{len(errors)} RAG queries failed: {errors[:3]}")
    if metrics["hit_at_5"] < HIT5_MIN:
        soft.append(f"hit@5 {metrics['hit_at_5']:.2f} < {HIT5_MIN} (misses {evidence['misses']})")
    if metrics["answer_accuracy"] < ANSWER_MIN:
        soft.append(f"answer accuracy {metrics['answer_accuracy']:.2f} < {ANSWER_MIN}")
    if metrics["citation_accuracy"] < CITATION_MIN:
        soft.append(f"citation accuracy {metrics['citation_accuracy']:.2f} < {CITATION_MIN}")
    if not abstained:
        soft.append(f"no abstention on a question without evidence: {evidence['abstention']}")
    if dup_lists:
        soft.append(f"duplicate chunks in the result lists of {dup_lists}")
    assert not soft, "; ".join(soft)


# ── MONGO-PIPELINE-WORKFLOW ─────────────────────────────────────────────────


def _ledger_count(p: dict[str, Any], run_id: str) -> int:
    return int(p["client"][lm.TOOL_DB][p["shop"]["ledger"]].count_documents({"run_id": run_id}))


def _audit_names(api: LiveAPI, *subjects: str) -> list[str]:
    rows: list[dict[str, Any]] = []
    for s in subjects:
        rows += wfx.audit_rows(api, s)
    return [str(r.get("tool_name")) for r in rows]


@pytest.mark.scenario("MONGO-PIPELINE-WORKFLOW")
def test_pipeline_workflow_kb_mongo_hitl(api: LiveAPI, cleanup: Any, pipeline: dict[str, Any],
                                         evidence: dict[str, Any]) -> None:
    p = pipeline
    data: cs.CommerceData = p["data"]
    truth = sum(1 for d in data.docs["orders"] if d.get("status") == "returned")
    t = p["tag"]
    common = {"collection_id": p["collection_id"], "server_id": p["server_id"],
              "orders": p["shop"]["orders"], "ledger": p["shop"]["ledger"]}
    wf_gate = wfx.import_yaml(api, cleanup, wfm.returns_review_yaml(
        f"rw-returns-review-{t}", threshold=max(0, truth - 1), **common))
    wf_auto = wfx.import_yaml(api, cleanup, wfm.returns_review_yaml(
        f"rw-returns-auto-{t}", threshold=truth + 10000, **common))
    evidence.update(workflow_gate=wf_gate, workflow_auto=wf_auto, returned_truth=truth)
    soft: list[str] = []
    key = load_api_key()

    # Run A: approved.
    run_a = wfx.trigger_run(api, cleanup, wf_gate)
    sse = SSECollector(key, f"{wfx.V1}/runs/{run_a}/stream", max_seconds=wfx.GATE_TIMEOUT +
                       wfx.FINISH_TIMEOUT).start()
    final_a, decided_a = wfx.drive_approvals(api, run_a, {}, wfx.GATE_TIMEOUT + wfx.FINISH_TIMEOUT)
    time.sleep(2)
    sse.stop()
    steps_a = wfx.get_steps(api, run_a)
    evidence["run_approved"] = {"run_id": run_a, "status": final_a.get("status"),
                                "steps": {k: v.get("status") for k, v in steps_a.items()},
                                "decided": decided_a, "error": mask(final_a.get("error"))[:200]}
    if final_a.get("status") != "complete":
        soft.append(f"approved run ended {final_a.get('status')} at "
                    f"{final_a.get('error_step_id')}: {mask(final_a.get('error'))[:160]}")
    counted = lm.tool_result(steps_a.get("count_returns")).get("results") or []
    got = int((counted[0] if counted else {}).get("returned") or 0)
    if got != truth:
        soft.append(f"aggregate counted {got} returned orders, MongoDB holds {truth}")
    if (wfx.output_of(steps_a, "route") or {}).get("chosen_branch") != "ops_review":
        soft.append(f"route chose {wfx.output_of(steps_a, 'route')} (expected ops_review)")
    if steps_a.get("auto_close", {}).get("status") == "complete":
        soft.append("the branch not taken (auto_close) ran")
    brief = wfx.output_of(steps_a, "kb_brief")
    evidence["kb_brief"] = mask(brief)[:300]
    if "certificate" not in norm(brief):
        soft.append(f"the rag step's brief does not name the expired certificate: "
                    f"{mask(brief)[:160]}")
    if _ledger_count(p, run_a) != 1:
        soft.append(f"approved run wrote {_ledger_count(p, run_a)} ledger documents "
                    "(expected exactly 1)")
    summary = wfx.output_of(steps_a, "summary")
    evidence["summary_approved"] = summary
    if summary.get("decision") != "approve" or summary.get("returned_orders") != truth:
        soft.append(f"summary of the approved run: {summary}")
    gate_ids = [d["request_id"] for d in decided_a if d["step_id"] == "ops_review"]
    if not gate_ids:
        soft.append("the approved run never raised the ops_review approval")
    else:
        appr = api.json_ok("GET", f"{wfx.V1}/approvals/{gate_ids[0]}")
        evidence["approval_record"] = {k: appr.get(k) for k in ("status", "action_taken",
                                                                "decided_by", "note")}
        if appr.get("status") in ("pending", None):
            soft.append(f"approval record still {appr.get('status')} after the decision")
    if not any(d["step_id"] == "record_decision" for d in decided_a):
        evidence["write_gate"] = "the platform did not gate mongodb_insert_one (write_high)"
    names = _audit_names(api, run_a, wf_gate)
    evidence["audit_approved"] = {n: names.count(n) for n in sorted(set(names))}
    for needed in ("workflow.run.started", "workflow.run.waiting_approval",
                   "workflow.run.completed", "workflow.run_triggered",
                   "workflow.approval_decided"):
        if needed not in names:
            soft.append(f"no {needed} audit row for the approved run")
    if names.count("workflow.step.completed") < 5:
        soft.append(f"only {names.count('workflow.step.completed')} step-completed audit rows")
    cost, tokens = float(final_a.get("cost_usd") or 0), int(final_a.get("tokens_used") or 0)
    if cost <= 0 and tokens <= 0:
        soft.append("the run records no LLM cost or tokens although its rag step ran")
    costs = api.get("/costs/summary", params={"period_days": 1})
    evidence["cost_summary_http"] = costs.status_code
    events = sse.events
    evidence["sse"] = {"events": len(events), "status": sse.status_code, "error": sse.error}
    for needle in ('"step_completed"', "kb_brief", "count_returns", "run_completed"):
        if not sse.saw(needle):
            soft.append(f"SSE stream never carried {needle}")

    # Run B: rejected.
    run_b = wfx.trigger_run(api, cleanup, wf_gate)
    final_b, decided_b = wfx.drive_approvals(api, run_b, {"ops_review": (
        "reject", "Returns spike explained by the festival exchange window.")},
        wfx.GATE_TIMEOUT + wfx.FINISH_TIMEOUT)
    steps_b = wfx.get_steps(api, run_b)
    evidence["run_rejected"] = {"run_id": run_b, "status": final_b.get("status"),
                                "steps": {k: v.get("status") for k, v in steps_b.items()},
                                "decided": decided_b}
    if final_b.get("status") not in ("failed", "rejected", "cancelled"):
        soft.append(f"rejected run ended {final_b.get('status')}")
    if steps_b.get("record_decision", {}).get("status") == "complete":
        soft.append("the rejected run still recorded its decision")
    if _ledger_count(p, run_b) != 0:
        soft.append(f"rejected run wrote {_ledger_count(p, run_b)} ledger documents")

    # Run C: below the threshold -> the other branch, no gate, no write.
    run_c = wfx.trigger_run(api, cleanup, wf_auto)
    final_c, decided_c = wfx.drive_approvals(api, run_c, {}, wfx.FINISH_TIMEOUT + 180)
    steps_c = wfx.get_steps(api, run_c)
    evidence["run_auto"] = {"run_id": run_c, "status": final_c.get("status"),
                            "steps": {k: v.get("status") for k, v in steps_c.items()},
                            "decided": decided_c}
    if final_c.get("status") != "complete":
        soft.append(f"below-threshold run ended {final_c.get('status')}")
    if decided_c:
        soft.append(f"below-threshold run raised approvals: {decided_c}")
    if (wfx.output_of(steps_c, "summary") or {}).get("decision") != "auto_closed":
        soft.append(f"below-threshold summary: {wfx.output_of(steps_c, 'summary')}")
    if _ledger_count(p, run_c) != 0:
        soft.append("below-threshold run wrote to the ledger")
    record(evidence, cost_usd=cost, tokens_used=tokens,
           duration_ms=float(final_a.get("duration_ms") or 0), sse_events=len(events))
    assert not soft, "; ".join(soft)


# ── MONGO-PIPELINE-INCREMENTAL ──────────────────────────────────────────────


@pytest.mark.scenario("MONGO-PIPELINE-INCREMENTAL")
def test_pipeline_incremental_exact(api: LiveAPI, pipeline: dict[str, Any],
                                    evidence: dict[str, Any]) -> None:
    p = pipeline
    data: cs.CommerceData = p["data"]
    cid, sid, names, client = p["collection_id"], p["source_id"], p["names"], p["client"]
    db = client[DB]
    pl = data.planted
    before_jobs = {str(j.get("job_id")) for j in sj.jobs(api, sid)}
    inserted = cs.new_orders(5)
    db[names["orders"]].insert_many(inserted)
    old_ticket = str(data.find("support_tickets", str(pl["update_ticket"]))["body"])
    db[names["support_tickets"]].update_one({"_id": pl["update_ticket"]},
                                            {"$set": {"body": cs.UPDATED_TICKET}})
    db[names["customers"]].update_one(
        {"_id": pl["update_customer"]},
        {"$set": {"preferences.comms.invoice.language": cs.UPDATED_LANGUAGE}})
    db[names["orders"]].update_one({"_id": pl["update_order"]},
                                   {"$set": {"notes": "Re-packed after a forklift scrape; "
                                                      "insurer survey ref INS-20931."}})
    db[names["payment_events"]].delete_one({"_id": pl["delete_payment"]})
    db[names["support_tickets"]].delete_one({"_id": pl["delete_ticket"]})
    time.sleep(1)

    job2 = sj.sync(api, sid, timeout=1800)
    later = [j for j in sj.jobs(api, sid) if str(j.get("job_id")) not in before_jobs]
    indexed = sum(int(j.get("docs_indexed") or 0) for j in later)
    evidence.update(sync2=job2, jobs_after=[(j.get("triggered_by"), j.get("status"),
                                             j.get("docs_indexed")) for j in later])
    soft: list[str] = []
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"incremental sync {job2.get('status')}: {job2.get('error_message')}")
    if indexed != len(inserted) + 3:
        soft.append(f"{indexed} documents indexed by the incremental sync(s), expected "
                    f"{len(inserted) + 3} (5 inserts + 3 updates)")
    if not _hit_from(_search(api, cid, "Rush Onam order banana-leaf platters Kochi store"),
                     names, "orders", str(inserted[0]["_id"])):
        soft.append("inserted order not searchable")
    upd = _hit_from(_search(api, cid, "replacement shipped via Blue Dart airway bill"),
                    names, "support_tickets", str(pl["update_ticket"]))
    if not upd or norm(cs.UPDATED_TICKET) not in norm(upd[0].get("content")):
        soft.append("updated ticket's new text not served (change stream)")
    stale = [h for h in _search(api, cid, old_ticket, 20)
             if cs.hit_is(h, DB, names["support_tickets"], str(pl["update_ticket"]))
             and norm(old_ticket) in norm(h.get("content"))]
    if stale:
        soft.append("updated ticket's OLD text is still served")
    if not _hit_from(_search(api, cid, "forklift scrape insurer survey INS-20931"),
                     names, "orders", str(pl["update_order"])):
        soft.append("updated order's new notes not served")

    sj.reconcile(api, sid)
    expected = lm.expected_tails(DB, names, {lg: data.keys(lg) for lg in cs.LOGICAL})
    expected |= {cs.doc_path(DB, names["orders"], str(d["_id"])) for d in inserted}
    expected -= {cs.doc_path(DB, names["payment_events"], str(pl["delete_payment"])),
                 cs.doc_path(DB, names["support_tickets"], str(pl["delete_ticket"]))}
    try:
        inv = wait_until(lambda: lm.inventory(api, sid, cid, expected), timeout=600,
                         interval=10, desc="reconcile to remove the 2 deleted documents",
                         done=lambda i: not lm.exactness_problems(i))
    except AssertionError:
        inv = lm.inventory(api, sid, cid, expected)
    soft += lm.exactness_problems(inv)
    evidence["inventory_after"] = {k: v for k, v in inv.items() if k != "content_hash"}

    # Unchanged re-sync: nothing indexed, every content hash kept.
    hashes = dict(inv["content_hash"])
    job3 = sj.sync(api, sid, timeout=1800)
    evidence["sync_unchanged"] = job3
    if int(job3.get("docs_indexed") or 0) != 0:
        soft.append(f"an unchanged re-sync indexed {job3.get('docs_indexed')} documents")
    after = lm.inventory(api, sid, cid, expected)
    changed = [k for k, v in after["content_hash"].items() if hashes.get(k) != v]
    if changed or after["documents"] != inv["documents"]:
        soft.append(f"an unchanged re-sync changed {len(changed)} documents' content hash "
                    f"(documents {inv['documents']} -> {after['documents']})")
    record(evidence, incremental_indexed=indexed, sync2_s=job2.get("wall_s"),
           unchanged_sync_s=job3.get("wall_s"), documents_final=after["documents"])
    assert not soft, "; ".join(soft)


# ── MONGO-PIPELINE-ISOLATION ────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-PIPELINE-ISOLATION")
def test_pipeline_tenant_isolation(api: LiveAPI, second_tenant_api: LiveAPI | None,
                                   pipeline: dict[str, Any], evidence: dict[str, Any]) -> None:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE: a key of a "
                    "different tenant")
    other = second_tenant_api
    p = pipeline
    cid, sid, server_id = p["collection_id"], p["source_id"], p["server_id"]
    answers = {
        "GET source": other.get(f"/sources/{sid}").status_code,
        "sync source": other.post(f"/sources/{sid}/sync").status_code,
        "source documents": other.get("/ingestion/documents",
                                      params={"source_id": sid}).status_code,
        "collection documents": other.get(f"/knowledge/collections/{cid}/documents").status_code,
        "collection stats": other.get(f"/knowledge/collections/{cid}/stats").status_code,
        "GET connector": other.get(f"/connectors/{server_id}").status_code,
        "connector tools": other.get(f"/connectors/{server_id}/tools").status_code,
    }
    search = other.get("/knowledge/search", params={"q": "Bidriware vases order total",
                                                    "collection_id": cid, "top_k": 10})
    rag = other.post("/rag/query", json={"query": "What is the root cause of PM-2026-014?",
                                         "collection_id": cid, "top_k": 5})
    dlq = other.get("/ingestion/dlq", params={"source_id": sid})
    listed_sources = [s for s in (other.get("/sources").json() or [])
                      if same_id(s.get("source_id") or s.get("id"), sid)]
    evidence.update(answers=answers, search_http=search.status_code, rag_http=rag.status_code,
                    dlq_http=dlq.status_code, listed_sources=len(listed_sources))
    soft: list[str] = []
    leaked = [k for k, c in answers.items() if c < 400]
    if leaked:
        soft.append(f"second tenant got 2xx for {leaked}")
    if search.status_code == 200 and search.json():
        soft.append(f"second tenant's search returned {len(search.json())} hits from the "
                    "collection")
    if rag.status_code == 200 and (rag.json().get("citations") or []):
        soft.append("second tenant's RAG query cited the collection")
    if dlq.status_code == 200 and dlq.json():
        soft.append("second tenant sees the Source's DLQ entries")
    if listed_sources:
        soft.append("the Source appears in the second tenant's listing")
    if api.get(f"/sources/{sid}").status_code != 200:
        soft.append("the owner lost access to its Source")
    assert not soft, "; ".join(soft)
