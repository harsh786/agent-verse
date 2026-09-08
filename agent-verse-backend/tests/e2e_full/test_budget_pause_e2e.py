"""e2e_full: budget exhaustion should pause/block a goal (DOCUMENTED GAP — xfail).

Phase-3 *budget-pause* dimension. The desired capability: with the tenant's cost
budget set at/near zero, submitting a goal should surface a budget-driven
*blocked* or *paused* outcome with a budget reason, so a tenant that has spent
its allowance cannot silently keep executing.

This is **not implemented on the live goal path today**, so this test asserts
the desired behavior and is marked ``xfail(strict=True)`` to pin the exact gap:

* ``app.state.cost_controller`` is enforced **only inside the executor, per LLM
  step** (``executor_mixin`` ``check_and_record`` → returns the step observation
  string ``"Step skipped: budget exceeded."``). It never rejects submission and
  never sets a goal-level state.
* ``GoalStatus`` (``app/agent/state.py``) has **no ``BLOCKED`` and no ``PAUSED``**
  value; "paused" exists only via the manual ``pause_goal`` API, which budget
  never triggers.
* ``POST /goals`` performs daily-count and concurrency pre-flight checks but
  **no cost/budget pre-flight** — so an over-budget tenant's goal is accepted
  and runs (steps merely skipped), rather than being blocked with a reason.

When budget enforcement is promoted to a goal-level gate (submission rejection
or a paused/blocked status carrying a budget reason), this test should start
passing and the ``xfail`` marker be removed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _CompletingProvider(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Do the expensive thing"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "ok"}'
        else:
            content = "Did the expensive thing."
        return CompletionResponse(content=content, model="fake", input_tokens=9, output_tokens=9)


@pytest_asyncio.fixture(loop_scope="session")
async def budget_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"budget-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Budget", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _zero_budget_inline(app: Any) -> Any:
    """Pin a zero-budget in-memory CostController and run goals inline."""
    from app.governance.cost import BudgetConfig, CostController

    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    prev_cost = getattr(app.state, "cost_controller", None)

    app.state._llm_provider_override = _CompletingProvider()
    gs._task_queue = None
    # In-memory controller: a 0.0 limit means any positive cost is over budget.
    app.state.cost_controller = CostController(
        BudgetConfig(per_goal_usd=0.0, per_tenant_daily_usd=0.0)
    )
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue
        app.state.cost_controller = prev_cost


@pytest.mark.xfail(
    strict=True,
    reason=(
        "No goal-level budget gate: budget is enforced only per-step in the "
        "executor ('Step skipped: budget exceeded.'); there is no blocked/paused "
        "goal status and no budget reason surfaced at the goal level."
    ),
)
async def test_zero_budget_goal_is_blocked_with_budget_reason(
    budget_client: Any, _zero_budget_inline: Any
) -> None:
    submit = await budget_client.post("/goals", json={"goal": "Run an expensive analysis"})

    # DESIRED: submission is rejected for budget, OR the goal ends in a
    # budget-driven paused/blocked state carrying a budget reason.
    if submit.status_code != 202:
        # A budget-driven rejection would be an acceptable realization of the DoD.
        assert submit.status_code in (402, 429, 403)
        assert "budget" in submit.text.lower()
        return

    goal_id = submit.json()["goal_id"]
    final = await wait_for_status(
        budget_client, goal_id, {"complete", "failed", "paused", "blocked"}, timeout=30.0
    )
    status = str(final.get("status"))
    reason_blob = " ".join(
        str(final.get(k, "")) for k in ("status", "reason", "error", "error_message", "block_reason")
    ).lower()
    assert status in ("paused", "blocked"), f"expected a budget pause/block, got {status!r}"
    assert "budget" in reason_blob, f"no budget reason surfaced: {final!r}"
