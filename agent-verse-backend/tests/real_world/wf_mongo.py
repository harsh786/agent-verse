"""Workflow definitions (YAML) of the MONGO-PIPELINE / CHAOS / ONPREM scenarios.

Real components only: a ``rag`` step over the knowledge collection the MongoDB
Source filled, the built-in MongoDB MCP connector (``mongodb_aggregate`` read-only,
``mongodb_insert_one`` — ``write_high``, so the platform itself gates it behind an
approval), a conditional on the aggregate's real result, an explicit HITL gate and
code steps. Every side effect is a document in MongoDB (``rw_shop.<ledger>``) whose
``run_id`` field lets a scenario count it in MongoDB itself.

The offline harness parses each builder's YAML with the platform's own DSL model,
publish checks and compiler, so a scenario never fails on a typo in its fixture.
"""

from __future__ import annotations

import json

RETURNS_PIPELINE = [{"$match": {"status": "returned"}}, {"$count": "returned"}]

SUMMARY_CODE = """\
decision = inputs.get("decision") or "auto_closed"
returned = inputs.get("returned")
try:
    returned = int(float(returned))
except (TypeError, ValueError):
    returned = None
output = {
    "decision": decision,
    "returned_orders": returned,
    "root_cause": str(inputs.get("root_cause") or "")[:200],
    "minutes": inputs.get("minutes"),
    "ledger_id": inputs.get("ledger_id") or None,
}
"""


