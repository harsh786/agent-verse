"""P8-1: a tenant's guardrail rules reach the worker and apply to its output.

Live GOV-PII-GUARDRAIL: ``POST /guardrails`` kept the rule in the API process
only (its DB insert failed under ``except: pass``); the worker never bound the
rule repository, so only the in-memory baseline applied there; nothing applied
a tenant REDACT rule to a step's output, and the email/phone came back raw.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.guardrails_v2 import engine as engine_mod
from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.models import (
    GuardrailAction,
    GuardrailLayer,
    GuardrailRule,
    ViolationCategory,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_T = "t-worker-pii"
_CTX = TenantContext(tenant_id=_T, plan=PlanTier.ENTERPRISE, api_key_id="k")
_KEY = "ak_worker_pii"
EMAIL = "ravi.menon@bramblewood-freight.example"
PHONE = "+91 98450 12345"
CARD = f"Ravi Menon, email {EMAIL}, mobile {PHONE}."


class _Repo:
    """A rule repository double (records upserts; can fail)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: dict[str, GuardrailRule] = {}
        self.fail = fail

    async def upsert(self, rule: GuardrailRule) -> None:
        if self.fail:
            raise ConnectionError("db down")
        self.rows[rule.rule_id] = rule

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        return [r for r in self.rows.values() if r.tenant_id == tenant_id]

    async def delete(self, tenant_id: str, rule_id: str) -> None:
        self.rows.pop(rule_id, None)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> GuardrailsEngine:
    """A fresh process-singleton engine (no repository) for the test."""
    fresh = GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)
    return fresh


def _pii_redact_rule() -> GuardrailRule:
    return GuardrailRule(
        rule_id="r-pii", tenant_id=_T, name="pii out", rule_type="pii_detection",
        layers=[GuardrailLayer.FINAL_OUTPUT, GuardrailLayer.TOOL_OUTPUT],
        action=GuardrailAction.REDACT, categories=[ViolationCategory.PII],
    )


# ── the API side: a config is a durable rule ───────────────────────────────


