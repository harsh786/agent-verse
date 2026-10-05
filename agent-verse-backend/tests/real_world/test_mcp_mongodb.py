"""MCP-MONGO-*: the built-in MongoDB MCP tool connector on the live stack (P1c / A5).

The connector is registered exactly as the UI's catalog flow does it (Connectors ->
Catalog -> MongoDB -> Configure): ``auth_type=connection_string``, ``url=builtin://``,
the DSN in ``auth_config.url``, ``type=builtin-mongodb`` and a *renamed* connection
("orders-db-…"), against the throwaway replica set ``rw-mongo`` as ``rwtool``
(``readWrite`` on ``rw_shop`` only).

Tool calls run through a workflow ``tool`` step (the deterministic path; no LLM):
limit clamps, a read-only aggregate, refused operators, a sanitised error with an
error id. MCP-MONGO-HITL lets a supervised agent delete one document: the call must
pause for a human approval and run only after it.

Environment (else SKIPPED): ``RW_MONGO_ROOT_PASSWORD``, ``RW_MONGO_TOOL_PASSWORD``
(+ ``RW_MONGO_SEED_PORT``); ``RW_SECOND_TENANT_FILE`` for MCP-MONGO-ISOLATION;
``RW_MONGO_MCP_KILL_SWITCH=off`` for MCP-MONGO-KILL-SWITCH.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import workflows as wf
from tests.real_world.helpers import (
    LiveAPI,
    key_from_env,
    mask,
    register_secret,
    tag,
    wait_until,
)
from tests.real_world.metrics import record

DB = "rw_shop"
DOCS = int(os.getenv("RW_MONGO_TOOL_DOCS", "1500"))


def _env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"needs {name}: the throwaway MongoDB replica set (rw-mongo)")
    register_secret(value)
    return value


def _dsn(password: str | None = None) -> str:
    pw = password if password is not None else _env("RW_MONGO_TOOL_PASSWORD")
    return f"mongodb://rwtool:{pw}@rw-mongo:27017/{DB}?replicaSet=rs0&authSource=admin"


@pytest.fixture
def shop() -> Iterator[dict[str, Any]]:
    import pymongo

    client: Any = pymongo.MongoClient(
        f"mongodb://127.0.0.1:{os.getenv('RW_MONGO_SEED_PORT', '57017')}/",
        directConnection=True, serverSelectionTimeoutMS=8000, username="rwroot",
        password=_env("RW_MONGO_ROOT_PASSWORD"), authSource="admin")
    t = tag()
    name = f"orders_{t}"
    cities = ["Pune", "Nagpur", "Kochi", "Guwahati", "Surat"]
    client[DB][name].insert_many([
        {"order_no": f"ORD-{t}-{i:05d}", "city": cities[i % 5],
         "status": ["placed", "shipped", "cancelled"][i % 3], "amount": 100 + i % 900}
        for i in range(DOCS)])
    client[DB][name].insert_one({"order_no": f"ORD-{t}-DEL", "city": "Kochi",
                                 "status": "cancelled_test_order", "amount": 1})
    yield {"client": client, "collection": name, "tag": t}
    with contextlib.suppress(Exception):
        for coll in client[DB].list_collection_names():
            if t in coll:
                client[DB].drop_collection(coll)
        client["rw_other"].drop_collection(f"merged_{t}")
    client.close()


def _catalog_entry(api: LiveAPI) -> dict[str, Any]:
    body = api.json_ok("GET", "/connectors/catalog")
    items = body if isinstance(body, list) else body.get("connectors") or body.get("items") or []
    entry = next((c for c in items if c.get("name") == "mongodb"), None)
    assert entry, "no mongodb entry in GET /connectors/catalog"
    return dict(entry)


def _register(api: LiveAPI, cleanup: Any, name: str, auth_config: dict[str, Any],
              expect: int = 201) -> dict[str, Any]:
    """POST /connectors with the payload the catalog -> Configure form sends."""
    entry = _catalog_entry(api)
    resp = api.post("/connectors", json={
        "name": name, "url": "builtin://", "auth_type": entry["auth_type"],
        "auth_config": auth_config, "auto_approve": False,
        "type": entry.get("builtin_server_id") or "mongodb"})
    assert resp.status_code == expect, (
        f"POST /connectors -> {resp.status_code} (expected {expect}): {mask(resp.text)[:400]}")
    body = resp.json() if resp.content else {}
    if resp.status_code < 300:
        sid = str(body.get("server_id") or body.get("id"))
        cleanup("DELETE", f"/connectors/{sid}")
        body["server_id"] = sid
    body["_http"] = resp.status_code
    return dict(body)


def _no_secret(blob: Any, password: str) -> bool:
    text = json.dumps(blob, default=str)
    return password not in text and "rwtool:" not in text


# ── MCP-MONGO-REGISTER ──────────────────────────────────────────────────────


@pytest.mark.scenario("MCP-MONGO-REGISTER")
def test_mcp_mongo_register_test_list(api: LiveAPI, cleanup: Any,
                                      evidence: dict[str, Any]) -> None:
    password = _env("RW_MONGO_TOOL_PASSWORD")
    entry = _catalog_entry(api)
    evidence["catalog"] = {k: entry.get(k) for k in ("auth_type", "has_builtin",
                                                      "builtin_server_id", "connector_type")}
    soft: list[str] = []
    if entry.get("auth_type") != "connection_string" or not entry.get("has_builtin"):
        soft.append(f"catalog entry is not a built-in connection_string connector: "
                    f"{evidence['catalog']}")
    name = f"orders-db-{tag()}"
    created = _register(api, cleanup, name, {"url": _dsn(), "database": DB})
    sid = created["server_id"]
    evidence["created"] = mask({k: created.get(k) for k in (
        "url", "display_url", "upstream_url", "builtin_type", "auth_type", "auth_config")})
    if not _no_secret(created, password):
        soft.append("the register response carries the password / userinfo")
    display = str(created.get("display_url") or "")
    if not display.startswith("mongodb://rw-mongo:27017") or "@" in display:
        soft.append(f"display_url {display!r} is not the masked host list")
    for path in (f"/connectors/{sid}", "/connectors"):
        body = api.json_ok("GET", path)
        if not _no_secret(body, password):
            soft.append(f"GET {path} carries the password / userinfo")
    one = api.json_ok("GET", f"/connectors/{sid}")
    if str(one.get("builtin_type") or "") not in ("builtin-mongodb", "mongodb"):
        soft.append(f"renamed connection is not bound to the built-in: "
                    f"builtin_type={one.get('builtin_type')!r}")
    if str((one.get("auth_config") or {}).get("url")) != "<redacted>":
        soft.append("auth_config.url is not '<redacted>' in GET")

    started = time.monotonic()
    test = api.json_ok("POST", f"/connectors/{sid}/test")
    evidence["test"] = {**mask(test), "s": round(time.monotonic() - started, 2)} \
        if isinstance(mask(test), dict) else mask(test)
    if test.get("status") != "passed" or not test.get("reachable"):
        soft.append(f"connection test: {mask(test)[:300]}")
    tools = api.json_ok("GET", f"/connectors/{sid}/tools")
    names = sorted(str(t.get("name")) for t in tools)
    evidence["tools"] = names
    expected = {"mongodb_find", "mongodb_find_one", "mongodb_aggregate", "mongodb_count",
                "mongodb_list_collections", "mongodb_insert_one", "mongodb_update_one",
                "mongodb_delete_one"}
    if not expected <= {n.rsplit("__", 1)[-1] for n in names}:
        soft.append(f"tools/list misses {sorted(expected - set(names))}")

    # An edit that echoes the masked values back keeps the sealed DSN working.
    upd = api.request("PUT", f"/connectors/{sid}", json={
        "name": name, "url": "builtin://", "auth_type": "connection_string",
        "auth_config": {**(one.get("auth_config") or {}), "url": "<redacted>",
                        "database": DB}, "auto_approve": False})
    evidence["update_http"] = upd.status_code
    if upd.status_code != 200 or not _no_secret(upd.json(), password):
        soft.append(f"PUT with masked values -> {upd.status_code} {mask(upd.text)[:200]}")
    again = api.json_ok("POST", f"/connectors/{sid}/test")
    if again.get("status") != "passed":
        soft.append(f"connection test after the masked edit: {mask(again)[:200]}")
    assert not soft, "; ".join(soft)


# ── MCP-MONGO-TOOLS (workflow tool steps) ───────────────────────────────────


def _step(step_id: str, server_id: str, tool: str, args: dict[str, Any]) -> str:
    return (f"  - id: {step_id}\n    type: tool\n    tool: {tool}\n    server_id: {server_id}\n"
            f"    on_failure: skip\n    timeout: 120s\n    input: {json.dumps(args)}\n")


def _run_tools(api: LiveAPI, cleanup: Any, server_id: str,
               calls: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    yaml = (f"name: rw-mongo-tools-{tag()}\ndescription: MongoDB tool calls\n"
            "trigger:\n  type: api\nsteps:\n"
            + "".join(_step(sid, server_id, tool, args) for sid, (tool, args) in calls.items()))
    wf_id = wf.import_yaml(api, cleanup, yaml)
    run_id = wf.trigger_run(api, cleanup, wf_id)
    run = wf.wait_status(api, run_id, {"complete", "failed"}, 600)
    steps = wf.get_steps(api, run_id)
    out: dict[str, dict[str, Any]] = {"_run": {"status": run.get("status")}}
    for sid in calls:
        s = steps.get(sid) or {}
        out[sid] = {"status": s.get("status"), "output": wf.step_output(s),
                    "error": s.get("error") or s.get("error_message")}
    return out


def _result(step: dict[str, Any]) -> dict[str, Any]:
    output = step.get("output") or {}
    inner = output.get("output", output) if isinstance(output, dict) else {}
    if isinstance(inner, str):
        with contextlib.suppress(ValueError):
            inner = json.loads(inner)
    return inner if isinstance(inner, dict) else {"raw": inner}


@pytest.mark.scenario("MCP-MONGO-TOOLS")
def test_mcp_mongo_tool_calls(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                              shop: dict[str, Any]) -> None:
    coll, t = shop["collection"], shop["tag"]
    sid = _register(api, cleanup, f"orders-db-{t}", {"url": _dsn(), "database": DB})["server_id"]
    calls: dict[str, tuple[str, dict[str, Any]]] = {
        "find_default": ("mongodb_find", {"collection": coll, "query": {}}),
        "find_5": ("mongodb_find", {"collection": coll, "query": {"city": "Kochi"}, "limit": 5}),
        "find_zero": ("mongodb_find", {"collection": coll, "query": {}, "limit": 0}),
        "find_negative": ("mongodb_find", {"collection": coll, "query": {}, "limit": -5}),
        "find_huge": ("mongodb_find", {"collection": coll, "query": {}, "limit": 50000}),
        "count": ("mongodb_count", {"collection": coll, "query": {"status": "shipped"}}),
        "aggregate": ("mongodb_aggregate", {"collection": coll, "pipeline": [
            {"$match": {"status": "shipped"}},
            {"$group": {"_id": "$city", "orders": {"$sum": 1}, "revenue": {"$sum": "$amount"}}},
            {"$sort": {"_id": 1}}]}),
        "aggregate_out": ("mongodb_aggregate", {"collection": coll, "pipeline": [
            {"$match": {}}, {"$out": f"pwned_out_{t}"}]}),
        "aggregate_merge": ("mongodb_aggregate", {"collection": coll, "pipeline": [
            {"$merge": {"into": {"db": "rw_other", "coll": f"merged_{t}"}}}]}),
        "find_where": ("mongodb_find", {"collection": coll,
                                        "query": {"$where": "sleep(2000) || true"}}),
        "count_function": ("mongodb_count", {"collection": coll, "query": {"$expr": {
            "$function": {"body": "function() { return true }", "args": [], "lang": "js"}}}}),
        "unauthorized_db": ("mongodb_find", {"collection": "orders", "database": "rw_p1c",
                                             "query": {}}),
    }
    started = time.monotonic()
    res = _run_tools(api, cleanup, sid, calls)
    evidence["run_s"] = round(time.monotonic() - started, 1)
    evidence["steps"] = {k: {"status": v.get("status"), "error": mask(v.get("error"))[:240],
                             "result": mask(_result(v))[:240]} for k, v in res.items()
                         if k != "_run"}
    soft: list[str] = []
    expect_counts = {"find_default": 100, "find_5": 5, "find_zero": 1000,
                     "find_negative": 1000, "find_huge": 1000}
    for step_id, n in expect_counts.items():
        got = _result(res[step_id]).get("count")
        if got != n:
            soft.append(f"{step_id}: {got} documents, expected {n}")
    shipped = shop["client"][DB][coll].count_documents({"status": "shipped"})
    if _result(res["count"]).get("count") != shipped:
        soft.append(f"count: {_result(res['count'])} expected {shipped}")
    groups = _result(res["aggregate"]).get("results") or []
    if len(groups) != 5 or sum(int(g.get("orders", 0)) for g in groups) != shipped:
        soft.append(f"aggregate groups wrong: {mask(groups)[:200]}")
    for step_id in ("aggregate_out", "aggregate_merge", "find_where", "count_function"):
        text = json.dumps(res[step_id], default=str).lower()
        if res[step_id].get("status") in ("completed", "complete", "success") and \
                "refused" not in text and "not allowed" not in text:
            soft.append(f"{step_id}: not refused ({mask(res[step_id])[:200]})")
    names = shop["client"][DB].list_collection_names()
    if f"pwned_out_{t}" in names:
        soft.append("$out wrote a collection")
    if f"merged_{t}" in shop["client"]["rw_other"].list_collection_names():
        soft.append("$merge wrote into another database")
    unauth = json.dumps(res["unauthorized_db"], default=str)
    evidence["unauthorized_error"] = mask(unauth)[:300]
    if "error id" not in unauth or "not authorized" not in unauth.lower():
        soft.append(f"unauthorized read: no classified error with an error id: {unauth[:200]}")
    if "TopologyDescription" in unauth or "rwtool:" in unauth:
        soft.append("unauthorized read leaks driver text / credentials")
    record(evidence, tool_steps=len(calls), run_s=evidence["run_s"])
    assert not soft, "; ".join(soft)


# ── MCP-MONGO-ERRORS ────────────────────────────────────────────────────────


@pytest.mark.scenario("MCP-MONGO-ERRORS")
def test_mcp_mongo_errors(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    """Wrong password / hung server: the test answers fast with a classified message
    and an error id; internal hosts and TLS weakening are refused on save."""
    soft: list[str] = []
    out: dict[str, Any] = {}
    bad = _register(api, cleanup, f"orders-db-badpw-{tag()}",
                    {"url": _dsn("wrong-password-123456"), "database": DB})
    started = time.monotonic()
    r = api.json_ok("POST", f"/connectors/{bad['server_id']}/test")
    out["wrong_password"] = {**{k: mask(r.get(k)) for k in ("status", "error")},
                             "s": round(time.monotonic() - started, 1)}
    if r.get("status") != "failed" or "authentication failed" not in str(r.get("error")).lower() \
            or "error id" not in str(r.get("error")):
        soft.append(f"wrong password: {out['wrong_password']}")
    hung = _register(api, cleanup, f"orders-db-hung-{tag()}",
                     {"url": "mongodb://rw-mongo-stall:27017/shop?timeoutMS=0&socketTimeoutMS=0",
                      "database": "shop"})
    started = time.monotonic()
    r = api.json_ok("POST", f"/connectors/{hung['server_id']}/test")
    took = time.monotonic() - started
    out["hung_server"] = {**{k: mask(r.get(k)) for k in ("status", "error")}, "s": round(took, 1)}
    if r.get("status") != "failed" or took > 60 or "error id" not in str(r.get("error")):
        soft.append(f"hung server: {out['hung_server']}")
    refused = {
        "internal postgres": "mongodb://postgres:5432/x",
        "metadata IP": "mongodb://169.254.169.254:27017/x",
        "tlsInsecure": "mongodb://rw-mongo:27017/x?tlsInsecure=true",
        "';' tlsAllowInvalidCertificates": "mongodb://rw-mongo:27017/x?replicaSet=rs0;"
                                           "tlsAllowInvalidCertificates=true",
        "tlsCAFile platform file": "mongodb://rw-mongo:27017/x?tlsCAFile=/etc/passwd",
        "MONGODB-AWS": "mongodb://rw-mongo:27017/x?authMechanism=MONGODB-AWS",
    }
    for name, url in refused.items():
        r2 = api.post("/connectors", json={"name": f"orders-db-ref-{tag()}", "url": "builtin://",
                                           "auth_type": "connection_string",
                                           "auth_config": {"url": url},
                                           "type": "builtin-mongodb"})
        out[name] = r2.status_code
        if r2.status_code < 300:
            cleanup("DELETE", f"/connectors/{r2.json().get('server_id')}")
            soft.append(f"{name}: registered ({r2.status_code})")
    evidence.update(out)
    assert not soft, "; ".join(soft)


# ── MCP-MONGO-ISOLATION ─────────────────────────────────────────────────────


@pytest.mark.scenario("MCP-MONGO-ISOLATION")
def test_mcp_mongo_cross_tenant(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    other_key = key_from_env("RW_SECOND_TENANT_API_KEY", "RW_SECOND_TENANT_FILE")
    if not other_key:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY / RW_SECOND_TENANT_FILE")
    sid = _register(api, cleanup, f"orders-db-{tag()}", {"url": _dsn(), "database": DB})[
        "server_id"]
    other = LiveAPI(other_key)
    try:
        answers = {
            "GET": other.get(f"/connectors/{sid}").status_code,
            "test": other.post(f"/connectors/{sid}/test").status_code,
            "tools": other.get(f"/connectors/{sid}/tools").status_code,
            "health": other.get(f"/connectors/{sid}/health").status_code,
            "PUT": other.request("PUT", f"/connectors/{sid}", json={
                "name": "x", "url": "builtin://", "auth_type": "connection_string",
                "auth_config": {"url": "mongodb://rw-mongo:27017/"}}).status_code,
            "DELETE": other.delete(f"/connectors/{sid}").status_code,
        }
        listed = [c for c in other.json_ok("GET", "/connectors")
                  if str(c.get("server_id")) == sid]
    finally:
        other.close()
    evidence.update(answers=answers, listed_for_other=len(listed))
    assert all(code == 404 for code in answers.values()) and not listed, answers
    assert api.get(f"/connectors/{sid}").status_code == 200, "owner lost the connector"


# ── MCP-MONGO-HITL ──────────────────────────────────────────────────────────


@pytest.mark.scenario("MCP-MONGO-HITL")
def test_mcp_mongo_delete_one_needs_approval(api: LiveAPI, cleanup: Any,
                                             evidence: dict[str, Any],
                                             shop: dict[str, Any]) -> None:
    """A supervised agent deletes one cancelled test order: mongodb_delete_one is
    write_high — it must wait for a human; the document survives until approved."""
    coll, t = shop["collection"], shop["tag"]
    name = f"orders-db-{t}"
    sid = _register(api, cleanup, name, {"url": _dsn(), "database": DB})["server_id"]
    agent = api.json_ok("POST", "/agents", json={
        "name": f"rw-mongo-ops-{t}", "autonomy_mode": "supervised",
        "system_prompt": "You are an order-desk operator. Use the MongoDB connector tools to "
                         "act on the shop database exactly as asked.",
        "max_iterations": 8, "timeout_seconds": 420})
    agent_id = str(agent.get("agent_id") or agent.get("id"))
    cleanup("DELETE", f"/agents/{agent_id}")
    # With grant enforcement (ENFORCE_AGENT_GRANTS) the operator grants the tools.
    grant = api.post("/grants", json={"grantee_agent_id": agent_id, "scopes": [
        "mongodb_find", "mongodb_find_one", "mongodb_count", "mongodb_delete_one"],
        "ttl_seconds": 3600, "max_cost_usd": 5.0})
    evidence["grant_http"] = grant.status_code
    if grant.status_code == 201:
        cleanup("POST", f"/grants/{grant.json()['grant_id']}/revoke")
    order_no = f"ORD-{t}-DEL"
    goal = (f"Using the MongoDB connector '{name}' (database {DB}), delete the single "
            f"cancelled test order with order_no '{order_no}' from the collection '{coll}' "
            "with mongodb_delete_one. Delete nothing else. Report whether it was deleted.")
    body = api.json_ok("POST", "/goals", json={"goal": goal, "agent_id": agent_id})
    goal_id = str(body.get("goal_id") or body.get("id"))
    cleanup("POST", f"/goals/{goal_id}/cancel")
    evidence.update(goal_id=goal_id, server_id=sid)
    col = shop["client"][DB][coll]

    def pending() -> list[dict[str, Any]]:
        return [a for a in api.json_ok("GET", "/governance/approvals") or []
                if a.get("goal_id") == goal_id and a.get("status", "pending") == "pending"]

    def goal_state() -> dict[str, Any]:
        return dict(api.json_ok("GET", f"/goals/{goal_id}"))

    first = wait_until(lambda: pending() or (
        [] if goal_state().get("status") not in ("complete", "failed", "cancelled") else
        [{"terminal": goal_state().get("status")}]), timeout=480, interval=5,
        desc="a pending approval for mongodb_delete_one")
    evidence["approval"] = mask(first[0])[:500]
    soft: list[str] = []
    if first[0].get("terminal"):
        soft.append(f"the goal ended {first[0]['terminal']} without asking for approval")
        assert not soft, "; ".join(soft)
    blob = json.dumps(first[0], default=str)
    if "delete" not in blob.lower():
        soft.append(f"the pending approval is not for the delete: {mask(blob)[:200]}")
    if col.count_documents({"order_no": order_no}) != 1:
        soft.append("the document was deleted BEFORE approval")
    # Approve the delete — and any later gate the plan raises — as an operator would.
    approved: list[str] = []

    def approve_pending() -> dict[str, Any]:
        for item in pending():
            rid = str(item.get("request_id") or item.get("approval_id") or item.get("id"))
            if rid in approved:
                continue
            resp = api.post(f"/governance/approvals/{rid}/approve",
                            json={"note": "Verified cancelled test order", "approver": "rw-p1c"})
            approved.append(rid)
            evidence.setdefault("approvals", []).append(
                {"action": str(item.get("action"))[:160], "http": resp.status_code})
        return goal_state()

    final = wait_until(approve_pending, timeout=600, interval=5, desc="goal to finish",
                       done=lambda g: g.get("status") in ("complete", "failed", "cancelled"))
    evidence["goal_status"] = final.get("status")
    left = col.count_documents({"order_no": order_no})
    others = col.count_documents({"order_no": {"$ne": order_no}})
    evidence.update(target_left=left, others_left=others)
    if left != 0:
        soft.append("the approved delete did not remove the document")
    if others != DOCS:
        soft.append(f"{DOCS - others} other documents were deleted")
    assert not soft, "; ".join(soft)


# ── MCP-MONGO-KILL-SWITCH ───────────────────────────────────────────────────


@pytest.mark.scenario("MCP-MONGO-KILL-SWITCH")
def test_mcp_mongo_kill_switch(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    if os.getenv("RW_MONGO_MCP_KILL_SWITCH") != "off":
        pytest.skip("run with RW_MONGO_MCP_KILL_SWITCH=off against a stack started with "
                    "MCP_CONNECTOR_MONGODB_ENABLED=false (RW_MONGO_KILL_CONNECTOR_ID = a "
                    "connection registered before the switch)")
    soft: list[str] = []
    r = api.post("/connectors", json={"name": f"orders-db-{tag()}", "url": "builtin://",
                                      "auth_type": "connection_string",
                                      "auth_config": {"url": _dsn(), "database": DB},
                                      "type": "builtin-mongodb"})
    evidence["register"] = (r.status_code, mask(r.text)[:200])
    if r.status_code < 300:
        cleanup("DELETE", f"/connectors/{r.json().get('server_id')}")
    if r.status_code != 422 or "disabled" not in r.text.lower():
        soft.append(f"register -> {r.status_code} {mask(r.text)[:160]}")
    existing = os.getenv("RW_MONGO_KILL_CONNECTOR_ID", "")
    if existing:
        test = api.json_ok("POST", f"/connectors/{existing}/test")
        evidence["test"] = mask(test)
        if test.get("status") != "failed" or "disabled" not in json.dumps(test).lower():
            soft.append(f"test of an existing connection: {mask(test)[:200]}")
        res = _run_tools(api, cleanup, existing, {"find": ("mongodb_find", {
            "collection": "orders", "query": {}, "limit": 1})})
        evidence["tool_call"] = mask(res["find"])[:300]
        if "disabled" not in json.dumps(res["find"], default=str).lower():
            soft.append(f"tool call not refused as disabled: {mask(res['find'])[:200]}")
    assert not soft, "; ".join(soft)
