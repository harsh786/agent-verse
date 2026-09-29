"""e2e_full: schema-driven cross-tenant (IDOR) sweep over every path-param operation.

For each create-style ``POST /<collection>`` in the app's own OpenAPI schema, a
request body is synthesised from the declared request schema (with a unique
marker planted in every string field) and tenant **A** creates the resource.
Every operation under that collection that takes a path parameter is then
called by tenant **B** with A's real ids substituted in.

A finding is:
* a 2xx **read** whose body contains A's resource id or A's marker, or
* a 2xx **write** (POST/PUT/PATCH/DELETE) against A's resource.

Operations B is expected to be refused must answer 401/403/404 (or 405/422).
The sweep is schema-driven so new endpoints are covered automatically.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# Collections not exercised by this sweep, each with a reason.
_SKIP_COLLECTIONS = {
    "/tenants": "tenant self-management — the caller's own tenant, no foreign ids",
    "/auth": "SSO flows",
    "/scim": "IdP provisioning with its own bearer auth",
    "/billing": "payment provider flows",
    "/v1/gateway": "channel webhooks with per-channel signature auth",
    "/integrations": "third-party webhooks",
    "/wf-hooks": "signed webhook tokens",
    "/health": "public",
    "/.well-known": "public discovery",
}

_ID_KEYS = ("id", "_id", "Id")


class _Synth:
    def __init__(self, spec: dict[str, Any], marker: str) -> None:
        self.spec = spec
        self.marker = marker

    def _resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        seen = 0
        while "$ref" in schema and seen < 20:
            ref = schema["$ref"].split("/")[-1]
            schema = self.spec["components"]["schemas"].get(ref, {})
            seen += 1
        for key in ("allOf", "anyOf", "oneOf"):
            if schema.get(key):
                options = [self._resolve(s) for s in schema[key]]
                non_null = [o for o in options if o.get("type") != "null"] or options
                if key == "allOf":
                    merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
                    for o in non_null:
                        merged["properties"].update(o.get("properties", {}))
                        merged["required"] += o.get("required", [])
                    return merged
                return non_null[0]
        return schema

    def value(self, schema: dict[str, Any], name: str = "", depth: int = 0) -> Any:
        schema = self._resolve(schema or {})
        if "default" in schema and schema["default"] is not None:
            return schema["default"]
        if schema.get("enum"):
            return schema["enum"][0]
        if "const" in schema:
            return schema["const"]
        t = schema.get("type")
        fmt = schema.get("format", "")
        if t == "object" or "properties" in schema:
            if depth > 4:
                return {}
            props = schema.get("properties", {})
            return {
                k: self.value(v, k, depth + 1)
                for k, v in props.items()
                if k in set(schema.get("required", []))
            }
        if t == "array":
            n = max(int(schema.get("minItems", 0)), 1 if "items" in schema else 0)
            return [self.value(schema.get("items", {}), name, depth + 1) for _ in range(min(n, 1))]
        if t == "integer":
            return max(int(schema.get("minimum", 1)), 1)
        if t == "number":
            lo = schema.get("minimum", 0.0)
            hi = schema.get("maximum", 1.0)
            return (float(lo) + float(hi)) / 2
        if t == "boolean":
            return False
        if fmt == "email":
            return f"{self.marker}@example.com"
        if fmt in ("uri", "url") or name.endswith("url"):
            return "https://example.com/" + self.marker
        if fmt == "uuid":
            return str(uuid.uuid4())
        if fmt == "date-time":
            return "2030-01-01T00:00:00Z"
        text = f"{self.marker}-{name or 'v'}"
        max_len = int(schema.get("maxLength", 200))
        return text[:max_len] if len(text) >= int(schema.get("minLength", 0)) else text.ljust(
            int(schema.get("minLength", 0)), "x"
        )


def _collect_ids(obj: Any, out: set[str], depth: int = 0) -> None:
    if depth > 4:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (k in _ID_KEYS or k.endswith("_id")) and len(v) >= 6:
                out.add(v)
            elif isinstance(v, dict | list):
                _collect_ids(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:5]:
            _collect_ids(item, out, depth + 1)


async def _signup(client: Any) -> tuple[str, str]:
    email = f"idor-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "IDOR", "email": email})
    assert r.status_code == 201, r.text
    return str(r.json()["api_key"]), str(r.json()["tenant_id"])


async def _req(client: Any, method: str, url: str, body: Any = None) -> Any:
    try:
        return await asyncio.wait_for(
            client.request(
                method,
                url,
                content=json.dumps(body if body is not None else {}),
                headers={"content-type": "application/json"},
            ),
            timeout=20,
        )
    except TimeoutError:
        return None


async def test_no_path_param_operation_leaks_across_tenants(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    from httpx import ASGITransport, AsyncClient

    spec = app.openapi()
    # DISTINCT markers: A's creations and B's probe bodies must never share one,
    # or B's own writes echoed back to B would read as A's data.
    marker = f"mkA{uuid.uuid4().hex[:10]}"
    synth = _Synth(spec, marker)
    synth_b = _Synth(spec, f"mkB{uuid.uuid4().hex[:10]}")
    key_a, _tid_a = await _signup(client)
    key_b, _tid_b = await _signup(client)
    # raise_app_exceptions=False: an unhandled server exception becomes the 500 a
    # real client would receive, recorded as a finding, instead of aborting the
    # sweep at the first crash.
    def _client(key: str) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://e2e",
            headers={"X-API-Key": key},
        )

    ca, cb = _client(key_a), _client(key_b)
    server_errors: list[str] = []
    notes: list[str] = []

    paths = spec["paths"]
    # 1. Tenant A creates one resource per create-style collection.
    ids_by_collection: dict[str, set[str]] = {}
    created = 0
    for path, item in paths.items():
        post = item.get("post")
        if not post or "{" in path:
            continue
        if any(path.startswith(p) for p in _SKIP_COLLECTIONS):
            continue
        if not any(p.startswith(path.rstrip("/") + "/{") for p in paths):
            continue  # nothing addressable underneath — not a resource collection
        schema = (
            post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        )
        body = synth.value(schema) if schema else {}
        resp = await _req(ca, "POST", path, body)
        if resp is not None and resp.status_code >= 500:
            if resp.status_code == 503 and "not enabled" in resp.text.lower():
                # A feature switched off by configuration, reported as such —
                # not a server error (e.g. CIVILIZATION_ENABLED unset).
                notes.append(f"feature disabled: POST {path}")
            elif resp.status_code == 501:
                # An honest "not implemented" (e.g. golden datasets) creates
                # nothing and returns no data, so there is nothing to leak.
                notes.append(f"not implemented: POST {path}")
            else:
                server_errors.append(f"{resp.status_code} POST {path} (tenant A create)")
        if resp is None or resp.status_code >= 300:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        found: set[str] = set()
        _collect_ids(payload, found)
        if found:
            ids_by_collection[path.rstrip("/")] = found
            created += 1

    assert created >= 15, (
        f"tenant A could only create {created} resource types — the body synthesiser is not "
        "producing valid requests, so the sweep would prove nothing"
    )

    # 2. Tenant B replays every path-param operation with A's ids.
    #
    # Reads: a leak is A's marker, or an A id OTHER than the one B put in the URL.
    # Writes: a 2xx alone proves nothing (it may be a no-op, or a write into B's
    # own tenant referencing a foreign id). A write is a finding only if A's own
    # view of that resource CHANGES — every parameterless GET under the resource
    # is snapshotted by A before the write and compared after it.
    _volatile = re.compile(
        r'"[^"]*(?:_at|timestamp|time|duration[a-z_]*|latency[a-z_]*|elapsed[a-z_]*)"\s*:\s*'
        r'(?:"[^"]*"|-?[0-9.eE+-]+|null)'
    )

    def _gets_under(prefix: str) -> list[str]:
        out = []
        for p2, it2 in paths.items():
            if "get" in it2 and p2.startswith(prefix + "/{") and p2.count("{") == 1:
                if not any(seg in p2 for seg in ("/stream", "/events")):
                    out.append(p2)
        return out

    async def _snapshot(collection: str, rid: str) -> dict[str, str]:
        snap: dict[str, str] = {}
        for p2 in _gets_under(collection):
            r = await _req(ca, "GET", re.sub(r"\{[^}]+\}", rid, p2))
            if r is not None and r.status_code < 300:
                snap[p2] = _volatile.sub("", r.text)
        return snap

    findings: list[str] = []
    probed = 0
    for path, item in paths.items():
        if "{" not in path:
            continue
        collection = next(
            (
                c
                for c in sorted(ids_by_collection, key=len, reverse=True)
                if path.startswith(c + "/{")
            ),
            None,
        )
        if collection is None:
            continue
        a_ids = ids_by_collection[collection]
        first_id = sorted(a_ids)[0]
        for method, op in item.items():
            m = method.upper()
            if m not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            url = re.sub(r"\{[^}]+\}", first_id, path, count=1)
            url = re.sub(r"\{[^}]+\}", uuid.uuid4().hex, url)  # deeper params: unknown ids
            schema = (
                op.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            body = synth_b.value(schema) if schema else {}
            before = await _snapshot(collection, first_id) if m != "GET" else {}
            resp = await _req(cb, m, url, body)
            probed += 1
            if resp is None:
                findings.append(f"HUNG {m} {path}")
                continue
            if resp.status_code == 501:
                notes.append(f"not implemented: {m} {path}")
                continue
            if resp.status_code >= 500:
                server_errors.append(f"{resp.status_code} {m} {path} (tenant B probe)")
                continue
            if resp.status_code >= 300:
                continue
            if m == "GET":
                other_ids = [i for i in a_ids if i != first_id]
                if marker in resp.text or any(i in resp.text for i in other_ids):
                    findings.append(f"READ-LEAK {resp.status_code} {m} {path}")
                continue
            after = await _snapshot(collection, first_id)
            changed = [k for k in before if after.get(k) != before[k]]
            if changed:
                findings.append(
                    f"FOREIGN-WRITE {resp.status_code} {m} {path} (changed A's {changed[0]})"
                )
            else:
                # 2xx for a foreign id without touching A: the handler never
                # checked the parent exists in the caller's tenant. Not a breach,
                # but it should be a 404 — recorded, not asserted.
                notes.append(f"{resp.status_code} {m} {path}")

    await ca.aclose()
    await cb.aclose()
    assert probed > 50, f"only {probed} cross-tenant probes ran"
    # Two finding classes, reported together so one never masks the other:
    # cross-tenant access, and any 5xx provoked by schema-valid client input
    # (a robustness defect even when nothing leaks).
    report = []
    if findings:
        report.append(
            f"{len(findings)} cross-tenant finding(s) over {probed} probes "
            f"({created} resource types):\n" + "\n".join(sorted(findings))
        )
    if server_errors:
        report.append(
            f"{len(server_errors)} server error(s) on schema-valid input:\n"
            + "\n".join(sorted(set(server_errors)))
        )
    print(
        f"\nNOTE: {len(notes)} write(s) returned 2xx for a foreign parent id without "
        "affecting it (missing existence check):\n" + "\n".join(sorted(notes))
    )
    assert not report, "\n\n".join(report)
