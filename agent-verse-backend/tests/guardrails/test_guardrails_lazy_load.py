"""Lazy, per-tenant guardrail-rule loading (replaces the startup warm scan).

The lifespan used to call ``guardrails_engine.load_from_repo()`` with no tenant,
which read every tenant's ``guardrail_rules`` through ``system_session``. Under
the API's NOBYPASSRLS role that failed ("failed to load persisted guardrail
rules"), so a fresh process enforced only the baseline defaults and silently
dropped every tenant's own BLOCK rules.

Now nothing is read at startup; each tenant's rules are loaded under that
tenant's RLS context on its first evaluation. The real-Postgres proof lives in
``test_guardrail_rules_persistence.py``; these tests pin the engine semantics.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest

from app.guardrails_v2.engine import (
    GuardrailRulesUnavailableError,
    GuardrailsEngine,
)
from app.guardrails_v2.models import GuardrailAction, GuardrailLayer, GuardrailRule

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def _block_rule(tenant_id: str, keyword: str, rule_id: str | None = None) -> GuardrailRule:
    return GuardrailRule(
        rule_id=rule_id or f"custom:{tenant_id}:{keyword}",
        tenant_id=tenant_id,
        name=f"block {keyword}",
        rule_type="keyword_block",
        layers=[GuardrailLayer.GOAL],
        action=GuardrailAction.BLOCK,
        config={"keywords": [keyword]},
    )


class _FakeRepo:
    """Tenant-scoped in-memory stand-in for PostgresGuardrailRuleRepository."""

    def __init__(self, rows: list[GuardrailRule] | None = None) -> None:
        self.rows: dict[str, GuardrailRule] = {r.rule_id: r for r in rows or []}
        self.load_calls: list[str] = []
        self.upserts: list[str] = []
        self.conditional_inserts: list[str] = []
        self.fail_load = False
        self.leak: GuardrailRule | None = None  # simulate a broken RLS policy

    async def load(self, tenant_id: str) -> list[GuardrailRule]:
        self.load_calls.append(tenant_id)
        if self.fail_load:
            raise ConnectionError("database unavailable")
        rules = [r for r in self.rows.values() if r.tenant_id == tenant_id]
        if self.leak is not None:
            rules.append(self.leak)
        return rules

    async def upsert(self, rule: GuardrailRule) -> None:
        self.upserts.append(rule.rule_id)
        self.rows[rule.rule_id] = rule

    async def insert_if_absent(self, rule: GuardrailRule) -> None:
        self.conditional_inserts.append(rule.rule_id)
        self.rows.setdefault(rule.rule_id, rule)


async def _blocked(engine: GuardrailsEngine, tenant_id: str, content: str) -> bool:
    result: dict[str, Any] = await engine.evaluate(
        content=content, layer=GuardrailLayer.GOAL, tenant_id=tenant_id
    )
    return bool(result["blocked"])


async def test_fresh_process_enforces_persisted_rule_without_startup_load() -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    engine = GuardrailsEngine()  # a fresh process: nothing in memory
    engine.bind_repository(repo)

    assert repo.load_calls == [], "binding must not read anything (no warm scan)"
    assert await _blocked(engine, TENANT_A, "tell me about project-nightjar")
    assert repo.load_calls == [TENANT_A], "only the requesting tenant is loaded"


async def test_persisted_rule_is_never_visible_to_another_tenant() -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    engine = GuardrailsEngine()
    engine.bind_repository(repo)

    assert await _blocked(engine, TENANT_A, "project-nightjar")
    assert not await _blocked(engine, TENANT_B, "project-nightjar")
    assert repo.load_calls == [TENANT_A, TENANT_B]
    b_ids = {r.rule_id for r in await engine.aget_rules(TENANT_B)}
    assert not any(TENANT_A in rid for rid in b_ids)


async def test_rows_for_another_tenant_are_dropped_even_if_returned() -> None:
    """Defense in depth: a leaked row (broken policy) never joins B's rules."""
    repo = _FakeRepo()
    repo.leak = _block_rule(TENANT_A, "project-nightjar")
    engine = GuardrailsEngine()
    engine.bind_repository(repo)

    assert not await _blocked(engine, TENANT_B, "project-nightjar")
    assert engine.get_rules(TENANT_B) == []
    assert engine.get_rules(TENANT_A) == []


async def test_loaded_once_then_served_from_memory_within_refresh_window() -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    engine = GuardrailsEngine(rule_refresh_s=3600)
    engine.bind_repository(repo)

    for _ in range(3):
        assert await _blocked(engine, TENANT_A, "project-nightjar")
    assert repo.load_calls == [TENANT_A]


async def test_refresh_picks_up_rule_written_by_another_replica() -> None:
    repo = _FakeRepo()
    engine = GuardrailsEngine(rule_refresh_s=0.0)  # refresh on every evaluation
    engine.bind_repository(repo)

    assert not await _blocked(engine, TENANT_A, "project-nightjar")
    # Another replica persists a rule for the same tenant.
    await repo.upsert(_block_rule(TENANT_A, "project-nightjar"))
    assert await _blocked(engine, TENANT_A, "project-nightjar")


