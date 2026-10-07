"""Offline checks of the MONGO-PIPELINE / CHAOS / SCALE / ONPREM / OCR fixtures and helpers.

Pure helpers only — no live stack, no fake platform or vendor. Where the platform's own
code decides what a fixture must satisfy, that code is used: the MongoDB connector's
document flattener (what gets indexed), the ingestion PII analyzer (what gets
redacted) and the workflow DSL / publish checks / compiler (what a workflow import
accepts). So a live scenario never fails on a defect in its own fixture.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
import yaml

from tests.real_world import chaos
from tests.real_world import commerce_seed as cs
from tests.real_world import live_mongo as lm
from tests.real_world import loadgen
from tests.real_world import ocr_fixtures as ocf
from tests.real_world import onprem as op
from tests.real_world import wf_mongo as wfm
from tests.real_world.metrics import answer_correct, norm
from tests.real_world.sse_stream import gaps_and_duplicates, parse_sse


@pytest.fixture(scope="module")
def data() -> cs.CommerceData:
    return cs.generate(3000)


def _rendered(doc: dict[str, Any]) -> str:
    """The text the platform's MongoDB connector indexes for ``doc``."""
    from app.ingestion.connectors.mongodb_connector import _flatten

    return _flatten({**doc, "_id": str(doc["_id"])})[0]


# ── commerce dataset ─────────────────────────────────────────────────────────


def test_dataset_is_deterministic_and_sized(data: cs.CommerceData) -> None:
    again = cs.generate(3000)
    assert cs.digest(data) == cs.digest(again)
    assert cs.digest(data) != cs.digest(cs.generate(3000, seed=cs.SEED + 1))
    assert data.total == 3000 and set(data.counts) == set(cs.LOGICAL)
    small = cs.generate(600)
    assert all(n >= 12 for n in small.counts.values())
    for logical in cs.LOGICAL:
        keys = data.keys(logical)
        assert len(keys) == len(set(keys)), f"duplicate _id in {logical}"


def test_seeded_object_ids_sort_before_later_inserts(data: cs.CommerceData) -> None:
    ids = [d["_id"] for d in data.docs["orders"]]
    fresh = [d["_id"] for d in cs.new_orders(3)]
    assert max(ids) < min(fresh), "post-sync inserts must sort after every seeded _id"


def test_questions_are_answerable_from_the_indexed_text(data: cs.CommerceData) -> None:
    questions = cs.questions(data)
    assert len(questions) >= 25 and len({q.id for q in questions}) == len(questions)
    kinds = {q.kind for q in questions}
    assert {"numeric", "date", "nested", "array", "multilingual", "negation",
            "cross_collection"} <= kinds
    for q in questions:
        text = _rendered(data.find(q.collection, q.key))
        assert norm(q.must_contain) in norm(text), f"{q.id}: fact not in the indexed text"
        assert answer_correct(q.must_contain + " " + text[:0], q.as_eval()) or any(
            norm(a) in norm(text) for a in q.answer_any), f"{q.id}: no accepted answer in text"
        for logical, key in q.related:
            assert data.find(logical, key)


def test_bson_fidelity_and_array_window_in_indexed_text(data: cs.CommerceData) -> None:
    pl = data.planted
    bulk = _rendered(data.find("orders", str(pl["order_bulk"])))
    assert "40 more item(s) of 140 not indexed" in bulk
    assert cs.BEYOND_WINDOW_ITEM not in bulk and "Channapatna lacquer toy train" in bulk
    attempts = _rendered(data.find("payment_events", str(pl["pay_attempts"])))
    assert "50 more item(s) of 150 not indexed" in attempts and "Kotak switch" in attempts
    assert str(cs.LEDGER_SEQ) in _rendered(data.find("payment_events", str(pl["pay_ledger"])))
    assert "48213.75" in _rendered(data.find("orders", str(pl["order_total"])))
    assert "nested deeper than 5 levels" in _rendered(data.find("orders", str(pl["order_dock"])))
    assert not cs.luhn_ok(str(cs.LEDGER_SEQ)) and cs.LEDGER_SEQ > 2 ** 53


