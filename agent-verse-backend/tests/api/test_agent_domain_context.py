"""QA-14: an agent's domain_context / domain_metadata are saved, not just echoed.

POST /agents accepted both fields and echoed them from the in-memory record, but
``_db_persist_agent`` never wrote them, ``_row_to_dict`` never returned them and
the ORM model had no columns - although migration 0053 created
``agents.domain_context`` / ``agents.domain_metadata``. The agent identity
service reads ``a.domain_context`` from Postgres, so every agent was "general"
and domain checks never applied.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore, UpdateAgentRequest
from app.api.agents import router as agents_router
from app.db.models.agent import Agent
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests._rls_recorder import RlsRecordingDb, RlsRecordingSession

_CTX = TenantContext(tenant_id="tid-domain", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_test_domainkey"
_H = {"X-API-Key": _KEY}
_LEGAL = {"domain_context": "legal", "domain_metadata": {"bar_number": "CA12345"}}


def _client(store: AgentStore | None = None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(agents_router)
    app.state.agent_store = store or AgentStore()
    app.state.meta_agent = AsyncMock()
    return TestClient(app, raise_server_exceptions=False)


class _AddingSession(RlsRecordingSession):
    def add(self, obj: Any) -> None:
        self._db.added.append(obj)  # type: ignore[attr-defined]


class _AddingDb(RlsRecordingDb):
    def __init__(self) -> None:
        super().__init__()
        self.added: list[Any] = []

    def __call__(self) -> _AddingSession:
        return _AddingSession(self)


def test_orm_model_has_the_migration_0053_columns() -> None:
    cols = Agent.__table__.columns
    assert "domain_context" in cols
    assert "domain_metadata" in cols
    assert cols["domain_context"].nullable is False
    assert cols["domain_metadata"].nullable is False


async def test_db_persist_agent_writes_domain_fields() -> None:
    db = _AddingDb()
    store = AgentStore(db_session_factory=db)
    await store.create({"name": "lawyer", **_LEGAL}, tenant_ctx=_CTX)
    [row] = db.added
    assert row.domain_context == "legal"
    assert row.domain_metadata == {"bar_number": "CA12345"}


async def test_db_persist_agent_defaults_domain_fields() -> None:
    db = _AddingDb()
    store = AgentStore(db_session_factory=db)
    await store.create({"name": "plain"}, tenant_ctx=_CTX)
    [row] = db.added
    assert row.domain_context == "general"
    assert row.domain_metadata == {}


def test_row_to_dict_returns_domain_fields() -> None:
    row = SimpleNamespace(
        id="a1",
        tenant_id="t1",
        name="n",
        goal_template="",
        autonomy_mode="supervised",
        connector_ids=[],
        trigger_config={},
        created_at=None,
        domain_context="healthcare",
        domain_metadata={"npi": "1234567890"},
    )
    rec = AgentStore._row_to_dict(row)
    assert rec["domain_context"] == "healthcare"
    assert rec["domain_metadata"] == {"npi": "1234567890"}


def test_row_to_dict_defaults_missing_domain_fields() -> None:
    row = SimpleNamespace(
        id="a1",
        tenant_id="t1",
        name="n",
        goal_template="",
        autonomy_mode="supervised",
        connector_ids=[],
        trigger_config={},
        created_at=None,
        domain_context=None,
        domain_metadata=None,
    )
    rec = AgentStore._row_to_dict(row)
    assert rec["domain_context"] == "general"
    assert rec["domain_metadata"] == {}


async def test_update_writes_domain_fields_to_the_db() -> None:
    db = _AddingDb()
    store = AgentStore(db_session_factory=db)
    store._data[(_CTX.tenant_id, "a1")] = {"agent_id": "a1", "name": "n"}

    async def _cached(agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return store._data.get((tenant_ctx.tenant_id, agent_id))

    store.get_async = _cached  # type: ignore[method-assign]
    db.rows_for = lambda sql, _p: [("a1",)] if sql.startswith("UPDATE agents") else []
    await store.update_async("a1", dict(_LEGAL), tenant_ctx=_CTX)
    [upd] = [s for s in db.statements if s.sql.startswith("UPDATE agents")]
    assert "domain_context = :domain_context" in upd.sql
    assert "domain_metadata = CAST(:domain_metadata AS jsonb)" in upd.sql
    assert upd.params["domain_context"] == "legal"


def test_create_get_round_trip_and_update_domain_fields() -> None:
    client = _client()
    resp = client.post("/agents", json={"name": "lawyer", **_LEGAL}, headers=_H)
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["agent_id"]
    got = client.get(f"/agents/{agent_id}", headers=_H).json()
    assert got["domain_context"] == "legal"
    assert got["domain_metadata"] == {"bar_number": "CA12345"}

    upd = client.put(
        f"/agents/{agent_id}",
        json={"domain_context": "healthcare", "domain_metadata": {"npi": "1"}},
        headers=_H,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["domain_context"] == "healthcare"
    assert upd.json()["domain_metadata"] == {"npi": "1"}


def test_update_to_legal_without_bar_number_is_422() -> None:
    client = _client()
    agent_id = client.post("/agents", json={"name": "plain"}, headers=_H).json()["agent_id"]
    resp = client.put(f"/agents/{agent_id}", json={"domain_context": "legal"}, headers=_H)
    assert resp.status_code == 422, resp.text
    assert client.get(f"/agents/{agent_id}", headers=_H).json()["domain_context"] == "general"


def test_update_legal_metadata_must_keep_bar_number() -> None:
    client = _client()
    agent_id = client.post("/agents", json={"name": "lawyer", **_LEGAL}, headers=_H).json()[
        "agent_id"
    ]
    resp = client.put(
        f"/agents/{agent_id}", json={"domain_metadata": {"jurisdiction": "CA"}}, headers=_H
    )
    assert resp.status_code == 422, resp.text
    ok = client.put(
        f"/agents/{agent_id}",
        json={"domain_metadata": {"bar_number": "NY1", "jurisdiction": "NY"}},
        headers=_H,
    )
    assert ok.status_code == 200, ok.text


def test_update_request_accepts_domain_fields() -> None:
    req = UpdateAgentRequest(domain_context="finance", domain_metadata={"crd": "42"})
    assert req.domain_context == "finance"
    assert req.domain_metadata == {"crd": "42"}


def test_clone_carries_domain_fields() -> None:
    client = _client()
    agent_id = client.post("/agents", json={"name": "lawyer", **_LEGAL}, headers=_H).json()[
        "agent_id"
    ]
    clone = client.post(f"/agents/{agent_id}/clone", headers=_H)
    assert clone.status_code == 201, clone.text
    assert clone.json()["domain_context"] == "legal"
    assert clone.json()["domain_metadata"] == {"bar_number": "CA12345"}
