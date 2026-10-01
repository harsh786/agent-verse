"""TRG-29: /triggers reads are strict — a DB outage is a 503, not a stale cache.

GET/list on /triggers read non-strict, so during a Postgres outage they answered
from this replica's per-process cache (possibly missing or stale triggers),
unlike /schedules which already returned 503.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore


@pytest.fixture(autouse=True)
def _no_tenant_envelope_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake sessions here model only the store's own table: no tenant has an
    envelope key (tenant_vault_keys is covered by test_tenant_envelope_all)."""

    async def _no_key(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.providers.tenant_vault.ensure_tenant_vault", _no_key)


_CTX = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _FlakyDb:
    """Works for the create, then the database goes away."""

    def __init__(self) -> None:
        self.down = False

    def __call__(self) -> Any:
        if self.down:
            raise ConnectionError("db down")
        return _Session()


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    def add(self, _obj: Any) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(
            scalar=lambda: 0,
            scalar_one=lambda: 0,
            rowcount=1,
            fetchall=list,
            scalars=lambda: SimpleNamespace(all=list),
        )


def _client(store: ScheduleStore) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = _CTX
        return await call_next(request)

    app.include_router(triggers_router)
    app.state.schedule_store = store
    return TestClient(app, raise_server_exceptions=False)


async def test_list_and_get_answer_503_during_a_db_outage() -> None:
    db = _FlakyDb()
    store = ScheduleStore(db_session_factory=db)
    sid = await store.create_async(
        goal_id="g",
        spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
        tenant_ctx=_CTX,
    )
    assert store.get(sid, tenant_ctx=_CTX) is not None  # cached on this replica
    db.down = True
    client = _client(store)

    assert client.get("/triggers").status_code == 503
    assert client.get(f"/triggers/{sid}").status_code == 503