def test_only_the_planted_ticket_triggers_the_platform_pii_screen(data: cs.CommerceData) -> None:
    from app.ingestion.pii import RegexPIIAnalyzer

    analyzer = RegexPIIAnalyzer()
    pii_key = str(data.planted["ticket_pii"])
    flagged = []
    for logical in cs.LOGICAL:
        for doc in data.docs[logical]:
            if str(doc["_id"]) == pii_key:
                continue
            if analyzer.analyze(_rendered(doc)):
                flagged.append(f"{logical}/{doc['_id']}")
    assert not flagged, f"generic documents the PII screen would redact: {flagged[:5]}"
    redacted = analyzer.redact(_rendered(data.find("support_tickets", pii_key)))
    for category in cs.PII["categories"]:
        assert f"[REDACTED:{category}]" in redacted
    assert cs.PII["email"] not in redacted and "Neha Kulkarni" in redacted


def test_content_checksum_and_duplicate_helpers() -> None:
    doc = {"_id": "CUS-1", "name": "Meera", "nested": {"a": {"b": "deep value"}},
           "money": None, "list": ["one item", "x"]}
    leaves = cs.leaf_values(doc)
    assert leaves == ["CUS-1", "Meera", "deep value", "one item"]
    assert cs.content_checksum(leaves) == cs.content_checksum(list(reversed(leaves)))
    assert cs.duplicates(["a", "b", "a", "c", "c", "c"]) == {"a": 2, "c": 3}
    hits = [{"source_url": "mongodb://h/db/c/1", "content": "Same text"},
            {"source_url": "mongodb://h/db/c/1", "content": "same  text"},
            {"source_url": "mongodb://h/db/c/2", "content": "same text"}]
    assert len(cs.duplicate_hits(hits)) == 1
    assert cs.hit_is(hits[0], "db", "c", "1") and not cs.hit_is(hits[2], "db", "c", "1")
    assert cs.rank_in(hits, "db", "c", "2", "same text") == 3
    assert cs.doc_path("rw_p1c", "orders_x", "a/b c") == "/rw_p1c/orders_x/a%2Fb%20c"


def test_abstention_rules() -> None:
    assert cs.abstained(422, {"detail": {"code": "answer_ungrounded"}})
    assert cs.abstained(200, {"answer": "x", "grounded": False})
    assert cs.abstained(200, {"answer": "The knowledge base does not contain PM-2026-099."})
    assert not cs.abstained(200, {"answer": "The root cause was a DNS outage led by Ravi.",
                                  "grounded": True})
    assert not cs.abstained(500, {})


def test_poison_and_drift_documents() -> None:
    from bson import BSON, decode
    from bson.codec_options import CodecOptions

    poison = cs.poison_docs()
    assert len(poison["oversize"]["blob"]) > 10 * 1024 * 1024
    assert cs.nesting_depth(poison["deep"]) >= 95
    assert len(BSON.encode(poison["deep"])) < 16 * 1024 * 1024
    assert len(poison["huge_array"]["events"]) == 20000
    raw = cs.invalid_utf8_raw("POISON-UTF8")
    with pytest.raises(Exception):  # strict decoding must refuse it
        decode(raw)
    lenient = decode(raw, CodecOptions(unicode_decode_error_handler="replace"))
    assert lenient["_id"] == "POISON-UTF8" and "�" in lenient["notes"]
    drifted = cs.drifted_payments(3)
    assert all(d["schema_version"] == 2 and isinstance(d["method"], dict) for d in drifted)


def test_scale_generator_streams_deterministically() -> None:
    from tests.real_world.test_scale_pipeline import scale_query

    first = [str(d["_id"]) for d in cs.scale_docs(500)]
    assert first == [str(d["_id"]) for d in cs.scale_docs(500)]
    assert len(set(first)) == 500
    keys = cs.needle_keys(100000)
    assert set(keys) == set(cs.SCALE_NEEDLES)
    assert keys[17] == first[16]
    q0, want0 = scale_query(0, keys)
    assert want0 in keys.values() and "Needle" not in q0
    q1, want1 = scale_query(1, keys)
    assert want1 is None and q1 == scale_query(1, keys)[0]


