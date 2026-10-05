"""e2e_full: an ungrounded answer on a high-risk goal is flipped by the grounding gate.

Phase-3 *hallucination* dimension. When a high-risk goal (``_is_high_risk_step``
keyword — "delete", "production", ...) produces a final answer carrying a
concrete claim (a number, id, URL, ...) that no execution evidence supports, the
verifier's grounding gate flips the verdict (``success=False, retry=True`` ->
replan, ultimately failed) instead of letting the hallucinated answer complete.

The e2e harness has no executable tools, so the text-only answer has **no**
evidence at all. The gates used to fail closed only when evidence existed, so
this case completed with a mere warning (CORE-03). The planned step itself is
worded as read/report-only (since HIGH-RISK-GATE-WORDING every step of a
high-risk goal that is not read-only — "how many rows were removed" names a
removal — needs an approval, which bounded-autonomous mode denies first), so
the grounding gate is what must fail the goal.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# A concrete, regex-extractable claim (a multi-digit number) that appears in the
# answer but is supported by no evidence — the hallucination to be caught.
_UNGROUNDED_CLAIM = "4718"


class _UngroundedHighRiskProvider(FakeProvider):
    """Plans a high-risk step; the final answer asserts an unsupported number."""

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Report the row count"]}'
        elif "success" in props:
            # Verifier believes the step succeeded — the grounding gate is the
            # only thing that should catch the unsupported claim.
            content = '{"success": true, "reason": "deletion reported"}'
        else:
            content = f"Deleted the production database. Removed {_UNGROUNDED_CLAIM} rows."
        return CompletionResponse(content=content, model="fake", input_tokens=6, output_tokens=6)

    async def stream_tokens(self, request: Any, on_token: Any) -> Any:  # type: ignore[override]
        # The executor streams: without this it received FakeProvider's canned
        # reply and the unsupported claim never reached the step output.
        resp = await self.complete(request)
        await on_token(resp.content)
        return resp


@pytest_asyncio.fixture(loop_scope="session")
async def halluc_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"halluc-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Halluc", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _inline_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _UngroundedHighRiskProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_ungrounded_high_risk_answer_is_flipped(
    halluc_client: Any, _inline_provider: Any
) -> None:
    submit = await halluc_client.post(
        "/goals",
        json={"goal": "Delete the production database and report how many rows were removed"},
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    final = await wait_for_status(
        halluc_client, goal_id, {"complete", "failed"}, timeout=40.0
    )
    events = await collect_sse(halluc_client, goal_id, timeout=15.0)
    types = {t for t in (_type(raw) for raw in events) if t}

    grounding_fired = bool(
        {"grounding_blocked", "grounding_warning", "claim_grounding_warning"} & types
    )

    # The unsupported claim on this high-risk goal must be caught AND must stop
    # the goal from completing with the hallucination intact.
    assert grounding_fired, f"no grounding event fired: {sorted(types)}"
    assert str(final.get("status")) == "failed", (
        f"ungrounded high-risk answer completed: status={final.get('status')!r}"
    )


def _type(raw: str) -> str | None:
    try:
        return str(json.loads(raw).get("type"))
    except Exception:
        return None
