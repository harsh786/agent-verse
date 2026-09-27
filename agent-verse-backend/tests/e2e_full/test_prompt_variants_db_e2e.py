"""e2e_full: prompt variants live in Postgres per tenant; promotion is cost/latency-gated.

Regressions:

* The prompt-variant endpoints searched EVERY tenant's variants: a tenant could
  read another tenant's variant report and promote (i.e. hot-swap the live
  planner prompt of) another tenant's variant, or delete the shared "global"
  ones. Everything is tenant-scoped now, and ``prompt_variants`` has RLS.
* Each replica loaded every tenant's variants into memory at startup; a variant
  registered or promoted on one replica was unknown to the others. The
  optimizer now reads and writes the table per tenant.
* Auto-promotion looked only at quality, so a challenger that scored slightly
  higher while tripling cost was promoted. It must now also pass the
  ``RegressionGate`` on cost and p95 latency.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _signup(app: Any, client: Any) -> tuple[AsyncClient, str]:
    email = f"pv-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "PV", "email": email})
    assert r.status_code == 201, r.text
    body = r.json()
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://e2e-full",
        headers={"X-API-Key": body["api_key"]},
    )
    return c, body["tenant_id"]


async def test_prompt_variants_are_tenant_scoped_in_the_database(app: Any, client: Any) -> None:
    assert app.state.prompt_optimizer.db_mode is True
    owner, _ = await _signup(app, client)
    other, _ = await _signup(app, client)
    try:
        created = await owner.post(
            "/intelligence/prompt-variants",
            json={"key": "planner", "name": "terse", "prompt_text": "Plan tersely."},
        )
        assert created.status_code == 201, created.text
        vid = created.json()["id"]
        dup = await owner.post(
            "/intelligence/prompt-variants",
            json={"key": "planner", "name": "terse", "prompt_text": "again"},
        )
        assert dup.status_code == 409

        for method, path in [
            ("GET", f"/intelligence/prompt-variants/{vid}/report"),
            ("POST", f"/intelligence/prompt-variants/{vid}/promote"),
            ("DELETE", f"/intelligence/prompt-variants/{vid}"),
        ]:
            r = await other.request(method, path)
            assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text}"
        assert (await other.get("/intelligence/prompt-variants?key=planner")).json() == []

        promoted = await owner.post(f"/intelligence/prompt-variants/{vid}/promote")
        assert promoted.status_code == 200, promoted.text
        listed = (await owner.get("/intelligence/prompt-variants?key=planner")).json()
        assert [(v["id"], v["is_control"]) for v in listed] == [(vid, True)]
        report = (await owner.get(f"/intelligence/prompt-variants/{vid}/report")).json()
        assert report["run_count"] == 0 and report["mean_score"] is None
    finally:
        await owner.aclose()
        await other.aclose()


async def test_auto_promotion_is_gated_on_cost_and_latency(app: Any, client: Any) -> None:
    from app.intelligence.prompt_optimizer import PromptOptimizer

    owner, tenant_id = await _signup(app, client)
    await owner.aclose()
    opt = PromptOptimizer(min_runs_for_promotion=30, confidence=0.95)
    opt.set_db(app.state.db_session_factory)

    control = await opt.aregister("planner", "control", "base", tenant_id=tenant_id,
                                  is_control=True)
    pricey = await opt.aregister("planner", "pricey", "verbose", tenant_id=tenant_id)
    lean = await opt.aregister("planner", "lean", "lean", tenant_id=tenant_id)
    assert control and pricey and lean

    verdict = None
    for i in range(30):
        jitter = (i % 3) * 0.01
        await opt.arecord_result(control.variant_id, tenant_id=tenant_id,
                                 eval_score=0.60 + jitter, cost_usd=0.010, latency_ms=900)
        # Clearly better quality — but 3x the cost: must be held back.
        verdict = await opt.arecord_result(pricey.variant_id, tenant_id=tenant_id,
                                           eval_score=0.90 + jitter, cost_usd=0.030,
                                           latency_ms=900)
    assert verdict is not None and verdict.promoted_variant_id is None
    assert "cost_regression" in verdict.held[pricey.variant_id]

    for i in range(30):
        jitter = (i % 3) * 0.01
        verdict = await opt.arecord_result(lean.variant_id, tenant_id=tenant_id,
                                           eval_score=0.80 + jitter, cost_usd=0.010,
                                           latency_ms=800)
    # Better quality at the same cost and latency: promoted, durably.
    assert verdict is not None and verdict.promoted_variant_id == lean.variant_id
    listed = {v.variant_id: v for v in await opt.alist(tenant_id, "planner")}
    assert listed[lean.variant_id].is_control is True
    assert control.variant_id not in listed  # old control archived (inactive)
    assert listed[pricey.variant_id].is_control is False
    assert listed[lean.variant_id].run_count == 30
    assert listed[lean.variant_id].cost_samples == 30

    # A second replica (fresh optimizer, same DB) sees the same state.
    replica = PromptOptimizer()
    replica.set_db(app.state.db_session_factory)
    selected = {
        (await replica.aselect_variant("planner", tenant_id=tenant_id)).variant_id
        for _ in range(40)
    }
    assert lean.variant_id in selected and control.variant_id not in selected

    # Outcomes on another tenant's variant are ignored (RLS + tenant predicate).
    other_tenant = uuid.uuid4().hex
    assert await opt.arecord_result(lean.variant_id, tenant_id=other_tenant,
                                    eval_score=0.0) is None
    after = {v.variant_id: v for v in await opt.alist(tenant_id, "planner")}
    assert after[lean.variant_id].run_count == 30
