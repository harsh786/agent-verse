"""ONPREM-*: on-prem vLLM models configured BY HAND through the Model Registry API.

Exactly what an operator does in Settings -> Models: register each on-prem model with
its own ``base_url`` (``POST /models/configured``), press "Test connection"
(``POST /models/configured/test-endpoint``), put it first in the capability's
preference order (``PUT /models/preferences/{capability}``) and pin the agent roles
(``PUT /models/routing-policies/{planning|execution|verification}``). The models are
REAL (``onprem.py``: Qwen3.5-4B chat, Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B,
gemma-4-E2B with a 1,024-token context); outages are real too — a closed port on the
same host, an unroutable address. Nothing is mocked.

The registry is global and platform-admin only: the scenarios need a platform-admin
tenant key or ``RW_PLATFORM_ADMIN_KEY`` (SKIPPED otherwise), and every change is
restored in a ``finally`` (``onprem.RegistrySandbox``).

* ONPREM-REGISTER — four models registered, each "Test connection" passes with the right
  probe (chat reply / 1024-dim embedding / documents scored), the chat model ranks first.
* ONPREM-REASONING-GOAL — a multi-step arithmetic + retrieval goal over a small KB: the
  answer is right, it cites the KB, and ``role_calls`` prove the on-prem model served
  planner, executor and verifier — no cloud model was called.
* ONPREM-EMBEDDER — the stack embeds with the on-prem embedder (1024 dims) end to end
  (SKIPPED with the switching steps when the deployment's one global EMBEDDING_DIM differs).
* ONPREM-TEST-CONNECTION-FAILURES — wrong base_url, endpoint down, unroutable address, wrong
  model id: "Test connection" fails honestly and fast.
* ONPREM-ROUTING-FAILOVER — a dead preferred model (closed port, then unroutable): goals
  fail over to the next eligible model per the routing precedence (or fail honestly); the
  role record names the model that SERVED each call and the dead one in ``fallback_from``.
* ONPREM-PREFERENCE-CHANGE — removing the on-prem model from the order changes routing for
  the very next goal.
* ONPREM-CONTEXT-OVERFLOW — a prompt beyond gemma-4-E2B's 1,024 tokens: handled (honest
  failure or failover), never a 500 or a hung run. SKIPPED when the cluster serves gemma
  without a chat template (vLLM 400 "default chat template is no longer allowed").
* ONPREM-EMBED-DIMENSION — an embedder whose dimension differs from the index is marked
  refused and never becomes the active embedder; a collection never mixes dimensions.
* ONPREM-ISOLATION — tenant-scoped routing policies stay per tenant; a non-admin tenant
  cannot change the global registry.

Environment: ``RW_ONPREM_CHAT_URL``, ``RW_ONPREM_EMBED_URL``, ``RW_ONPREM_RERANK_URL``,
``RW_ONPREM_SMALL_URL``, ``RW_ONPREM_PROVIDER`` (default ``onprem``),
``RW_PLATFORM_ADMIN_KEY``, ``RW_SECOND_TENANT_*`` (ISOLATION), ``RW_GOAL_TIMEOUT``.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import onprem as op
from tests.real_world import sources as srcs
from tests.real_world import wf_mongo as wfm
from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, env_float, mask, tag
from tests.real_world.metrics import norm, record

GOAL_TIMEOUT = env_float("RW_GOAL_TIMEOUT", 480)
ROLES = ("planning", "execution", "verification")
TRACE_ROLES = ("planner", "executor", "verifier")

CAPACITY_DOC = (
    "Capacity ledger, October 2026 (rw-capacity-ledger). WH-Hosur: rated capacity 1,240 "
    "pallet positions; occupied on 1 October: 905. October movements at WH-Hosur: inbound "
    "310 pallets, outbound 180 pallets. WH-Pune: rated capacity 860 pallet positions; "
    "occupied on 1 October: 610. October movements at WH-Pune: inbound 95 pallets, outbound "
    "140 pallets.")
DISTRACTOR_DOC = (
    "Fleet notes (rw-fleet-notes). The Hosur reefer trucks were serviced on 12 October; "
    "the Pune yard repainted bay markings. Neither note changes pallet capacity.")
REASONING_GOAL = (
    "Using only the knowledge base, compute the free pallet positions at WH-Hosur and at "
    "WH-Pune at the end of October (free = rated capacity - (occupied on 1 October + "
    "inbound - outbound)). Show the arithmetic, say which warehouse has more free positions "
    "and cite the knowledge-base document you used.")


@pytest.fixture(scope="module")
def registry(api: LiveAPI) -> Iterator[dict[str, Any]]:
    """The four on-prem models registered and connection-tested (restored at the end)."""
    admin = op.admin_client(api)
    op.require_registry_admin(admin)
    box = op.RegistrySandbox(admin).__enter__()
    try:
        regs = {name: box.register(m) for name, m in op.MODELS.items()}
        tests = {name: op.test_endpoint(admin, m) for name, m in op.MODELS.items()}
        yield {"admin": admin, "box": box, "registered": regs, "tests": tests}
    finally:
        box.__exit__(None, None, None)
        if admin is not api:
            admin.close()


def _goal(api: LiveAPI, cleanup: Any, text: str, **extra: Any) -> dict[str, Any]:
    body = api.json_ok("POST", "/goals", json={"goal": text, **extra})
    gid = str(body.get("goal_id") or body.get("id"))
    cleanup("POST", f"/goals/{gid}/cancel")
    started = time.monotonic()
    g = op.wait_goal(api, gid, GOAL_TIMEOUT)
    g["_s"] = round(time.monotonic() - started, 1)
    g["_id"] = gid
    return g


def _served(api: LiveAPI, goal: dict[str, Any]) -> dict[str, set[str]]:
    with contextlib.suppress(Exception):
        return op.models_by_role(op.role_trace(api, goal["_id"]))
    return {}


# ── ONPREM-REGISTER ─────────────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-REGISTER")
def test_register_and_test_connection(api: LiveAPI, registry: dict[str, Any],
                                      evidence: dict[str, Any]) -> None:
    admin = registry["admin"]
    evidence.update(registered={k: v.get("_http") for k, v in registry["registered"].items()},
                    tests={k: {f: v.get(f) for f in ("ok", "probe", "model_listed", "detail",
                                                    "error", "latency_ms", "_s")}
                           for k, v in registry["tests"].items()})
    soft: list[str] = []
    for name, reg in registry["registered"].items():
        if reg.get("_http") not in (200, 201):
            soft.append(f"register {name} -> {reg}")
    want_probe = {"chat": "chat", "small": "chat", "embed": "embedding", "rerank": "rerank"}
    # gemma-4-E2B served without a chat template cannot answer the chat probe:
    # an environment limitation (reported honestly), not a platform failure.
    small_no_template = op.chat_template_missing(registry["tests"]["small"])
    evidence["small_no_chat_template"] = small_no_template
    for name, res in registry["tests"].items():
        if name == "small" and small_no_template:
            continue
        if not res.get("ok"):
            soft.append(f"Test connection of {name} failed: {mask(res.get('error'))[:160]}")
        if res.get("probe") != want_probe[name]:
            soft.append(f"{name}: probe {res.get('probe')!r}, expected {want_probe[name]!r}")
        if res.get("model_listed") is False:
            soft.append(f"{name}: the endpoint does not list {op.MODELS[name]['model_id']}")
    if f"{op.EMBED_DIM}-dimension" not in str(registry["tests"]["embed"].get("detail")):
        soft.append(f"embedding probe: {registry['tests']['embed'].get('detail')!r}")
    with op.RegistrySandbox(admin) as box:
        r = box.set_order("text_generation", [op.key(op.MODELS["chat"])] + [
            k for k in box.prefs_before.get("text_generation", [])
            if k != op.key(op.MODELS["chat"])])
        rr = box.set_order("rerank", [op.key(op.MODELS["rerank"])])
        group = op.capability_group(api, "text_generation")
        mine = next((m for m in group.get("models") or []
                     if m.get("model_id") == op.CHAT_MODEL), {})
        evidence.update(order=r, rerank_order=rr, selected=group.get("selected_model_id"),
                        chat_entry={k: mine.get(k) for k in ("provider_ready", "is_available",
                                                            "base_url", "rank")})
        if r.get("_http") != 200 or rr.get("_http") != 200:
            soft.append(f"preference orders refused: {r} {rr}")
        if group.get("selected_model_id") != op.CHAT_MODEL:
            soft.append(f"text_generation selects {group.get('selected_model_id')!r}, not the "
                        "on-prem model placed first")
        if not mine.get("provider_ready"):
            soft.append("the on-prem chat model is not provider_ready")
        if str(mine.get("base_url") or "").rstrip("/") != op.CHAT_URL:
            soft.append(f"base_url stored as {mine.get('base_url')!r}")
        pins = {role: box.pin_role(role, op.CHAT_MODEL) for role in ROLES}
        evidence["pins"] = pins
        for role, res in pins.items():
            if res.get("_http") != 200 or not res.get("enforced"):
                soft.append(f"routing pin {role}: {res}")
    assert not soft, "; ".join(soft)
    if small_no_template:
        pytest.skip("everything else passed; Test connection of the small model not "
                    "checked: " + op.no_chat_template_reason(op.SMALL_MODEL, op.SMALL_URL))


# ── ONPREM-REASONING-GOAL ───────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-REASONING-GOAL")
def test_reasoning_goal_served_on_prem(api: LiveAPI, cleanup: Any, registry: dict[str, Any],
                                       embedder_info: dict[str, Any],
                                       evidence: dict[str, Any]) -> None:
    if not registry["tests"]["chat"].get("ok"):
        pytest.fail(f"the on-prem chat model is unreachable: {registry['tests']['chat']}")
    t = tag()
    cid = srcs.create_collection(api, cleanup, "rw-onprem-kb")
    for title, text in (("rw-capacity-ledger", CAPACITY_DOC), ("rw-fleet-notes", DISTRACTOR_DOC)):
        api.json_ok("POST", "/knowledge/ingest", json={
            "collection_id": cid, "source_type": "text", "content": text,
            "metadata": {"title": title, "source_file": f"{title}.txt"}})
    agent = api.json_ok("POST", "/agents", json={
        "name": f"rw-onprem-analyst-{t}", "autonomy_mode": "bounded-autonomous",
        "allowed_collection_ids": [cid], "max_iterations": 10, "timeout_seconds": 420,
        "system_prompt": "You are a warehouse capacity analyst. Use the knowledge base; "
                         "show your arithmetic; cite the document."})
    agent_id = str(agent.get("agent_id") or agent.get("id"))
    cleanup("DELETE", f"/agents/{agent_id}")
    api.put(f"/agents/{agent_id}/knowledge", json={"collection_ids": [cid]})
    evidence.update(collection_id=cid, agent_id=agent_id, embedder=embedder_info)
    soft: list[str] = []
    with op.RegistrySandbox(registry["admin"]) as box:
        box.set_order("text_generation", [op.key(op.MODELS["chat"])])
        for role in ROLES:
            box.pin_role(role, op.CHAT_MODEL)
        goal = _goal(api, cleanup, REASONING_GOAL, agent_id=agent_id)
        trace = op.role_trace(api, goal["_id"]) if goal.get("status") in (
            "complete", "completed") else {}
    served = op.models_by_role(trace)
    answer = op.goal_text(goal)
    every = {m for ms in served.values() for m in ms}
    evidence.update(goal_id=goal["_id"], status=goal.get("status"), seconds=goal["_s"],
                    answer=answer[:600], served={k: sorted(v) for k, v in served.items()},
                    role_calls=mask(trace.get("role_calls"))[:800],
                    total_cost_usd=trace.get("total_cost_usd"))
    if goal.get("status") not in ("complete", "completed"):
        soft.append(f"the reasoning goal ended {goal.get('status')}: "
                    f"{mask(goal.get('failure_reason') or goal.get('error'))[:200]}")
    for needed in ("205", "295", "pune"):
        if needed not in norm(answer):
            soft.append(f"answer lacks {needed!r} (Hosur 205, Pune 295 free; Pune has more)")
    if "capacity-ledger" not in norm(json.dumps(goal, default=str)):
        soft.append("the goal does not cite the rw-capacity-ledger document")
    for role in TRACE_ROLES:
        models = served.get(role, set())
        if not models:
            soft.append(f"no {role} call recorded in role_calls")
        elif models != {op.CHAT_MODEL}:
            soft.append(f"{role} served by {sorted(models)}, expected only {op.CHAT_MODEL}")
    cloud = op.cloud_models(every)
    if cloud:
        soft.append(f"cloud models were called: {cloud}")
    record(evidence, goal_s=goal["_s"], roles_on_prem=sum(
        1 for r in TRACE_ROLES if served.get(r) == {op.CHAT_MODEL}),
        cloud_calls=len(cloud), cost_usd=float(trace.get("total_cost_usd") or 0))
    assert not soft, "; ".join(soft)


# ── ONPREM-EMBEDDER ─────────────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-EMBEDDER")
def test_stack_embeds_with_onprem_model(api: LiveAPI, cleanup: Any,
                                        embedder_info: dict[str, Any],
                                        evidence: dict[str, Any]) -> None:
    evidence["embedder"] = embedder_info
    if "qwen3-embedding" not in str(embedder_info.get("model") or "").lower():
        group = op.capability_group(api, "embedding")
        active = group.get("active_embedder") or {}
        row = next((m for m in group.get("models") or []
                    if m.get("model_id") == op.EMBED_MODEL), {})
        evidence.update(active_embedder=active, onprem_row={
            k: row.get(k) for k in ("key", "base_url", "dimensions", "index_dimension",
                                    "dimension_mismatch", "refused", "refusal_reason")})
        pytest.skip(_embedder_skip_reason(embedder_info, active, row))
    soft: list[str] = []
    if int(embedder_info.get("dimension") or 0) != op.EMBED_DIM:
        soft.append(f"active embedder dimension {embedder_info.get('dimension')}")
    body = api.json_ok("POST", "/knowledge/collections", json={
        "name": f"rw-onprem-embed-{tag()}", "embedder_type": op.EMBED_MODEL})
    cid = str(body.get("collection_id"))
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    if int(body.get("embedding_dim") or 0) != op.EMBED_DIM:
        soft.append(f"collection embedding_dim {body.get('embedding_dim')}")
    api.json_ok("POST", "/knowledge/ingest", json={"collection_id": cid, "source_type": "text",
                                                   "content": CAPACITY_DOC})
    hits = api.json_ok("GET", "/knowledge/search", params={
        "q": "How many pallet positions is WH-Hosur rated for?", "collection_id": cid,
        "top_k": 3})
    if not any("1,240" in str(h.get("content")) for h in hits):
        soft.append("on-prem embedded content not retrieved")
    health = api.get(f"/embeddings/health/{cid}")
    evidence["embedding_health"] = mask(health.json() if health.status_code == 200 else
                                        health.status_code)[:400]
    group = op.capability_group(api, "embedding")
    evidence["embedding_group"] = {k: group.get(k) for k in ("selected_model_id",
                                                             "fallback_model_ids", "order_mode")}
    assert not soft, "; ".join(soft)


def _embedder_skip_reason(info: dict[str, Any], active: dict[str, Any],
                          row: dict[str, Any]) -> str:
    """Why the stack does not embed with the on-prem model — the current truth.

    A per-model ``base_url`` IS honoured for embeddings (a36df92a1). What blocks
    the on-prem embedder is the vector width: the deployment has ONE global
    ``EMBEDDING_DIM`` (one pgvector chunk table per width; the knowledge store,
    collections and memory all use it), and a model whose width differs is
    refused. EMBEDDING_DIM is per deployment, never per tenant.
    """
    using = (f"the stack embeds with {info.get('model') or active.get('model')!r} "
             f"({info.get('dimension') or active.get('dimension')}-d)")
    switch = (f"To run this scenario use a dedicated deployment (EMBEDDING_DIM is global "
              f"per deployment, not per tenant): set EMBEDDING_BASE_URL={op.EMBED_URL} "
              f"EMBEDDING_MODEL={op.EMBED_MODEL} EMBEDDING_DIM={op.EMBED_DIM} (an explicit "
              "EMBEDDING_BASE_URL stops the NVIDIA embedder from overriding it; a 1024-d "
              "chunk table exists), restart the API and every worker, put "
              f"{op.PROVIDER}/{op.EMBED_MODEL} first in the embedding preference order, then "
              "re-embed existing collections (POST /knowledge/collections/{id}/re-embed) or "
              "start from fresh volumes")
    if row.get("dimension_mismatch") or row.get("refused"):
        return (f"{using}: the deployment has ONE global EMBEDDING_DIM "
                f"({row.get('index_dimension')}) and {op.EMBED_MODEL} returns "
                f"{row.get('dimensions') or op.EMBED_DIM}-d vectors, so the registry refuses "
                "it for embeddings (its per-model base_url is honoured; the width is the "
                f"blocker). {switch}")
    if not row:
        return (f"{using}: {op.EMBED_MODEL} is not registered for embeddings (register it "
                f"with base_url {op.EMBED_URL} and press Test connection). {switch}")
    return (f"{using}: {op.EMBED_MODEL} is registered but not the active embedder "
            f"(registry_refusal: {active.get('registry_refusal')!r}). {switch}")


# ── ONPREM-TEST-CONNECTION-FAILURES ─────────────────────────────────────────


def _closed_port(url: str, port: int = 30099) -> str:
    from urllib.parse import urlsplit

    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}:{port}{u.path}"


@pytest.mark.scenario("ONPREM-TEST-CONNECTION-FAILURES")
def test_test_connection_failures_are_honest(api: LiveAPI, registry: dict[str, Any],
                                             evidence: dict[str, Any]) -> None:
    admin = registry["admin"]
    chat = op.MODELS["chat"]
    cases = {
        "wrong base_url (closed port)": ({"base_url": _closed_port(op.CHAT_URL)}, 40),
        "endpoint down (wrong path)": ({"base_url": op.CHAT_URL.rsplit("/v1", 1)[0] +
                                        "/nope/v1"}, 40),
        "unroutable address": ({"base_url": "http://10.255.255.1:8000/v1"}, 45),
        "wrong model id": ({"model_id": "Qwen/Qwen3.5-4B-does-not-exist"}, 40),
        "embedding probe at the chat port": ({"model_id": op.EMBED_MODEL,
                                              "base_url": op.CHAT_URL,
                                              "capabilities": ["embedding"]}, 40),
    }
    soft: list[str] = []
    out: dict[str, Any] = {}
    for name, (over, bound) in cases.items():
        res = op.test_endpoint(admin, chat, **over)
        out[name] = {k: res.get(k) for k in ("_http", "ok", "model_listed", "error", "_s")}
        if res.get("_http") >= 500:
            soft.append(f"{name}: HTTP {res.get('_http')}")
        if res.get("ok"):
            soft.append(f"{name}: Test connection passed")
        elif not str(res.get("error") or "").strip():
            soft.append(f"{name}: failed without saying why")
        if res.get("_s", 0) > bound:
            soft.append(f"{name}: took {res.get('_s')}s (bound {bound}s)")
    if out["wrong model id"].get("model_listed") is not False:
        soft.append("a missing model id is not reported as not listed")
    evidence["cases"] = mask(out)
    assert not soft, "; ".join(soft)


# ── ONPREM-ROUTING-FAILOVER ─────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-ROUTING-FAILOVER")
@pytest.mark.parametrize("outage", ["closed-port", "unroutable"])
def test_dead_preferred_model_fails_over(api: LiveAPI, cleanup: Any, registry: dict[str, Any],
                                         evidence: dict[str, Any], outage: str) -> None:
    url = _closed_port(op.CHAT_URL) if outage == "closed-port" else \
        "http://10.255.255.1:8000/v1"
    dead = {**op.MODELS["chat"], "model_id": f"rw-dead-{outage}-{tag()}", "base_url": url,
            "display_name": f"dead chat model ({outage})"}
    soft: list[str] = []
    with op.RegistrySandbox(registry["admin"]) as box:
        reg = box.register(dead)
        assert reg["_http"] < 300, f"register the dead entry -> {reg}"
        box.set_order("text_generation", [op.key(dead), op.key(op.MODELS["chat"])])
        for role in ROLES:  # the pin is the highest-precedence choice: pin the dead one
            box.pin_role(role, dead["model_id"])
        goal = _goal(api, cleanup, "Compute 12 multiplied by 12 and reply with just the "
                                   "number. Do not use tools.")
        trace: dict[str, Any] = {}
        if goal.get("status") in ("complete", "completed"):
            with contextlib.suppress(Exception):
                trace = op.role_trace(api, goal["_id"])
    served = op.models_by_role(trace)
    fell_back = op.fallbacks_by_role(trace)
    every = {m for ms in served.values() for m in ms}
    evidence.update(outage=outage, goal_id=goal["_id"], status=goal.get("status"),
                    seconds=goal["_s"], answer=op.goal_text(goal)[:120],
                    served={k: sorted(v) for k, v in served.items()},
                    fallback_from={k: sorted(v) for k, v in fell_back.items()},
                    error=mask(goal.get("failure_reason") or goal.get("error"))[:240],
                    terminal_reason=goal.get("terminal_reason"), registry_log=box.log)
    if goal.get("status") in ("complete", "completed"):
        if dead["model_id"] in every:
            soft.append("the unreachable model is recorded as having served the goal")
        if not any(dead["model_id"] in ms for ms in fell_back.values()):
            soft.append("role_calls do not record the failover from the unreachable model "
                        "(fallback_from)")
        if not op.goal_text(goal).strip():
            soft.append("the goal is complete with an empty answer")
        if op.CHAT_MODEL not in every:
            soft.append(f"failover went to {sorted(every)}, not the next model in the order "
                        f"({op.CHAT_MODEL})")
        if "144" not in op.goal_text(goal):
            soft.append(f"wrong answer after failover: {op.goal_text(goal)[:80]!r}")
    else:
        err = str(goal.get("failure_reason") or goal.get("error") or "")
        soft.append(f"no failover to the next eligible model: the goal ended "
                    f"{goal.get('status')} ({goal.get('terminal_reason')}: {mask(err)[:160]})")
    if goal["_s"] > GOAL_TIMEOUT * 0.9:
        soft.append(f"the goal needed {goal['_s']}s against a dead model")
    record(evidence, goal_s=goal["_s"])
    assert not soft, "; ".join(soft)


# ── ONPREM-PREFERENCE-CHANGE ────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-PREFERENCE-CHANGE")
def test_removing_model_from_order_changes_routing(api: LiveAPI, cleanup: Any,
                                                   registry: dict[str, Any],
                                                   evidence: dict[str, Any]) -> None:
    group = op.capability_group(api, "text_generation")
    ours = {op.key(m) for m in op.MODELS.values()}
    others = [str(m.get("key")) for m in group.get("models") or []
              if str(m.get("key")) not in ours and m.get("provider_ready")]
    if not others:
        pytest.skip("no other provider-ready text_generation model is configured: routing "
                    "cannot move away from the on-prem model")
    goal_text = "Compute 9 multiplied by 8 and reply with just the number. Do not use tools."
    soft: list[str] = []
    with op.RegistrySandbox(registry["admin"]) as box:
        box.set_order("text_generation", [op.key(op.MODELS["chat"]), *others])
        g1 = _goal(api, cleanup, goal_text)
        s1 = _served(api, g1)
        box.set_order("text_generation", others)
        g2 = _goal(api, cleanup, goal_text)
        s2 = _served(api, g2)
    m1 = {m for ms in s1.values() for m in ms}
    m2 = {m for ms in s2.values() for m in ms}
    evidence.update(first={"goal": g1["_id"], "models": sorted(m1), "status": g1.get("status")},
                    second={"goal": g2["_id"], "models": sorted(m2),
                            "status": g2.get("status")}, others=others[:3])
    if op.CHAT_MODEL not in m1:
        soft.append(f"with the on-prem model first the goal ran on {sorted(m1)}")
    if op.CHAT_MODEL in m2:
        soft.append("after removing the on-prem model from the order the next goal still "
                    "used it")
    for g in (g1, g2):
        if g.get("status") not in ("complete", "completed"):
            soft.append(f"goal {g['_id']} ended {g.get('status')}")
    assert not soft, "; ".join(soft)


# ── ONPREM-CONTEXT-OVERFLOW ─────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-CONTEXT-OVERFLOW")
def test_prompt_beyond_small_model_context(api: LiveAPI, cleanup: Any, registry: dict[str, Any],
                                           evidence: dict[str, Any]) -> None:
    small = registry["tests"]["small"]
    if op.chat_template_missing(small):
        # The registry has no per-model context window to shrink Qwen's 32,768
        # tokens for this check (POST /models/configured does not take one), so
        # there is no other model with a small window to overflow.
        evidence["small_test_connection"] = mask(small.get("error"))[:240]
        pytest.skip(op.no_chat_template_reason(op.SMALL_MODEL, op.SMALL_URL) + "; the "
                    "registry has no per-model context_window, so the overflow cannot be "
                    "run against another model with a deliberately small window instead")
    if not small.get("ok"):
        pytest.fail(f"gemma-4-E2B is unreachable: {small}")
    filler = " ".join(f"Line {i}: pallet {i} moved from bay {i % 40} to bay {(i * 7) % 40}."
                      for i in range(400))  # ~6,000 tokens, far beyond 1,024
    prompt = f"{filler}\nHow many lines are listed above? Reply with the number only."
    wf_id = wfx.import_yaml(api, cleanup, wfm.llm_step_yaml(
        f"rw-onprem-overflow-{tag()}", model=op.SMALL_MODEL, prompt=prompt, timeout="180s"))
    resp = api.post(f"{wfx.V1}/workflows/{wf_id}/trigger", json={"inputs": {}})
    evidence["trigger_http"] = resp.status_code
    soft: list[str] = []
    if resp.status_code >= 500:
        soft.append(f"triggering the overflowing prompt answered {resp.status_code}")
        assert not soft, "; ".join(soft)
    run_id = str(resp.json().get("run_id"))
    cleanup("POST", f"{wfx.V1}/runs/{run_id}/cancel")
    run = wfx.wait_status(api, run_id, {"complete", "failed"}, 420)
    steps = wfx.get_steps(api, run_id)
    err = str(run.get("error") or (steps.get("ask") or {}).get("error") or "")
    evidence.update(run_id=run_id, status=run.get("status"), error=mask(err)[:300],
                    output=mask(wfx.output_of(steps, "ask"))[:200])
    if run.get("status") not in ("complete", "failed"):
        soft.append(f"the run did not end: {run.get('status')}")
    if run.get("status") == "failed" and op.chat_template_missing(err):
        pytest.skip(op.no_chat_template_reason(op.SMALL_MODEL, op.SMALL_URL))
    if run.get("status") == "failed":
        if not any(w in err.lower() for w in ("context", "length", "token", "maximum", "too long",
                                              "exceed")):
            soft.append(f"failure does not say the prompt exceeds the context: {err[:200]}")
        if "Traceback" in err:
            soft.append("raw traceback in the run error")
    assert not soft, "; ".join(soft)


# ── ONPREM-EMBED-DIMENSION ──────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-EMBED-DIMENSION")
def test_embedding_dimension_never_mixed(api: LiveAPI, cleanup: Any, registry: dict[str, Any],
                                         embedder_info: dict[str, Any],
                                         evidence: dict[str, Any]) -> None:
    group = op.capability_group(api, "embedding")
    rows = [{k: m.get(k) for k in ("key", "model_id", "dimensions", "dimension_mismatch",
                                   "index_dimension", "refused", "selected")}
            for m in group.get("models") or []]
    evidence.update(group_selected=group.get("selected_model_id"), models=rows,
                    active=embedder_info, active_embedder=group.get("active_embedder"))
    soft: list[str] = []
    mismatched = {str(r["key"]) for r in rows if r.get("dimension_mismatch")}
    mismatched_ids = {str(r["model_id"]) for r in rows if r.get("dimension_mismatch")}

    def _check_active(g: dict[str, Any], when: str) -> None:
        """The embedder that really embeds fits the index and is never a refused one."""
        act = g.get("active_embedder") or {}
        index_dim = next((r.get("index_dimension") for r in g.get("models") or []
                          if r.get("index_dimension")), None)
        if act.get("model") in mismatched_ids:
            soft.append(f"{when}: the active embedder {act.get('model')} has the wrong "
                        "dimension")
        if index_dim and act.get("dimension") and int(act["dimension"]) != int(index_dim):
            soft.append(f"{when}: active embedder {act.get('dimension')}-d, index "
                        f"{index_dim}-d")
        for r in g.get("models") or []:
            if r.get("dimension_mismatch") and not r.get("refused"):
                soft.append(f"{when}: {r.get('key')} has a dimension mismatch but is not "
                            "marked refused")
            if r.get("dimension_mismatch") and r.get("selected"):
                soft.append(f"{when}: refused {r.get('key')} is presented as selected")
        if g.get("selected_model_id") in mismatched_ids:
            soft.append(f"{when}: the listing presents refused {g.get('selected_model_id')} "
                        "as the selected embedder")

    _check_active(group, "listing")
    if mismatched:
        with op.RegistrySandbox(registry["admin"]) as box:
            res = box.set_order("embedding", [sorted(mismatched)[0]])
            after = op.capability_group(api, "embedding")
        evidence["mismatched_first"] = {"order": res, "selected": after.get("selected_model_id"),
                                        "active_embedder": after.get("active_embedder")}
        if res.get("_http") == 200:
            _check_active(after, "with the mismatched embedder first")
    active = str(embedder_info.get("model") or "")
    resp = api.post("/knowledge/collections", json={"name": f"rw-dim-{tag()}",
                                                    "embedder_type": op.EMBED_MODEL})
    evidence["create_with_onprem_embedder"] = {"http": resp.status_code,
                                               "body": mask(resp.text)[:240]}
    if resp.status_code in (200, 201):
        cid = str(resp.json().get("collection_id"))
        cleanup("DELETE", f"/knowledge/collections/{cid}")
        if op.EMBED_MODEL.lower() not in active.lower() and "default" not in active:
            soft.append(f"a collection was created for {op.EMBED_MODEL} although the stack "
                        f"embeds with {active!r} (silent dimension mixing risk)")
    elif resp.status_code != 422:
        soft.append(f"creating a collection for another embedder answered {resp.status_code}")
    cid = srcs.create_collection(api, cleanup, "rw-dim-check")
    api.json_ok("POST", "/knowledge/ingest", json={"collection_id": cid, "source_type": "text",
                                                   "content": CAPACITY_DOC})
    health = api.get(f"/embeddings/health/{cid}")
    evidence["collection_embedding_health"] = mask(health.json() if health.status_code == 200
                                                   else health.status_code)[:400]
    assert not soft, "; ".join(soft)


# ── ONPREM-ISOLATION ────────────────────────────────────────────────────────


@pytest.mark.scenario("ONPREM-ISOLATION")
def test_registry_tenant_scoping(api: LiveAPI, registry: dict[str, Any],
                                 second_tenant_api: LiveAPI | None,
                                 evidence: dict[str, Any]) -> None:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE")
    other = second_tenant_api
    soft: list[str] = []
    access = other.get("/models/configured/access")
    other_is_admin = bool(access.status_code == 200 and access.json().get("can_modify"))
    put_code: int | None = None
    reg_code: int | None = None
    with op.RegistrySandbox(registry["admin"]) as box:
        pin = box.pin_role("planning", op.CHAT_MODEL)
        theirs = other.get("/models/routing-policies")
        policies = theirs.json().get("policies", []) if theirs.status_code == 200 else []
        leaked = [p for p in policies if p.get("task_type") == "planning"
                  and p.get("preferred_model") == op.CHAT_MODEL]
        if not other_is_admin:  # a platform admin may write the global registry by design
            put_code = other.put("/models/preferences/text_generation",
                                 json={"order": [op.key(op.MODELS["small"])]}).status_code
            reg = other.post("/models/configured", json={
                "provider": op.PROVIDER, "model_id": f"rw-intruder-{tag()}",
                "capabilities": ["text_generation"], "base_url": op.CHAT_URL})
            reg_code = reg.status_code
            if reg_code < 300:
                with contextlib.suppress(Exception):
                    box.admin.delete(f"/models/configured/{op.PROVIDER}/"
                                     f"{reg.json().get('model_id')}")
            if put_code < 300:
                box.touched_prefs.add("text_generation")  # restored on exit
    evidence.update(pin=pin, other_policies_http=theirs.status_code, leaked=len(leaked),
                    other_is_platform_admin=other_is_admin, other_put_preferences=put_code,
                    other_register=reg_code)
    if leaked:
        soft.append("tenant A's routing pin is visible as tenant B's policy")
    if put_code is not None and put_code < 300:
        soft.append("a non-admin tenant changed the global preference order")
    if reg_code is not None and reg_code < 300:
        soft.append("a non-admin tenant registered a model in the global registry")
    for code in (put_code, reg_code):
        if code is not None and code >= 500:
            soft.append(f"registry refusal answered {code}")
    assert not soft, "; ".join(soft)