# ── live_mongo pure helpers ─────────────────────────────────────────────────


def test_inventory_exactness_rules() -> None:
    assert lm.url_tail("mongodb://rw-mongo:27017/rw_p1c/orders_x/abc") == "/rw_p1c/orders_x/abc"
    tails = lm.expected_tails("rw_p1c", {"orders": "orders_x"}, {"orders": ["a", "b"]})
    assert tails == {"/rw_p1c/orders_x/a", "/rw_p1c/orders_x/b"}
    clean = {"missing_count": 0, "missing": [], "extra_count": 0, "extra": [],
             "duplicate_ids": 0, "duplicate_urls": [], "documents": 2, "expected": 2,
             "collection_stats": {"chunk_count": 4}, "chunks_by_documents": 4}
    assert lm.exactness_problems(clean) == []
    broken = {**clean, "missing_count": 1, "missing": ["/x"], "duplicate_urls": ["/y"],
              "documents": 3, "collection_stats": {"chunk_count": 9}}
    problems = " ".join(lm.exactness_problems(broken))
    assert "not indexed" in problems and "duplicates" in problems
    assert "orphan or duplicate chunks" in problems and "expected exactly 2" in problems


# ── workflows ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("builder", [
    lambda: wfm.returns_review_yaml("rw-a", collection_id="c1", server_id="s1", orders="o",
                                    ledger="l", threshold=3),
    lambda: wfm.tool_failures_yaml("rw-b", server_id="s1", stalled_server_id="s2", orders="o"),
    lambda: wfm.two_writes_yaml("rw-c", server_id="s1", ledger="l"),
    lambda: wfm.approval_timeout_yaml("rw-d", server_id="s1", ledger="l"),
    lambda: wfm.side_effect_then_llm_yaml("rw-e", server_id="s1", ledger="l", collection_id="c"),
    lambda: wfm.llm_step_yaml("rw-f", model=op.SMALL_MODEL, prompt="x " * 2000),
])
def test_workflow_yaml_is_valid_publishable_and_compiles(builder: Any) -> None:
    from app.workflow.compiler import WorkflowCompiler
    from app.workflow.context import ContextResolver
    from app.workflow.dsl import WorkflowDefinition
    from app.workflow.service import publish_problems

    raw = yaml.safe_load(builder())
    assert publish_problems(raw) == []
    WorkflowCompiler(ContextResolver()).compile(WorkflowDefinition(**raw))


def test_returns_review_templates_resolve_against_real_tool_output() -> None:
    """The conditional / insert templates resolve on the MCP tool step's output shape."""
    from app.workflow.context import ContextResolver

    raw = yaml.safe_load(wfm.returns_review_yaml("rw-a", collection_id="c", server_id="s",
                                                 orders="o", ledger="l", threshold=3))
    steps = {s["id"]: s for s in raw["steps"]}
    assert [s["id"] for s in raw["steps"]] == wfm.RETURNS_STEPS
    state = {"run_id": "RUN-1", "step_outputs": {
        "count_returns": {"success": True, "output": {"results": [{"returned": 7}], "count": 1},
                          "error": ""},
        "ops_review": {"action": "approve", "note": "ok"}}}
    resolver = ContextResolver()
    cond = steps["route"]["branches"][0]["condition"]
    assert resolver.resolve(cond, state) == "7 > 3"
    doc = resolver.resolve_dict(steps["record_decision"]["input"], state)["document"]
    assert doc == {"run_id": "RUN-1", "kind": "returns_review", "returned": 7,
                   "decision": "approve", "note": "ok"}
    ns: dict[str, Any] = {"inputs": {"decision": None, "returned": "7", "root_cause": "x",
                                     "minutes": 47, "ledger_id": ""}}
    exec(wfm.SUMMARY_CODE, ns)
    assert ns["output"]["decision"] == "auto_closed" and ns["output"]["returned_orders"] == 7
    ns = {"inputs": {"bad_write_skipped": "True", "unauthorized_skipped": "true",
                     "slow_server_skipped": "False", "unknown_count": 0}}
    exec(wfm.COMPENSATE_CODE, ns)
    assert ns["output"]["compensated"] == ["bad_write", "unauthorized"]


