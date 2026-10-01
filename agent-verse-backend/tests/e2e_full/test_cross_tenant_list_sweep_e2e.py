"""e2e_full: schema-driven cross-tenant sweep over every parameterless GET (RLS-AUDIT).

The local Docker stack connected as a SUPERUSER, which bypasses row-level
security, and ``GET /api/v1/runs`` returned every tenant's workflow runs: the
store relied ONLY on RLS. The path-param IDOR sweep
(``test_cross_tenant_sweep_e2e``) never calls list endpoints, so it could not
see that class of bug.

This sweep runs on the e2e harness's default connection — the testcontainer
SUPERUSER, i.e. exactly the RLS-bypassing posture that hid the bug (with
``E2E_LEAST_PRIVILEGE=1`` it still holds, just less strictly). Tenant **A**
creates one resource per create-style collection (bodies synthesised from the
OpenAPI schema, a unique marker in every string) plus a goal, a workflow and a
workflow run. Tenant **B** calls every parameterless GET before and after; a
finding is A's marker, or one of A's new ids, appearing in B's response.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e_full.test_cross_tenant_sweep_e2e import (
    _SKIP_COLLECTIONS,
    _collect_ids,
    _req,
    _signup,
    _Synth,
)
from tests.e2e_full.test_workflow_trigger_e2e import _inline_runner  # noqa: F401 (fixture)

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
# Streaming / file endpoints: not JSON listings, and some never end.
_SKIP_SEGMENTS = {"stream", "events", "sse", "ws", "download", "export", "metrics"}


async def _reset_rate_limits(app: Any) -> None:
    """Clear per-tenant request windows: ~200 GETs per pass exceed the plan limit."""
    redis = getattr(app.state, "_rate_limiter_redis", None) or getattr(app.state, "_redis", None)
    if redis is None:
        return
    keys = await redis.keys("*rl:*")  # TenantScopedStore prefixes the tenant
    if keys:
        await redis.delete(*keys)


async def _seed_known_resources(ca: Any, marker: str) -> set[str]:
    """A goal, a workflow and a real workflow run for tenant A."""
    ids: set[str] = set()
    goal = await _req(ca, "POST", f"{_API}/goals", {"goal": f"{marker} summarise the weather"})
    if goal is not None and goal.status_code < 300:
        _collect_ids(goal.json(), ids)
    wf = await _req(
        ca,
        "POST",
        f"{_API}/workflows",
        {
            "name": f"{marker}-wf",
            "description": marker,
            "definition": {
                "name": f"{marker}-wf",
                "steps": [{"id": "s1", "type": "transform", "input": {"m": marker}}],
            },
        },
    )
    assert wf is not None and wf.status_code == 201, wf.text if wf is not None else "hung"
    wf_id = str(wf.json()["id"])
    ids.add(wf_id)
    trig = await _req(
        ca, "POST", f"{_API}/workflows/{wf_id}/trigger", {"inputs": {}, "dry_run": False}
    )
    assert trig is not None and trig.status_code == 202, trig.text if trig is not None else "hung"
    ids.add(str(trig.json()["run_id"]))
    # Sanity: A itself sees its run, so B not seeing it means something.
    runs = await _req(ca, "GET", f"{_API}/runs")
    assert runs is not None and str(trig.json()["run_id"]) in runs.text
    return ids


async def test_no_list_endpoint_leaks_across_tenants(
    app: Any, client: Any, _reset_signup_rate_limit: None, _inline_runner: None  # noqa: F811
) -> None:
    from httpx import ASGITransport, AsyncClient

    spec = app.openapi()
    marker = f"mkL{uuid.uuid4().hex[:10]}"
    synth = _Synth(spec, marker)
    key_a, _ = await _signup(client)
    key_b, _ = await _signup(client)

    def _client(key: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://e2e",
            headers={"X-API-Key": key},
        )

    ca, cb = _client(key_a), _client(key_b)
    paths = spec["paths"]
    list_paths = [
        p
        for p, item in paths.items()
        if "get" in item
        and "{" not in p
        and not any(p.startswith(s) for s in _SKIP_COLLECTIONS)
        and not _SKIP_SEGMENTS.intersection(p.split("/"))
    ]
    statuses: dict[int, int] = {}

    async def _b_views() -> dict[str, str]:
        out: dict[str, str] = {}
        statuses.clear()
        for i, p in enumerate(list_paths):
            if i % 20 == 0:
                await _reset_rate_limits(app)
            r = await _req(cb, "GET", p)
            code = r.status_code if r is not None else 0
            statuses[code] = statuses.get(code, 0) + 1
            if r is not None and r.status_code < 300:
                out[p] = r.text
        return out

    baseline = await _b_views()

    # Tenant A creates one resource per create-style collection (as the IDOR sweep).
    a_ids = await _seed_known_resources(ca, marker)
    created = 0
    for path, item in paths.items():
        post = item.get("post")
        if not post or "{" in path or any(path.startswith(p) for p in _SKIP_COLLECTIONS):
            continue
        if not any(p.startswith(path.rstrip("/") + "/{") for p in paths):
            continue
        schema = (
            post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        )
        await _reset_rate_limits(app)
        resp = await _req(ca, "POST", path, synth.value(schema) if schema else {})
        if resp is None or resp.status_code >= 300:
            continue
        try:
            _collect_ids(resp.json(), a_ids)
            created += 1
        except ValueError:
            continue
    assert created >= 15, f"tenant A could only create {created} resource types"

    after = await _b_views()
    findings: list[str] = []
    for p, body in after.items():
        before = baseline.get(p, "")
        leaked = [i for i in a_ids if i in body and i not in before]
        if marker in body or leaked:
            what = "marker" if marker in body else f"id {leaked[0]}"
            findings.append(f"LIST-LEAK GET {p} ({what})")
    await ca.aclose()
    await cb.aclose()
    assert len(after) > 40, f"only {len(after)} list endpoints answered tenant B: {statuses}"
    assert not findings, (
        f"{len(findings)} list endpoint(s) show tenant A's data to tenant B:\n"
        + "\n".join(sorted(findings))
    )
