"""P8b-1: PII / secrets in a step output are redacted on EVERY user-facing surface.

The baseline FINAL_OUTPUT PII rule only replaced ``cited_answer``; GET
/goals/{id}'s ``result_artifact`` is the last step's output (read from the
goal's events), so without a tenant output rule an email, phone, card number or
secret in a step output was served raw — by GET /goals/{id}, its events, the SSE
stream and replay / timeline — and stored raw in ``goal_events``.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailRulesUnavailableError, GuardrailsEngine
from app.guardrails_v2.models import (
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
)
from app.guardrails_v2.output_screening import (
    BLOCKED_OUTPUT,
    SCREENED_FLAG,
    WITHHELD_OUTPUT,
    redact_legacy_event,
    screen_goal_event,
)
from app.tenancy.context import PlanTier, TenantContext

_T = "t-out-screen"
_CTX = TenantContext(tenant_id=_T, plan=PlanTier.ENTERPRISE, api_key_id="k")
EMAIL = "ravi.menon@bramblewood-freight.example"
PHONE = "+91 98450 12345"
CARD = "4111 1111 1111 1111"
SECRET = "sk-" + "proj" + "A1b2C3d4E5f6G7h8I9j0K1l2"  # split: secret scanners
RAW = [EMAIL, PHONE, CARD, SECRET]
OUTPUT = f"Contact card: {EMAIL}, mobile {PHONE}, card {CARD}, key {SECRET}. Codename ORCA."


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> GuardrailsEngine:
    fresh = GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)
    return fresh


def _assert_clean(blob: Any) -> None:
    text = json.dumps(blob, default=str)
    for raw in RAW:
        assert raw not in text, f"{raw!r} leaked in {text[:400]}"


def _keyword_rule(action: GuardrailAction) -> GuardrailRule:
    return GuardrailRule(
        rule_id=f"r-kw-{action.value}", tenant_id=_T, name="codename",
        rule_type="keyword_block", layers=[GuardrailLayer.FINAL_OUTPUT],
        action=action, config={"keywords": ["orca"]},
    )


def _step_complete(output: str = OUTPUT) -> dict[str, Any]:
    return {"type": "step_complete", "step": "look up the contact", "step_id": "s-1",
            "output": output, "approval_id": "4111111111111111"}


# ── the screening function ──────────────────────────────────────────────────


async def test_baseline_redacts_pii_and_secrets_without_any_tenant_rule(
    engine: GuardrailsEngine,
) -> None:
    out = await screen_goal_event(_step_complete(), _T)
    assert out is not None
    _assert_clean(out["output"])
    assert "***REDACTED***" in out["output"] and "Codename ORCA" in out["output"]
    assert out[SCREENED_FLAG] is True
    # Structural fields (ids clients key on) are never rewritten.
    assert out["step_id"] == "s-1" and out["approval_id"] == "4111111111111111"
    assert out["type"] == "step_complete"


async def test_nested_outputs_are_screened(engine: GuardrailsEngine) -> None:
    event = {"type": "tool_call_complete", "tool_name": "crm_lookup",
             "tool_output": {"rows": [{"email": EMAIL, "phone": PHONE}]}, "args": [CARD]}
    out = await screen_goal_event(event, _T)
    assert out is not None
    _assert_clean(out)
    assert out["tool_name"] == "crm_lookup"


async def test_a_tenant_redact_rule_applies_on_top_of_the_baseline(
    engine: GuardrailsEngine,
) -> None:
    engine.add_rule(_keyword_rule(GuardrailAction.REDACT))
    out = await screen_goal_event(_step_complete(), _T)
    assert out is not None
    _assert_clean(out)
    assert "ORCA" not in out["output"]


async def test_a_tenant_block_rule_withholds_the_output(engine: GuardrailsEngine) -> None:
    engine.add_rule(_keyword_rule(GuardrailAction.BLOCK))
    out = await screen_goal_event(_step_complete(), _T)
    assert out is not None
    assert out["output"] == BLOCKED_OUTPUT


async def test_a_tenant_that_disabled_the_baseline_pii_rule_still_gets_secrets_redacted(
    engine: GuardrailsEngine,
) -> None:
    engine.ensure_default_rules(_T)
    await engine.update_rule_durable(_T, f"gr-default:{_T}:pii-final-output", enabled=False)
    out = await screen_goal_event(_step_complete(), _T)
    assert out is not None
    assert EMAIL in out["output"]  # the tenant opted out of PII redaction
    assert SECRET not in out["output"]  # secrets are never served


async def test_unloadable_tenant_rules_fail_closed(
    engine: GuardrailsEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down(tenant_id: str) -> None:
        raise GuardrailRulesUnavailableError("db down")

    monkeypatch.setattr(engine, "ensure_tenant_loaded", _down)
    out = await screen_goal_event(_step_complete(), _T)
    assert out is not None
    assert out["output"] == WITHHELD_OUTPUT
    assert out["step_id"] == "s-1"


async def test_token_stream_is_redacted_and_holds_back_a_value_still_arriving(
    engine: GuardrailsEngine,
) -> None:
    prefix = "x " * 80
    full = f"{prefix}write to {EMAIL} today " + "y " * 60
    seen = ""
    for cut in range(3, len(full) + 1, 3):
        cumulative = full[:cut]
        out = await screen_goal_event(
            {"type": "token_chunk", "step": "s", "token": cumulative[-3:],
             "cumulative": cumulative}, _T)
        assert out is not None
        assert EMAIL[:12] not in out["cumulative"]
        assert out["cumulative"].startswith(seen)
        assert seen + out["token"] == out["cumulative"]
        seen = out["cumulative"]
    assert "***REDACTED***" in seen


async def test_no_live_token_stream_under_tenant_output_rules(engine: GuardrailsEngine) -> None:
    engine.add_rule(_keyword_rule(GuardrailAction.BLOCK))
    out = await screen_goal_event(
        {"type": "token_chunk", "step": "s", "token": "orc", "cumulative": "orc"}, _T)
    assert out is None


def test_legacy_backstop_redacts_unscreened_history_only() -> None:
    legacy = redact_legacy_event(_step_complete())
    _assert_clean(legacy["output"])
    screened = {**_step_complete(output=f"opted-in {EMAIL}"), SCREENED_FLAG: True}
    assert redact_legacy_event(screened) == screened


# ── API path: GoalService (GET /goals/{id}, /events, SSE) ──────────────────


def _service(store: Any = None) -> Any:
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService

    svc = GoalService(event_store=store)
    svc._goals["g-pii"] = GoalRecord(
        goal_id="g-pii", goal_text="write a contact card", status=GoalStatus.EXECUTING,
        tenant_id=_T, priority="normal", dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    return svc


async def _run_goal_events(svc: Any) -> None:
    await svc._dispatch_event("g-pii", {"type": "goal_started", "goal": "write a card"},
                              tenant_ctx=_CTX)
    await svc._dispatch_event("g-pii", _step_complete(), tenant_ctx=_CTX)
    await svc._dispatch_event("g-pii", {"type": "goal_complete"}, tenant_ctx=_CTX)


@pytest.mark.parametrize("tenant_rule", [False, True])
async def test_every_goal_surface_is_redacted_on_the_api_path(
    engine: GuardrailsEngine, tenant_rule: bool
) -> None:
    if tenant_rule:
        engine.add_rule(_keyword_rule(GuardrailAction.REDACT))
    stored: list[dict[str, Any]] = []
    store = MagicMock()

    async def _append(goal_id: str, event: dict[str, Any], *, tenant_ctx: Any) -> int:
        stored.append(json.loads(json.dumps(event)))
        return len(stored)

    store.append_event = AsyncMock(side_effect=_append)
    store.list_events = AsyncMock(side_effect=lambda *a, **k: list(stored))
    store.list_events_since = AsyncMock(
        side_effect=lambda goal_id, after_sequence, limit=100, tenant_ctx=None: [
            {**e, "_seq": i} for i, e in enumerate(stored, 1) if i > after_sequence
        ]
    )
    svc = _service(store)
    await _run_goal_events(svc)

    _assert_clean(stored)  # never raw in the event log
    goal = await svc.get_goal("g-pii", _CTX)  # GET /goals/{id}
    _assert_clean(goal)
    assert "***REDACTED***" in goal["result_artifact"]["summary"]
    _assert_clean(await svc.get_events("g-pii", _CTX))  # /events, insights
    streamed = [e async for e in svc.subscribe_events("g-pii", _CTX)]  # SSE
    assert any(e.get("type") == "step_complete" for e in streamed)
    _assert_clean(streamed)
    if tenant_rule:
        assert "ORCA" not in goal["result_artifact"]["summary"]


async def test_live_sse_subscribers_get_the_redacted_event(engine: GuardrailsEngine) -> None:
    import asyncio

    svc = _service()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    svc._goals["g-pii"].subscribers.append(queue)
    await svc._dispatch_event("g-pii", _step_complete(), tenant_ctx=_CTX)
    _assert_clean(queue.get_nowait())


# ── event store read backstop + replay / timeline ───────────────────────────


async def test_event_store_reads_redact_history_written_before_screening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import event_store as es_mod

    @asynccontextmanager
    async def _no_rls(session: Any, tenant_id: str) -> Any:
        yield

    rows = [SimpleNamespace(payload=_step_complete(), sequence=1)]

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        def begin(self) -> _Session:
            return self

        async def execute(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    monkeypatch.setattr(es_mod, "sqlalchemy_rls_context", _no_rls)
    store = es_mod.EventStore(lambda: _Session())
    _assert_clean(await store.list_events("g", tenant_ctx=_CTX))
    since = await store.list_events_since("g", 0, tenant_ctx=_CTX)
    _assert_clean(since)
    assert since[0]["_seq"] == 1


@pytest.mark.parametrize("path", ["replay", "timeline"])
def test_replay_and_timeline_are_redacted(engine: GuardrailsEngine, path: str) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.replay import router
    from tests._rls_recorder import RlsRecordingDb

    now = datetime.now(UTC)

    def _rows(sql: str, params: dict[str, Any]) -> list[Any]:
        if "FROM goals" in sql:
            return [("g-pii", "write a card", "complete", now, now)]
        if "FROM goal_events" in sql:
            return [(1, "step_complete", _step_complete(), now)]
        if "FROM goal_steps" in sql:
            return [(0, f"mail {EMAIL}", "complete", OUTPUT, f"failed for {PHONE}", [], now)]
        if "FROM decision_traces" in sql:
            return [("act", f"because {CARD}", 0.9, {}, now)]
        return []

    app = FastAPI()
    app.state.db_session_factory = RlsRecordingDb(rows_for=_rows)

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = _CTX
        return await call_next(request)

    app.include_router(router)
    resp = TestClient(app).get(f"/goals/g-pii/{path}")
    assert resp.status_code == 200, resp.text
    _assert_clean(resp.json())