def test_mongodb_tool_risk_matches_the_scenarios() -> None:
    """The scenarios rely on the platform gating inserts (write_high) and not aggregates."""
    from app.agent.tool_risk import classify_tool_risk

    assert classify_tool_risk("mongodb_insert_one", "commerce-db-x") == "write_high"
    assert classify_tool_risk("mongodb_aggregate", "commerce-db-x",
                              {"pipeline": wfm.RETURNS_PIPELINE}) == "read"


# ── SSE / load / chaos helpers ──────────────────────────────────────────────


def test_parse_sse_and_resume_accounting() -> None:
    lines = [": ping", "", "id: 3", 'data: {"type": "plan"}', "", "data: no-id", "",
             "retry: 5000", "event: error", 'data: {"type": "stream_error"}', "", "id: 4",
             "data: line one", "data: line two"]
    events = parse_sse(lines)
    assert [e.id for e in events] == [3, None, None, 4]
    assert events[0].data == {"type": "plan"} and events[2].event == "error"
    assert events[3].data == "line one\nline two"
    ok = gaps_and_duplicates([1, 2, 3], [4, 5], [1, 2, 3, 4, 5])
    assert ok == {"missing": [], "duplicated": [], "unknown": [], "out_of_order": []}
    bad = gaps_and_duplicates([1, 2, 3], [3, 5, 4], [1, 2, 3, 4, 5, 6])
    assert bad["missing"] == [6] and bad["duplicated"] == [3] and bad["out_of_order"] == [4]


def test_latency_and_error_summaries() -> None:
    lat = loadgen.latency_summary(list(range(1, 101)))
    assert (lat["p50"], lat["p95"], lat["p99"], lat["max"]) == (50, 95, 99, 100)
    assert loadgen.latency_summary([]) == {"n": 0}
    errs = loadgen.error_rate([{"status": 200}, {"status": 429}, {"status": 503},
                               {"status": "ReadTimeout"}, {"status": 404}])
    assert errs["rate_limited"] == 1 and errs["server_errors"] == 1
    assert errs["transport_errors"] == 1 and errs["client_errors"] == 1
    assert errs["error_rate"] == 0.6


def test_chaos_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RW_CHAOS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        chaos.require_chaos()
    monkeypatch.setenv("RW_CHAOS", "1")
    monkeypatch.delenv("RW_REDIS_CONTAINER", raising=False)
    with pytest.raises(pytest.skip.Exception, match="RW_REDIS_CONTAINER"):
        chaos.require_container("RW_REDIS_CONTAINER")
    with pytest.raises(ValueError):
        chaos.inject("format-disk", "anything")
    assert chaos.UNDO == {"stop": "start", "kill": "start", "pause": "unpause", "restart": None}
    tally = chaos.classify_outage_answers([503, 503, 200, 500, 404, "ConnectError"])
    assert tally == {"503": 2, "2xx": 1, "500": 1, "other": 1, "transport": 1}
    assert chaos.undo_all() == []


# ── on-prem registry helpers ────────────────────────────────────────────────


def test_onprem_defaults_and_role_proof_helpers() -> None:
    assert op.CHAT_URL.endswith("/v1") and op.MODELS["chat"]["model_id"] == "Qwen/Qwen3.5-4B"
    assert op.MODELS["embed"]["capabilities"] == ["embedding"]
    assert op.key(op.MODELS["rerank"]) == f"{op.PROVIDER}/Qwen/Qwen3-Reranker-0.6B"
    trace = {"role_calls": [{"role": "planner", "model": op.CHAT_MODEL},
                            {"role": "executor", "model": op.CHAT_MODEL}],
             "model_selections": [{"role": "verifier", "model": "claude-sonnet-4"}]}
    roles = op.models_by_role(trace)
    assert roles == {"planner": {op.CHAT_MODEL}, "executor": {op.CHAT_MODEL},
                     "verifier": {"claude-sonnet-4"}}
    assert op.cloud_models({op.CHAT_MODEL, "claude-sonnet-4", "openai/gpt-4o"}) == [
        "claude-sonnet-4", "openai/gpt-4o"]
    assert op.goal_text({"result": "205 and 295"}) == "205 and 295"
    # GET /goals/{id} serves the answer in result_artifact (no top-level "result").
    assert op.goal_text({"result_artifact": {"kind": "text", "summary": "144"}}) == "144"
    assert op.goal_text({"result_artifact": {
        "kind": "empty", "summary": "No structured result was produced."}}) == ""
    failover = {"role_calls": [
        {"role": "executor", "model": op.CHAT_MODEL, "fallback_from": ["rw-dead-x"]},
        {"role": "planner", "model": op.CHAT_MODEL}]}
    assert op.models_by_role(failover) == {"executor": {op.CHAT_MODEL},
                                           "planner": {op.CHAT_MODEL}}
    assert op.fallbacks_by_role(failover) == {"executor": {"rw-dead-x"}}


