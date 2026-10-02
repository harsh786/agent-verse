"""Workflow definitions (YAML) of the complex workflow / trigger scenarios.

Each builder returns the YAML a user would import through
``POST /api/v1/workflows/import-yaml``. The offline harness parses every one with
the platform's own DSL model, publish checks and graph compiler, so a scenario
never fails on a typo in its own fixture.
"""

from __future__ import annotations

import json
from typing import Any

from tests.real_world.fixture_server import CARRIER_SLA, FX_RATES, INVENTORY, load_orders

PUBLISH_LANE_THRESHOLD = 50000

PARSE_ORDERS_CODE = """\
import json
data = inputs.get("orders")
if isinstance(data, str):
    data = json.loads(data)
orders = data.get("orders", [])
gross = round(sum(float(o["amount"]) for o in orders), 2)
output = {
    "batch": data.get("batch"),
    "region": data.get("region"),
    "count": len(orders),
    "gross": gross,
    "high_value": sorted(o["order_id"] for o in orders if float(o["amount"]) > 10000),
    "cold_chain": sum(1 for o in orders if o.get("handling") == "cold-chain"),
}
"""

JOIN_CODE = """\
import json
def obj(v):
    return json.loads(v) if isinstance(v, str) and v.strip().startswith("{") else (v or {})
fx, inv, sla = obj(inputs.get("fx")), obj(inputs.get("inventory")), obj(inputs.get("sla"))
gross = float(inputs.get("gross") or 0)
carriers = sla.get("carriers") or []
best = max(carriers, key=lambda c: c.get("on_time_pct", 0)) if carriers else {}
output = {
    "lane": inputs.get("lane"),
    "gross_inr": gross,
    "gross_usd": round(gross * float((fx.get("rates") or {}).get("USD", 0)), 2),
    "coldbox_stock": (inv.get("skus") or {}).get("LX-COLDBOX-40"),
    "best_carrier": best.get("name"),
}
"""

COMPOSE_CODE = """\
output = {
    "batch": inputs.get("batch"),
    "lane": inputs.get("lane"),
    "gross": float(inputs.get("gross") or 0),
    "decision": inputs.get("decision"),
    "approver_note": inputs.get("note"),
    "ledger_attempt": int(float(inputs.get("ledger_attempt") or 0)),
    "headline": str(inputs.get("headline") or "")[:140],
}
"""


def expected_pipeline() -> dict[str, Any]:
    """What WF-COMPLEX-PIPELINE must compute from the fixture data (ground truth)."""
    orders = load_orders()["orders"]
    gross = round(sum(float(o["amount"]) for o in orders), 2)
    best = max(CARRIER_SLA["carriers"], key=lambda c: float(str(c["on_time_pct"])))
    return {
        "count": len(orders),
        "gross": gross,
        "high_value": sorted(o["order_id"] for o in orders if float(o["amount"]) > 10000),
        "cold_chain": sum(1 for o in orders if o["handling"] == "cold-chain"),
        "lane": "expedite" if gross > PUBLISH_LANE_THRESHOLD else "standard",
        "gross_usd": round(gross * float(FX_RATES["rates"]["USD"]), 2),  # type: ignore[index]
        "coldbox_stock": INVENTORY["skus"]["LX-COLDBOX-40"],  # type: ignore[index]
        "best_carrier": best["name"],
        "batch": load_orders()["batch"],
    }


