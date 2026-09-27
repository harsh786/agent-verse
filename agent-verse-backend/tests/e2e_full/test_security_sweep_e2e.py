"""e2e_full: automated authentication / credential-scope sweep over the ENTIRE API.

Driven by the app's own OpenAPI schema, so every operation that exists is
exercised — including ones added after this test was written. Runs against the
real booted app (real lifespan, Postgres, Redis).

* **Unauthenticated sweep.** Every operation, called with no credentials, must
  be rejected (401/403) unless its path is deliberately public (health, docs,
  signup, SSO, and the webhook receivers that carry their own signature auth).

* **Stream-token scope.** ``GET /tenants/stream-token`` mints a short-lived
  token that is *designed* to travel in URLs (EventSource cannot set headers),
  i.e. it will land in access logs, proxy logs and browser history. It is
  documented as read-only; it must not be able to perform any write.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# Paths that are public by design. Anything else answering without credentials
# is a finding. Mirrors app.tenancy.middleware._BYPASS_PREFIXES plus the
# unversioned discovery documents.
_PUBLIC_PREFIXES = (
    "/health",
    "/v1/health",
    "/v1/info",
    "/metrics",
    "/status",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/.well-known/",
    "/tenants/signup",
    "/auth/login",
    "/auth/callback",
    "/auth/config",
    "/auth/token",
    "/integrations/",
    "/billing/webhook",
    "/wf-hooks/",
    "/scim/v2",
    "/v1/gateway/",
)

_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _operations(app: Any) -> list[tuple[str, str, dict[str, Any]]]:
    spec = app.openapi()
    ops = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method.upper() in ("GET", *_WRITE_METHODS):
                ops.append((method.upper(), path, op))
    return ops


def _fill(path: str, op: dict[str, Any]) -> str:
    """Substitute every path parameter with a type-appropriate dummy value."""
    types = {
        p["name"]: (p.get("schema") or {}).get("type", "string")
        for p in op.get("parameters", [])
        if p.get("in") == "path"
    }

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1).split(":")[0]
        return "1" if types.get(name) == "integer" else uuid.uuid4().hex

    return re.sub(r"\{([^}]+)\}", _sub, path)


async def _call(client: Any, method: str, url: str, **kw: Any) -> int:
    try:
        resp = await asyncio.wait_for(
            client.request(method, url, content=json.dumps({}), **kw), timeout=15
        )
        return resp.status_code
    except TimeoutError:
        return -1  # hung: reported separately, never counted as "rejected"


async def test_every_non_public_operation_rejects_unauthenticated_callers(
    app: Any, client: Any
) -> None:
    offenders: list[str] = []
    checked = 0
    for method, path, op in _operations(app):
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            continue
        checked += 1
        status = await _call(
            client, method, _fill(path, op), headers={"content-type": "application/json"}
        )
        if status not in (401, 403):
            offenders.append(f"{status} {method} {path}")
    assert checked > 700, f"sweep only covered {checked} operations — schema discovery broke"
    assert not offenders, (
        f"{len(offenders)} operation(s) answered an unauthenticated caller:\n"
        + "\n".join(sorted(offenders))
    )


async def test_stream_token_cannot_perform_writes(app: Any, tenant_client: Any) -> None:
    """A URL-borne, log-exposed token must be read-only in fact, not in name."""
    resp = await tenant_client.get("/tenants/stream-token")
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]

    from httpx import ASGITransport, AsyncClient

    accepted: list[str] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e-full") as bare:
        for method, path, op in _operations(app):
            if method not in _WRITE_METHODS:
                continue
            if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                continue
            status = await _call(
                bare,
                method,
                _fill(path, op),
                params={"token": token},
                headers={"content-type": "application/json"},
            )
            # 401/403: rejected. 405: method not allowed. Anything else means the
            # token got past authentication for a write (a 404/422 still proves
            # the request was authenticated and reached the handler).
            if status not in (401, 403, 405):
                accepted.append(f"{status} {method} {path}")
    assert not accepted, (
        f"the read-only stream token authenticated {len(accepted)} write operation(s):\n"
        + "\n".join(sorted(accepted)[:60])
    )


# Tables that carry a tenant_id column but are intentionally cross-tenant or
# pre-authentication (checked individually; each needs a reason).
_RLS_EXEMPT = {
    "tenants": "the tenant registry itself; read during API-key resolution before any tenant is known",
    "api_keys": "resolved by key hash during authentication, before a tenant context exists",
}


@pytest.mark.xfail(
    strict=True,
    reason="Open finding: ~37 tenant tables still lack ENABLE+FORCE RLS with a policy "
    "(listed by this test). Their access paths must be moved off system_session "
    "first; remove this marker when the audit is clean.",
)
async def test_every_tenant_table_has_forced_rls_with_a_policy(app: Any) -> None:
    """Checked against the LIVE migrated schema, not migration text.

    Migration-text sweeps miss tables created in loops or f-strings, tables with
    digits in their names, and later migrations that temporarily NO FORCE a table
    — an earlier sweep here missed ``a2a_tasks`` for exactly that reason. The
    catalog is the ground truth: every table holding tenant data must have RLS
    enabled AND forced (so the owner role the default DATABASE_URL uses cannot
    bypass it) AND at least one policy.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    async with (
        app.state.db_session_factory() as session,
        session.begin(),
        system_session(session),
    ):
        rows = (
            await session.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                      AND NOT c.relispartition
                      AND EXISTS (
                          SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                            AND NOT a.attisdropped
                      )
                    ORDER BY c.relname
                    """
                )
            )
        ).fetchall()

    assert len(rows) > 50, f"only {len(rows)} tenant tables found — catalog query broke"
    problems = []
    for name, enabled, forced, policies in rows:
        if name in _RLS_EXEMPT:
            continue
        missing = [
            label
            for label, ok in (("ENABLE", enabled), ("FORCE", forced), ("POLICY", policies > 0))
            if not ok
        ]
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")
    assert not problems, (
        f"{len(problems)} tenant table(s) without enforced isolation:\n" + "\n".join(problems)
    )


async def test_stream_token_only_opens_streaming_endpoints(app: Any, tenant_client: Any) -> None:
    """Read scope too: the URL-borne token is for EventSource, not general reads.

    Otherwise anyone who lifts it from a proxy log reads the tenant's goals,
    knowledge, audit trail and so on for its lifetime.
    """
    token = (await tenant_client.get("/tenants/stream-token")).json()["token"]
    from httpx import ASGITransport, AsyncClient

    leaked: list[str] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e-full") as bare:
        for method, path, op in _operations(app):
            if method != "GET" or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                continue
            if path.rstrip("/").rsplit("/", 1)[-1] in ("stream", "events"):
                continue
            status = await _call(bare, "GET", _fill(path, op), params={"token": token})
            if status not in (401, 403):
                leaked.append(f"{status} GET {path}")
    assert not leaked, (
        f"the stream token authenticated {len(leaked)} non-streaming read(s):\n"
        + "\n".join(sorted(leaked)[:60])
    )