def test_onprem_no_chat_template_is_recognised_as_an_environment_limitation() -> None:
    vllm_400 = ('HTTP 400: {"object":"error","message":"As of transformers v4.44, default '
                'chat template is no longer allowed, so you must provide a chat template if '
                'the tokenizer does not define one.","type":"BadRequestError"}')
    assert op.chat_template_missing({"ok": False, "error": vllm_400})
    assert op.chat_template_missing(vllm_400)
    assert not op.chat_template_missing({"ok": False, "error": "ConnectError: refused"})
    assert not op.chat_template_missing({"ok": True, "error": None})
    reason = op.no_chat_template_reason(op.SMALL_MODEL, op.SMALL_URL)
    assert "environment limitation" in reason and op.SMALL_MODEL in reason


def test_onprem_registry_payloads_match_the_api_contract() -> None:
    """Every capability the scenarios register is one the registry accepts."""
    from app.ai_router.models import ModelCapability

    allowed = {c.value for c in ModelCapability}
    for model in op.MODELS.values():
        assert model["model_id"] and model["base_url"].startswith("http")
        assert set(model["capabilities"]) <= allowed, model["capabilities"]
    assert {"ocr", "vision"} <= allowed  # OCR-VISION-FAILOVER registers these


# ── OCR fixtures ────────────────────────────────────────────────────────────


def test_ocr_fixtures_are_deterministic_and_distinct() -> None:
    docs = ocf.readable_docs()
    again = ocf.readable_docs()
    assert [d.data for d in docs] == [d.data for d in again]
    cases = {d.case for d in docs}
    assert {"two-column", "table", "screenshot-jpg", "handwritten", "multipage-3p", "rotated",
            "low-quality"} <= cases
    texts = {d.case: " ".join(d.facts) for d in docs}
    assert ocf.cross_talk(texts, {d.case: d for d in docs}) == []
    assert ocf.cross_talk({"rotated": "driver Wieczorek and DR-3091 GP-77120"},
                          {d.case: d for d in docs}) == []
    assert ocf.cross_talk({"table": "GP-77120"}, {d.case: d for d in docs})


def test_ocr_fixture_shapes() -> None:
    from PIL import Image
    from pypdf import PdfReader

    for d in ocf.readable_docs():
        if d.mime == ocf.PDF:
            reader = PdfReader(io.BytesIO(d.data))
            assert len(reader.pages) == d.pages, d.case
            assert not "".join(p.extract_text() or "" for p in reader.pages).strip(), \
                f"{d.case} has a text layer (must be a scan)"
        else:
            Image.open(io.BytesIO(d.data)).verify()
    assert ocf.readable_plus_noise().pages == 2
    over = ocf.oversize_png(1000)
    assert len(over) == 1001 and over.startswith(b"\x89PNG")
    with pytest.raises(Exception):  # a truncated PNG must not decode
        Image.open(io.BytesIO(ocf.truncated_png())).load()
    with pytest.raises(Exception):  # no renderer can read it
        PdfReader(io.BytesIO(ocf.unrasterisable_pdf())).pages[0].extract_text()
    assert ocf.contains_fact("Status:   OUT  for delivery", "out for delivery")