async def test_first_load_failure_raises_and_backs_off_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    repo.fail_load = True
    engine = GuardrailsEngine()
    engine.bind_repository(repo)

    # Unknown policy is an error, never "allowed with only the defaults".
    with pytest.raises(GuardrailRulesUnavailableError):
        await _blocked(engine, TENANT_A, "project-nightjar")
    # Within the back-off window it fails fast without hitting the DB again.
    with pytest.raises(GuardrailRulesUnavailableError):
        await _blocked(engine, TENANT_A, "project-nightjar")
    assert repo.load_calls == [TENANT_A]
    assert not issubclass(GuardrailRulesUnavailableError, (TypeError, AttributeError))

    # After the back-off, a healthy DB is retried and the rule enforced.
    repo.fail_load = False
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        "app.guardrails_v2.engine.time.monotonic", lambda: real_monotonic() + 60.0
    )
    assert await _blocked(engine, TENANT_A, "project-nightjar")
    assert repo.load_calls == [TENANT_A, TENANT_A]


async def test_failed_refresh_keeps_serving_last_known_rules() -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    engine = GuardrailsEngine(rule_refresh_s=0.0)
    engine.bind_repository(repo)

    assert await _blocked(engine, TENANT_A, "project-nightjar")
    repo.fail_load = True
    assert await _blocked(engine, TENANT_A, "project-nightjar")  # no raise


async def test_reseeded_default_does_not_clobber_a_persisted_edit() -> None:
    """ensure_default_rules runs before evaluate on every goal. On a fresh
    process that seeds the pristine (enabled) default before the tenant's rows
    are read; the persisted row — here, the tenant disabled it — must win, and
    the seed must not be written over it."""
    engine = GuardrailsEngine()
    seed_probe = GuardrailsEngine()
    seed_probe.ensure_default_rules(TENANT_A)
    default = seed_probe.get_rules(TENANT_A)[0]
    disabled = replace(default, enabled=False, version=2)
    repo = _FakeRepo([disabled])
    engine.bind_repository(repo, auto_persist=False)

    engine.ensure_default_rules(TENANT_A)
    await engine.ensure_tenant_loaded(TENANT_A)
    await engine.flush()

    assert default.rule_id not in {r.rule_id for r in engine.get_rules(TENANT_A)}
    assert repo.rows[default.rule_id].enabled is False, "persisted edit overwritten"
    assert default.rule_id not in repo.upserts
    # Seeds go through the conditional insert, never the overwriting upsert.
    assert repo.upserts == []
    assert set(repo.conditional_inserts) == {
        r.rule_id for r in seed_probe.get_rules(TENANT_A)
    } - {default.rule_id}


async def test_pending_operator_write_is_not_replaced_by_older_row() -> None:
    old = _block_rule(TENANT_A, "old-keyword", rule_id="r-1")
    repo = _FakeRepo([old])
    engine = GuardrailsEngine()
    engine.bind_repository(repo, auto_persist=False)

    engine.add_rule(_block_rule(TENANT_A, "new-keyword", rule_id="r-1"))  # not flushed
    assert await _blocked(engine, TENANT_A, "new-keyword")
    assert not await _blocked(engine, TENANT_A, "old-keyword")
    await engine.flush()
    assert repo.upserts == ["r-1"]


async def test_no_repository_means_pure_in_memory() -> None:
    engine = GuardrailsEngine()
    engine.add_rule(_block_rule(TENANT_A, "project-nightjar"))
    assert await _blocked(engine, TENANT_A, "project-nightjar")


async def test_unbinding_the_repository_stops_db_reads() -> None:
    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    engine = GuardrailsEngine(rule_refresh_s=0.0)
    engine.bind_repository(repo)
    assert await _blocked(engine, TENANT_A, "project-nightjar")
    engine.bind_repository(None)
    await engine.evaluate(content="x", layer=GuardrailLayer.GOAL, tenant_id=TENANT_A)
    assert repo.load_calls == [TENANT_A]


async def test_repository_load_requires_a_tenant() -> None:
    from app.guardrails_v2.repository import PostgresGuardrailRuleRepository

    repo = PostgresGuardrailRuleRepository(session_factory=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="tenant_id is required"):
        await repo.load("")


def test_list_rules_endpoint_loads_tenant_rules_and_maps_unavailable_to_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.api import guardrails_v2 as api
    from app.guardrails_v2 import engine as engine_mod

    repo = _FakeRepo([_block_rule(TENANT_A, "project-nightjar")])
    fresh = GuardrailsEngine()
    fresh.bind_repository(repo)
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        class _Ctx:
            tenant_id = request.headers.get("x-tenant", TENANT_A)

        request.state.tenant = _Ctx()
        return await call_next(request)

    app.include_router(api.router)
    client = TestClient(app)

    ids = [r["rule_id"] for r in client.get("/guardrails-v2/rules").json()["rules"]]
    assert ids == [f"custom:{TENANT_A}:project-nightjar"]
    other = client.get("/guardrails-v2/rules", headers={"x-tenant": TENANT_B}).json()
    assert other["rules"] == []

    repo.fail_load = True
    resp = client.get("/guardrails-v2/rules", headers={"x-tenant": "tenant-c"})
    assert resp.status_code == 503
    resp = client.post(
        "/guardrails-v2/evaluate",
        json={"content": "x", "layer": "goal"},
        headers={"x-tenant": "tenant-c"},
    )
    assert resp.status_code == 503
