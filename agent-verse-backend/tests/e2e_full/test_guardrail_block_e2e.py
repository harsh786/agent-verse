"""e2e_full: guardrail output enforcement blocks a leaking secret on the live path.

Row-12 flagship. A goal whose execution produces a hard secret (an AWS access
key) must not have that secret emitted in its final answer — the wired
guardrails_v2 FINAL_OUTPUT gate in the verifier redacts it. Proven against the
booted app with real Postgres+Redis.

Determinism: the tenant's baseline BLOCK rules are seeded via
``guardrails_engine.ensure_default_rules`` (a tenant-admin action), and a pinned
provider makes the executor emit the secret so the gate has something to catch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


class _SecretLeakProvider(FakeProvider):
    """Plans one step; the executor 'leaks' an AWS access key in its output."""

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["print the deployment credentials"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "done"}'
        else:
            content = f"The deployment credentials are {_AWS_KEY} — keep them safe."
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def secret_tenant(app: Any, client: Any) -> AsyncIterator[tuple[Any, str]]:
    """Fresh tenant with baseline guardrail BLOCK rules seeded; yields (client, tenant_id)."""
    from httpx import ASGITransport, AsyncClient

    from app.guardrails_v2.engine import guardrails_engine

    email = f"guard-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Guard", "email": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    api_key, tenant_id = body["api_key"], body["tenant_id"]

    # Seed the tenant's baseline BLOCK rules (injection/PII/secret) — the same
    # engine singleton the verifier's FINAL_OUTPUT gate uses.
    guardrails_engine.ensure_default_rules(tenant_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c, tenant_id


@pytest.fixture
def _inline_secret_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _SecretLeakProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_leaked_secret_is_blocked_from_final_answer(
    secret_tenant: Any, _inline_secret_provider: Any
) -> None:
    client, _tenant_id = secret_tenant

    submit = await client.post("/goals", json={"goal": "Fetch and show the deploy creds"})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    # Poll to terminal.
    deadline = asyncio.get_event_loop().time() + 30.0
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        got = await client.get(f"/goals/{goal_id}")
        if got.status_code == 200:
            last = got.json()
            if str(last.get("status")) in ("complete", "failed"):
                break
        await asyncio.sleep(0.3)
    assert str(last.get("status")) in ("complete", "failed"), f"goal stuck: {last.get('status')!r}"

    # The raw secret must NOT appear anywhere in the returned goal payload — the
    # guardrail gate redacted it. (If it leaked, the whole serialized goal
    # would contain the key.)
    import json as _json

    payload = _json.dumps(last)
    assert _AWS_KEY not in payload, (
        "leaked AWS access key was emitted in the goal result — guardrail "
        "FINAL_OUTPUT enforcement did not redact it"
    )
