"""GET /intelligence/calibration exposes the verifier false-confirm rate (MEM-33).

The rate was computed only from one process's in-memory buffer and had no
route. It is now read from ``verifier_calibration`` under the tenant's RLS
context, so every replica reports the same figure.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import intelligence_router
from app.intelligence.verifier_calibration import VerifierCalibrationStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-cal", plan=PlanTier.ENTERPRISE, api_key_id="k")
_H = {"X-API-Key": "k-cal"}


class _Row:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, db: _DB) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> _Row:
        sql = str(stmt)
        if "set_config" in sql:
            # The first call sets the tenant; the context resets it on exit.
            self._db.guc = self._db.guc or (params or {}).get("tid")
            return _Row(None)
        if self._db.fail:
            raise RuntimeError("db down")
        self._db.params = dict(params or {})
        return _Row(self._db.row)


class _DB:
    def __init__(self, row: Any = None, *, fail: bool = False) -> None:
        self.row, self.fail = row, fail
        self.guc: str | None = None
        self.params: dict[str, Any] = {}

    def __call__(self) -> _Session:
        return _Session(self)


def _client(store: VerifierCalibrationStore) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k-cal" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(intelligence_router)
    app.state.calibration_store = store
    return TestClient(app, raise_server_exceptions=False)


def test_calibration_is_read_from_the_table_under_rls() -> None:
    db = _DB(row=(50, 2, 3))  # resolved, false positives, false negatives
    body = _client(VerifierCalibrationStore(db)).get("/intelligence/calibration", headers=_H)
    assert body.status_code == 200, body.text
    out = body.json()
    assert out["total"] == 50 and out["false_positives"] == 2 and out["false_negatives"] == 3
    assert out["rate"] == 0.04 and out["on_target"] is False
    assert db.guc == _CTX.tenant_id and db.params["tid"] == _CTX.tenant_id


def test_calibration_db_error_is_503() -> None:
    resp = _client(VerifierCalibrationStore(_DB(fail=True))).get(
        "/intelligence/calibration", headers=_H
    )
    assert resp.status_code == 503


async def _seed(store: VerifierCalibrationStore) -> None:
    rid = await store.record_verdict(goal_id="g", tenant_id="t-cal", verifier_verdict=True)
    await store.record_actual_outcome(rid, actual_success=False)


def test_without_a_db_the_process_buffer_answers() -> None:
    import asyncio

    store = VerifierCalibrationStore()
    asyncio.run(_seed(store))
    out = _client(store).get("/intelligence/calibration", headers=_H).json()
    assert out["total"] == 1 and out["false_positives"] == 1