def complex_pipeline_yaml(name: str, base: str, key: str, *, flaky_failures: int = 2) -> str:
    """12 top-level steps (+3 parallel branches): fetch → parse → branch → fan-out/join →
    LLM summary → flaky ledger post with retries → approval gate → compose → publish."""
    return f"""\
name: {name}
description: Nightly order batch - fetch, enrich, summarise, approve and publish the release.
tags: [real-world, complex, hitl]
trigger:
  type: api
inputs:
  region:
    type: string
    required: false
    default: South-West
steps:
  - id: fetch_orders
    name: Fetch order batch
    type: http
    method: GET
    url: "{base}/orders/{key}.json"
    timeout: 30s
    on_failure: abort
  - id: parse_orders
    name: Parse and total the batch
    type: code
    runtime: python
    depends_on: [fetch_orders]
    on_failure: abort
    input:
      orders: "{{{{steps.fetch_orders.output}}}}"
    code: {json.dumps(PARSE_ORDERS_CODE)}
  - id: route_lane
    name: Choose the dispatch lane
    type: conditional
    depends_on: [parse_orders]
    branches:
      - condition: "{{{{steps.parse_orders.output.gross}}}} > {PUBLISH_LANE_THRESHOLD}"
        next: lane_expedite
      - condition: default
        next: lane_standard
  - id: lane_expedite
    name: Expedite lane
    type: set_variable
    depends_on: [route_lane]
    var_name: lane
    var_value: expedite
  - id: lane_standard
    name: Standard lane
    type: set_variable
    depends_on: [route_lane]
    var_name: lane
    var_value: standard
  - id: enrich
    name: Enrich in parallel
    type: parallel
    depends_on: [lane_expedite, lane_standard]
    depends_on_any: true
    on_failure: abort
    parallel_branches:
      - id: fx_rates
        type: http
        method: GET
        url: "{base}/fx-rates/{key}"
        timeout: 30s
      - id: inventory
        type: http
        method: GET
        url: "{base}/inventory/{key}"
        timeout: 30s
      - id: carrier_sla
        type: http
        method: GET
        url: "{base}/carrier-sla/{key}"
        timeout: 30s
  - id: join_context
    name: Join enrichment
    type: code
    runtime: python
    depends_on: [enrich]
    on_failure: abort
    input:
      fx: "{{{{steps.fx_rates.output}}}}"
      inventory: "{{{{steps.inventory.output}}}}"
      sla: "{{{{steps.carrier_sla.output}}}}"
      lane: "{{{{vars.lane}}}}"
      gross: "{{{{steps.parse_orders.output.gross}}}}"
    code: {json.dumps(JOIN_CODE)}
  - id: summarise
    name: Summarise for finance
    type: llm
    json_output: true
    temperature: 0.1
    max_tokens: 400
    timeout: 180s
    depends_on: [join_context]
    on_failure: abort
    prompt: >-
      Order batch {{{{steps.parse_orders.output.batch}}}}: {{{{steps.parse_orders.output.count}}}}
      orders worth INR {{{{steps.parse_orders.output.gross}}}} (USD
      {{{{steps.join_context.output.gross_usd}}}}), dispatch lane {{{{vars.lane}}}}, best carrier
      {{{{steps.join_context.output.best_carrier}}}}. Return ONLY a JSON object with keys
      "headline" (one sentence naming the batch id) and "risk_level" (one of "low", "medium",
      "high").
  - id: post_ledger
    name: Post to the finance ledger (flaky)
    type: http
    method: POST
    url: "{base}/flaky/{key}?fail={flaky_failures}"
    depends_on: [summarise]
    timeout: 20s
    on_failure: abort
    retry:
      max_attempts: {flaky_failures + 1}
      backoff: fixed
      base_delay_ms: 400
    request_body:
      batch: "{{{{steps.parse_orders.output.batch}}}}"
      gross: "{{{{steps.parse_orders.output.gross}}}}"
  - id: finance_approval
    name: Finance controller approval
    type: hitl
    depends_on: [post_ledger]
    timeout: 24h
    timeout_action: escalate
    context:
      - label: Batch
        value: "{{{{steps.parse_orders.output.batch}}}}"
        display_type: text
      - label: Lane
        value: "{{{{vars.lane}}}}"
        display_type: text
      - label: Summary
        value: "{{{{steps.summarise.output.headline}}}}"
        display_type: text
    actions:
      - id: approve
        label: Release
        style: success
      - id: reject
        label: Hold
        style: danger
        requires_note: true
  - id: compose_release
    name: Compose release
    type: code
    runtime: python
    depends_on: [finance_approval]
    on_failure: abort
    input:
      batch: "{{{{steps.parse_orders.output.batch}}}}"
      lane: "{{{{vars.lane}}}}"
      gross: "{{{{steps.parse_orders.output.gross}}}}"
      decision: "{{{{steps.finance_approval.output.action}}}}"
      note: "{{{{steps.finance_approval.output.note}}}}"
      ledger_attempt: "{{{{steps.post_ledger.output.attempt}}}}"
      headline: "{{{{steps.summarise.output.headline}}}}"
    code: {json.dumps(COMPOSE_CODE)}
  - id: publish
    name: Publish release
    type: http
    method: POST
    url: "{base}/publish/{key}"
    depends_on: [compose_release]
    timeout: 30s
    on_failure: abort
    request_body:
      batch: "{{{{steps.compose_release.output.batch}}}}"
      lane: "{{{{steps.compose_release.output.lane}}}}"
      gross: "{{{{steps.compose_release.output.gross}}}}"
      decision: "{{{{steps.compose_release.output.decision}}}}"
"""


