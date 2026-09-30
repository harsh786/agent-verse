"""TRG-27: typed-webhook token lookup is the indexed DB lookup, and an outage is a 503.

Every pre-auth delivery compared the token against every cached schedule
before the DB lookup, and on a DB error fell back to this replica's cache and
answered 404 — which senders treat as permanent (the trigger is dead to them).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, ScheduleStoreUnavailableError

TOKEN = "t" * 40


class _Down:
    """A session factory whose database is unreachable."""

    def __call__(self) -> Any:
        raise ConnectionError("db down")


class _Row:
    def __init__(self, tenant_id: str, token: str) -> None:
        self._row = (tenant_id, token)

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, row: Any) -> None:
        self._row = row
        self.queries = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        if "webhook_token" in str(stmt):
            self.queries += 1
            return self._row
        return _Row("", "")


async def _cached_store(tenant: str = "stale") -> ScheduleStore:
    store = ScheduleStore()
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.FREE, api_key_id="k")
    await store.create_async(
        goal_id="g",
        spec=TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token=TOKEN),
        tenant_ctx=ctx,
    )
    return store


async def test_db_lookup_is_authoritative_over_the_cache() -> None:
    store = await _cached_store("stale")  # this replica's cache disagrees
    session = _Session(_Row("owner", TOKEN))

    tenant = await store.find_tenant_by_webhook_token(TOKEN, system_db=lambda: session)

    assert tenant == "owner"
    assert session.queries == 1


async def test_db_outage_raises_instead_of_trusting_the_cache() -> None:
    store = await _cached_store()
    with pytest.raises(ScheduleStoreUnavailableError):
        await store.find_tenant_by_webhook_token(TOKEN, system_db=_Down())


async def test_strict_type_lookup_raises_on_outage() -> None:
    store = ScheduleStore(db_session_factory=_Down())
    with pytest.raises(ScheduleStoreUnavailableError):
        await store.find_by_type_async("webhook", tenant_id="t1", strict=True)


def test_typed_webhook_answers_503_when_the_store_is_down() -> None:
    class _Store:
        async def find_tenant_by_webhook_token(self, *_a: Any, **_k: Any) -> Any:
            raise ScheduleStoreUnavailableError("db down")

    app = FastAPI()
    app.include_router(triggers_router)
    app.state.schedule_store = _Store()
    app.state.trigger_dispatcher = object()
    client = TestClient(app, raise_server_exceptions=False)

    r = client.post(f"/triggers/webhooks/github/{TOKEN}", json={})

    assert r.status_code == 503, r.text
