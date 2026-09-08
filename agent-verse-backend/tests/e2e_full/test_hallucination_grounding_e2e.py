"""e2e_full: an ungrounded answer on a high-risk goal should be flipped by the
grounding gate (DOCUMENTED REACHABILITY GAP — xfail).

Phase-3 *hallucination* dimension. The desired capability: when a high-risk goal
(``_is_high_risk_step`` keyword — "delete", "production", …) produces a final
answer carrying a concrete claim (a number, id, URL, …) that is **not** supported
by the execution evidence, the verifier's grounding gate flips the verdict
(``success=False, retry=True`` → replan, ultimately failed) rather than letting
the hallucinated answer complete.

Why this is ``xfail(strict=True)`` on the live in-process path:

Both grounding gates are guarded on **non-empty tool-call evidence** — the
verifier gate runs only ``if _final_answer and _evidence`` where
``_evidence = [tc["output"] for step in steps for tc in step.tool_calls if
tc["output"]]`` (``app/agent/nodes/verifier_mixin.py``), and the executor gate on
``_tool_outputs_for_grounding`` (``app/agent/nodes/executor_mixin.py``). A step's
``tool_calls`` is populated only when a real tool actually executes. In the
``e2e_full`` harness there are no executable tools (RPA/Playwright disabled, no
MCP tools registered), so a text-only scripted provider yields **empty**
``step.tool_calls`` → ``_evidence`` is empty → **both gates are skipped**, and the
ungrounded claim on a high-risk goal passes straight through to ``complete``.

So the gap this pins is precise: the grounding gates cannot engage without
tool-call evidence, and the readily-reachable live path (text-only execution)
produces none. When the gate is made reachable for evidence-free ungrounded
answers (e.g. ``check_grounding``'s own "claims present + no evidence →
ungrounded" branch is honoured by the callers instead of the ``and _evidence``
guard), this test should pass and the ``xfail`` be removed.
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
            content = '{"steps": ["Delete the production database and report the row count"]}'
        elif "success" in props:
            # Verifier believes the step succeeded — the grounding gate is the
            # only thing that should catch the unsupported claim.
            content = '{"success": true, "reason": "deletion reported"}'
        else:
            content = f"Deleted the production database. Removed {_UNGROUNDED_CLAIM} rows."
        return CompletionResponse(content=content, model="fake", input_tokens=6, output_tokens=6)


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Grounding gates are guarded on non-empty tool-call evidence "
        "(verifier: 'if _final_answer and _evidence'); the e2e path has no "
        "executable tools, so step.tool_calls is empty, the gate is skipped, and "
        "an ungrounded claim on a high-risk goal completes instead of being "
        "flipped to replan/failed."
    ),
)
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

    # DESIRED: the grounding gate caught the unsupported claim on this high-risk
    # goal — either an explicit grounding event fired, or the verdict was flipped
    # so the goal did not complete with the hallucination intact.
    assert grounding_fired or str(final.get("status")) == "failed", (
        f"ungrounded high-risk answer was not flipped by the grounding gate: "
        f"status={final.get('status')!r}, grounding events={types & {'grounding_blocked', 'grounding_warning', 'claim_grounding_warning'}}"
    )


def _type(raw: str) -> str | None:
    try:
        return str(json.loads(raw).get("type"))
    except Exception:
        return None