PIPELINE_TOP_LEVEL = ["fetch_orders", "parse_orders", "route_lane", "lane_expedite",
                      "lane_standard", "enrich", "join_context", "summarise", "post_ledger",
                      "finance_approval", "compose_release", "publish"]


def recovery_yaml(name: str, base: str, key: str) -> str:
    """fetch → total → charge (side effect) → confirm (fails while the switch is on) → finish."""
    return f"""\
name: {name}
description: Charge a batch and confirm with finance; a finance outage fails the run.
tags: [real-world, recovery]
trigger:
  type: api
steps:
  - id: fetch_orders
    type: http
    method: GET
    url: "{base}/orders/{key}.json"
    on_failure: abort
  - id: total
    type: code
    runtime: python
    depends_on: [fetch_orders]
    on_failure: abort
    input:
      orders: "{{{{steps.fetch_orders.output}}}}"
    code: {json.dumps(PARSE_ORDERS_CODE)}
  - id: charge
    type: http
    method: POST
    url: "{base}/charge/{key}"
    depends_on: [total]
    on_failure: abort
    request_body:
      batch: "{{{{steps.total.output.batch}}}}"
      gross: "{{{{steps.total.output.gross}}}}"
  - id: confirm
    type: http
    method: GET
    url: "{base}/switch/{key}"
    depends_on: [charge]
    on_failure: abort
    retry:
      max_attempts: 1
  - id: finish
    type: code
    runtime: python
    depends_on: [confirm]
    on_failure: abort
    input:
      charged: "{{{{steps.charge.output.count}}}}"
      batch: "{{{{steps.total.output.batch}}}}"
    code: |
      output = {{"done": True, "batch": inputs.get("batch"),
                "charge_calls_seen": int(float(inputs.get("charged") or 0))}}
"""


def chain_producer_yaml(name: str, channel: str, secret: str) -> str:
    """Webhook-triggered (HMAC) producer whose completion announces an event."""
    return f"""\
name: {name}
description: Validate an inbound batch notification and announce it to downstream workflows.
tags: [real-world, trigger-chain]
trigger:
  type: webhook
  webhook:
    auth: hmac
    hmac_secret: "{secret}"
inputs:
  batch:
    type: string
    required: false
    default: ""
  gross:
    type: number
    required: false
    default: 0
steps:
  - id: validate
    type: code
    runtime: python
    on_failure: abort
    input:
      batch: "{{{{inputs.batch}}}}"
      gross: "{{{{inputs.gross}}}}"
    code: |
      gross = float(inputs.get("gross") or 0)
      output = {{"batch": inputs.get("batch"), "gross": gross, "valid": gross > 0}}
  - id: announce
    type: emit_event
    depends_on: [validate]
    on_failure: abort
    event_channel_out: {channel}
    event_payload:
      batch: "{{{{steps.validate.output.batch}}}}"
      gross: "{{{{steps.validate.output.gross}}}}"
      valid: "{{{{steps.validate.output.valid}}}}"
"""


def chain_consumer_yaml(name: str, channel: str) -> str:
    """Parks on the producer's event, then records what it received."""
    return f"""\
name: {name}
description: Wait for the upstream batch announcement, then book the dispatch.
tags: [real-world, trigger-chain]
trigger:
  type: api
steps:
  - id: wait_for_upstream
    type: wait
    event_channel: {channel}
    duration: 15m
  - id: book_dispatch
    type: code
    runtime: python
    depends_on: [wait_for_upstream]
    on_failure: abort
    input:
      event: "{{{{steps.wait_for_upstream.output.event}}}}"
      timed_out: "{{{{steps.wait_for_upstream.output.timed_out}}}}"
    code: |
      import json
      ev = inputs.get("event")
      if isinstance(ev, str):
          ev = json.loads(ev) if ev.strip().startswith("{{") else {{}}
      ev = ev or {{}}
      output = {{"received": bool(ev), "batch": ev.get("batch"), "gross": ev.get("gross"),
                "timed_out": str(inputs.get("timed_out")).lower() == "true"}}
"""


def scheduled_ping_yaml(name: str, cron: str) -> str:
    """A minimal scheduled workflow (one code step) for SCHED-FIRES-WORKFLOW."""
    return f"""\
name: {name}
description: Scheduled heartbeat that records when it ran.
tags: [real-world, schedule]
trigger:
  type: schedule
  schedule:
    cron: "{cron}"
    timezone: UTC
steps:
  - id: heartbeat
    type: code
    runtime: python
    code: |
      output = {{"alive": True}}
"""