def _j(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def returns_review_yaml(name: str, *, collection_id: str, server_id: str, orders: str,
                        ledger: str, threshold: int) -> str:
    """rag brief -> read-only aggregate -> branch -> HITL -> gated insert -> summary."""
    returned = "{{steps.count_returns.output.output.results.0.returned}}"
    insert_doc = {"run_id": "{{workflow.run_id}}", "kind": "returns_review",
                  "returned": returned, "decision": "{{steps.ops_review.output.action}}",
                  "note": "{{steps.ops_review.output.note}}"}
    return f"""\
name: {name}
description: Weekly returns review - brief from the knowledge base, count returned orders in
  MongoDB, escalate above the threshold to an operations manager, record the decision.
tags: [real-world, mongodb, hitl]
trigger:
  type: api
steps:
  - id: kb_brief
    name: Incident brief from the knowledge base
    type: rag
    rag:
      collection: {collection_id}
      top_k: 5
      strategy: hybrid
    json_output: true
    temperature: 0.0
    max_tokens: 400
    timeout: 180s
    on_failure: abort
    prompt: >-
      Using only the retrieved knowledge, answer: what was the root cause of incident
      PM-2026-014 and how many minutes did it last? Return ONLY a JSON object with keys
      "root_cause" (short string) and "minutes" (integer).
  - id: count_returns
    name: Count returned orders (read-only aggregate)
    type: tool
    tool: mongodb_aggregate
    server_id: {server_id}
    depends_on: [kb_brief]
    timeout: 60s
    on_failure: abort
    retry:
      max_attempts: 3
      backoff: exponential
      base_delay_ms: 500
    input: {_j({"collection": orders, "pipeline": RETURNS_PIPELINE})}
  - id: route
    name: Escalate above the threshold
    type: conditional
    depends_on: [count_returns]
    branches:
      - condition: "{returned} > {threshold}"
        next: ops_review
      - condition: default
        next: auto_close
  - id: ops_review
    name: Operations manager review
    type: hitl
    depends_on: [route]
    timeout: 24h
    timeout_action: escalate
    context:
      - label: Returned orders
        value: "{returned}"
        display_type: number
      - label: Incident root cause
        value: "{{{{steps.kb_brief.output.root_cause}}}}"
        display_type: text
    actions:
      - id: approve
        label: Record and escalate
        style: success
      - id: reject
        label: Dismiss
        style: danger
        requires_note: true
  - id: auto_close
    name: Below threshold - close automatically
    type: set_variable
    depends_on: [route]
    var_name: decision
    var_value: auto_closed
  - id: record_decision
    name: Record the decision in MongoDB (gated write)
    type: tool
    tool: mongodb_insert_one
    server_id: {server_id}
    depends_on: [ops_review]
    timeout: 60s
    on_failure: abort
    input: {_j({"collection": ledger, "document": insert_doc})}
  - id: summary
    name: Summary
    type: code
    runtime: python
    depends_on: [record_decision, auto_close]
    depends_on_any: true
    on_failure: abort
    input:
      decision: "{{{{steps.ops_review.output.action}}}}"
      returned: "{returned}"
      root_cause: "{{{{steps.kb_brief.output.root_cause}}}}"
      minutes: "{{{{steps.kb_brief.output.minutes}}}}"
      ledger_id: "{{{{steps.record_decision.output.output.inserted_id}}}}"
    code: {_j(SUMMARY_CODE)}
"""


RETURNS_STEPS = ["kb_brief", "count_returns", "route", "ops_review", "auto_close",
                 "record_decision", "summary"]


COMPENSATE_CODE = """\
failed = [k for k in ("bad_write", "unauthorized", "slow_server")
          if str(inputs.get(k + "_skipped")).lower() == "true"]
output = {"compensated": failed, "unknown_collection_count": inputs.get("unknown_count")}
"""


def tool_failures_yaml(name: str, *, server_id: str, stalled_server_id: str, orders: str) -> str:
    """Refused write operator, unauthorized database, unknown collection and a hung server,
    each with retry/backoff and ``on_failure: skip``; a compensation step records which
    calls failed (the failure path)."""
    def tool(step_id: str, tool_name: str, sid: str, args: dict[str, object], timeout: str,
             attempts: int) -> str:
        return (f"  - id: {step_id}\n    type: tool\n    tool: {tool_name}\n"
                f"    server_id: {sid}\n    timeout: {timeout}\n    on_failure: skip\n"
                f"    retry:\n      max_attempts: {attempts}\n      backoff: exponential\n"
                f"      base_delay_ms: 400\n    input: {_j(args)}\n")

    return (
        f"name: {name}\ndescription: MongoDB tool failures - retries, backoff, failure path.\n"
        "tags: [real-world, mongodb, failures]\ntrigger:\n  type: api\nsteps:\n"
        + tool("bad_write", "mongodb_aggregate", server_id,
               {"collection": orders, "pipeline": [{"$match": {}}, {"$out": f"{orders}_pwn"}]},
               "30s", 2)
        + tool("unauthorized", "mongodb_find", server_id,
               {"collection": "orders", "database": "rw_p1c", "query": {}}, "30s", 2)
        + tool("unknown_collection", "mongodb_count", server_id,
               {"collection": f"{orders}_does_not_exist", "query": {}}, "30s", 1)
        + tool("slow_server", "mongodb_find", stalled_server_id,
               {"collection": "orders", "query": {}, "limit": 1}, "10s", 3)
        + """\
  - id: compensate
    type: code
    runtime: python
    depends_on: [bad_write, unauthorized, unknown_collection, slow_server]
    on_failure: abort
    input:
      bad_write_skipped: "{{steps.bad_write.output._skipped}}"
      unauthorized_skipped: "{{steps.unauthorized.output._skipped}}"
      slow_server_skipped: "{{steps.slow_server.output._skipped}}"
      unknown_count: "{{steps.unknown_collection.output.output.count}}"
"""
        + f"    code: {_j(COMPENSATE_CODE)}\n")


def two_writes_yaml(name: str, *, server_id: str, ledger: str, wait: str = "2m") -> str:
    """insert #1 -> wait -> insert #2: cancelling during the wait must stop insert #2."""
    def doc(n: int) -> str:
        return _j({"collection": ledger, "document": {"run_id": "{{workflow.run_id}}",
                                                      "kind": "two_writes", "n": n}})

    return f"""\
name: {name}
description: Book a dispatch, wait for the carrier window, then confirm it.
tags: [real-world, mongodb, cancel]
trigger:
  type: api
steps:
  - id: book
    type: tool
    tool: mongodb_insert_one
    server_id: {server_id}
    timeout: 60s
    on_failure: abort
    input: {doc(1)}
  - id: carrier_window
    type: wait
    depends_on: [book]
    duration: {wait}
  - id: confirm
    type: tool
    tool: mongodb_insert_one
    server_id: {server_id}
    depends_on: [carrier_window]
    timeout: 60s
    on_failure: abort
    input: {doc(2)}
"""


def approval_timeout_yaml(name: str, *, server_id: str, ledger: str) -> str:
    """A gate with a 1-minute timeout (escalate) in front of a write."""
    return f"""\
name: {name}
description: Refund release that needs a finance sign-off within one minute.
tags: [real-world, hitl, timeout]
trigger:
  type: api
steps:
  - id: finance_signoff
    type: hitl
    timeout: 1m
    timeout_action: escalate
    escalation:
      after: 1m
      to_role: admin
    context:
      - label: Refund
        value: "INR 1999.00 for the cracked terracotta planter"
        display_type: text
    actions:
      - id: approve
        label: Release
        style: success
      - id: reject
        label: Hold
        style: danger
        requires_note: true
  - id: release
    type: tool
    tool: mongodb_insert_one
    server_id: {server_id}
    depends_on: [finance_signoff]
    timeout: 60s
    on_failure: abort
    input: {_j({"collection": ledger, "document": {"run_id": "{{workflow.run_id}}",
                                                    "kind": "refund_release"}})}
"""


def side_effect_then_llm_yaml(name: str, *, server_id: str, ledger: str,
                              collection_id: str) -> str:
    """A gated insert (the side effect) followed by a slow rag step: the chaos scenario
    kills the workflow worker between the two."""
    return f"""\
name: {name}
description: Record a settlement hold, then draft the merchant notice from the knowledge base.
tags: [real-world, chaos]
trigger:
  type: api
steps:
  - id: record_hold
    type: tool
    tool: mongodb_insert_one
    server_id: {server_id}
    timeout: 60s
    on_failure: abort
    input: {_j({"collection": ledger, "document": {"run_id": "{{workflow.run_id}}",
                                                    "kind": "settlement_hold"}})}
  - id: draft_notice
    type: rag
    depends_on: [record_hold]
    rag:
      collection: {collection_id}
      top_k: 8
      strategy: hybrid
    temperature: 0.2
    max_tokens: 900
    timeout: 300s
    on_failure: abort
    prompt: >-
      Write a detailed, polite three-paragraph notice to merchants explaining the settlement
      webhook outage PM-2026-014: what happened, how long it lasted, the root cause and the
      action items, using only the retrieved knowledge.
  - id: done
    type: code
    runtime: python
    depends_on: [draft_notice]
    on_failure: abort
    input:
      ledger_id: "{{{{steps.record_hold.output.output.inserted_id}}}}"
    code: |
      output = {{"ok": True, "ledger_id": inputs.get("ledger_id")}}
"""


def llm_step_yaml(name: str, *, model: str, prompt: str, max_tokens: int = 64,
                  timeout: str = "120s") -> str:
    """One llm step pinned to a registry model (context-overflow / routing scenarios)."""
    return f"""\
name: {name}
description: Single LLM call pinned to one registry model.
tags: [real-world, onprem]
trigger:
  type: api
steps:
  - id: ask
    type: llm
    model: {_j(model)}
    temperature: 0.0
    max_tokens: {max_tokens}
    timeout: {timeout}
    on_failure: abort
    prompt: {_j(prompt)}
"""