def _client() -> TestClient:
    from app.api.guardrails import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_post_guardrails_persists_a_v2_rule_through_the_repository(
    engine: GuardrailsEngine,
) -> None:
    repo = _Repo()
    engine.bind_repository(repo)
    resp = _client().post("/guardrails", headers={"X-API-Key": _KEY}, json={
        "name": "pii-output", "layers": ["final", "output"], "rule_type": "pii",
        "config": {"entities": ["email", "phone"]}, "severity": "high", "action": "redact"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["durable"] is True
    stored = repo.rows[resp.json()["id"]]
    assert stored.tenant_id == _T
    assert stored.rule_type == "pii_detection" and stored.action == GuardrailAction.REDACT
    assert stored.layers == [GuardrailLayer.FINAL_OUTPUT, GuardrailLayer.TOOL_OUTPUT]
    assert stored.categories == [ViolationCategory.PII]
    listed = _client().get("/guardrails", headers={"X-API-Key": _KEY}).json()
    assert [c["name"] for c in listed["configs"]] == ["pii-output"]
    assert listed["configs"][0]["config"] == {"entities": ["email", "phone"]}


def test_post_guardrails_fails_closed_when_the_rule_cannot_be_saved(
    engine: GuardrailsEngine,
) -> None:
    engine.bind_repository(_Repo(fail=True))
    resp = _client().post("/guardrails", headers={"X-API-Key": _KEY},
                          json={"name": "pii", "rule_type": "pii", "action": "redact"})
    assert resp.status_code == 503
    assert engine.all_rules(_T) == []


@pytest.mark.parametrize("field,value", [("rule_type", "astrology"), ("layers", ["moon"]),
                                         ("action", "explode")])
def test_unsupported_config_values_are_refused(
    engine: GuardrailsEngine, field: str, value: Any
) -> None:
    body = {"name": "x", "rule_type": "pii", "action": "block", field: value}
    resp = _client().post("/guardrails", headers={"X-API-Key": _KEY}, json=body)
    assert resp.status_code == 422
    assert engine.all_rules(_T) == []


# ── the worker side: the repository is bound on every worker path ───────────


def test_worker_binding_resolves_the_current_session_factory(
    engine: GuardrailsEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.guardrails_v2.worker_binding import bind_worker_guardrail_rules

    sessions: list[str] = []
    monkeypatch.setattr("app.db.session.get_session_factory",
                        lambda: (lambda: sessions.append("s") or "session"))
    assert bind_worker_guardrail_rules() is True
    assert bind_worker_guardrail_rules() is False  # idempotent
    assert engine.has_repository
    assert engine._repo._sessions() == "session"  # the live factory, per session
    assert sessions == ["s"]


def test_workflow_worker_binds_the_tenant_rule_repository(
    engine: GuardrailsEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workflow import celery_tasks

    monkeypatch.setattr(celery_tasks, "_WORKER_RUNNER", None)
    try:
        celery_tasks._build_worker_runner()
    finally:
        monkeypatch.setattr(celery_tasks, "_WORKER_RUNNER", None)
    assert engine.has_repository


# ── the agent applies the tenant rule to a step's output ─────────────────────


class _Logger:
    def warning(self, *_: Any, **__: Any) -> None:
        return None


def _executor(events: list[dict[str, Any]]) -> Any:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    ex = ExecutorMixin.__new__(ExecutorMixin)
    ex._logger = _Logger()

    async def _emit(event: dict[str, Any]) -> None:
        events.append(event)

    ex._emit = _emit
    return ex


async def test_tenant_redact_rule_redacts_a_step_output(engine: GuardrailsEngine) -> None:
    import app.agent.nodes.executor_mixin as ex_mod

    engine.add_rule(_pii_redact_rule())
    events: list[dict[str, Any]] = []
    state = SimpleNamespace(goal_id="g1", context={})
    with patch.object(ex_mod, "guardrails_engine", engine):
        out = await _executor(events)._screen_step_output("write the card", CARD, state, _CTX)
    assert EMAIL not in out and "98450" not in out
    assert events == [{"type": "pii_redacted", "issues": ["pii out"], "scope": "step_output"}]


async def test_without_the_tenant_rule_the_step_output_is_unchanged(
    engine: GuardrailsEngine,
) -> None:
    import app.agent.nodes.executor_mixin as ex_mod

    events: list[dict[str, Any]] = []
    state = SimpleNamespace(goal_id="g1", context={})
    with patch.object(ex_mod, "guardrails_engine", engine):
        out = await _executor(events)._screen_step_output("write the card", CARD, state, _CTX)
    assert out == CARD and events == []


async def test_tenant_block_rule_withholds_a_step_output(engine: GuardrailsEngine) -> None:
    import dataclasses

    import app.agent.nodes.executor_mixin as ex_mod

    engine.add_rule(dataclasses.replace(_pii_redact_rule(), action=GuardrailAction.BLOCK))
    events: list[dict[str, Any]] = []
    state = SimpleNamespace(goal_id="g1", context={})
    with patch.object(ex_mod, "guardrails_engine", engine):
        out = await _executor(events)._screen_step_output("write the card", CARD, state, _CTX)
    assert out == "[Output blocked by guardrail policy]"
    assert events[0]["type"] == "guardrail_blocked"


async def test_an_unloadable_rule_set_withholds_high_risk_output(
    engine: GuardrailsEngine,
) -> None:
    import app.agent.nodes.executor_mixin as ex_mod

    class _Down(_Repo):
        async def load(self, tenant_id: str) -> list[GuardrailRule]:
            raise ConnectionError("db down")

    engine.bind_repository(_Down())
    state = SimpleNamespace(goal_id="g1", context={"_risk_level": "high"})
    with patch.object(ex_mod, "guardrails_engine", engine):
        out = await _executor([])._screen_step_output("write the card", CARD, state, _CTX)
    assert EMAIL not in out and "withheld" in out


def test_international_phone_numbers_are_pii() -> None:
    import re

    from app.guardrails_v2.engine import _PII_PATTERNS

    hits = {label for p, label in _PII_PATTERNS if re.search(p, CARD)}
    assert {"Email", "International phone"} <= hits
    assert not any(re.search(p, "order 2026-01-01 total 1290 units") for p, _ in _PII_PATTERNS
                   if _ in {"International phone"})
