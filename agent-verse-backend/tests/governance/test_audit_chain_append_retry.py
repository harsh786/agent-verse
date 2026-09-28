"""Concurrent appends colliding on the (tenant_id, seq) PK are retried, never lost.

``api/grants.py`` wrapped ``chain.append`` in ``suppress(Exception)``: a second
writer that read the same tip lost the INSERT race with a unique violation and
its grant audit record silently vanished.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.governance import audit_chain_store
from app.governance.audit_chain_store import AuditChainAppendError, PersistentAuditChain


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def one_or_none(self) -> Any:
        return self._row


def _factory(execute: Any) -> Any:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=session)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    session.execute = AsyncMock(side_effect=execute)
    return lambda: session


def _pk_violation() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("duplicate key value violates pk"))


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_: float) -> None:
        return None

    monkeypatch.setattr(audit_chain_store.asyncio, "sleep", _sleep)


async def test_append_retries_on_seq_collision_and_rechains_on_new_tip() -> None:
    # Attempt 1 reads tip seq=4 and loses the INSERT race; attempt 2 re-reads the
    # (now advanced) tip seq=5 and succeeds at seq=6.
    tips = iter([(4, "h4"), (5, "h5")])
    inserts: list[dict[str, Any]] = []

    async def execute(q: Any, params: Any = None) -> Any:
        sql = str(q)
        if "SELECT seq, record_hash" in sql:
            return _Result(next(tips))
        if "INSERT INTO audit_chain" in sql:
            inserts.append(params)
            if len(inserts) == 1:
                raise _pk_violation()
        return _Result()

    rec = await PersistentAuditChain(_factory(execute)).append("t1", {"event": "grant_issued"})
    assert rec["seq"] == 6
    assert rec["prev_hash"] == "h5"
    assert [i["seq"] for i in inserts] == [5, 6]


async def test_append_raises_after_bounded_retries() -> None:
    calls = {"insert": 0}

    async def execute(q: Any, params: Any = None) -> Any:
        sql = str(q)
        if "SELECT seq, record_hash" in sql:
            return _Result((1, "h1"))
        if "INSERT INTO audit_chain" in sql:
            calls["insert"] += 1
            raise _pk_violation()
        return _Result()

    with pytest.raises(AuditChainAppendError):
        await PersistentAuditChain(_factory(execute)).append("t1", {"event": "x"})
    assert calls["insert"] == audit_chain_store.APPEND_MAX_ATTEMPTS


async def test_grant_api_reports_unrecorded_audit_instead_of_silently_dropping() -> None:
    from app.api.grants import router
    from app.governance.grants import InMemoryGrantStore
    from tests.governance._router_app import make_app

    class _FailingChain:
        async def append(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise AuditChainAppendError("lost every race")

    app = make_app(router)
    app.state.grant_store = InMemoryGrantStore()
    app.state.audit_chain = _FailingChain()
    grantee = MagicMock()  # the grantee agent must exist in the tenant
    grantee.get_async = AsyncMock(return_value={"agent_id": "a1"})
    app.state.agent_store = grantee
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/grants",
            json={"grantee_agent_id": "a1", "scopes": ["jira.*"], "ttl_seconds": 60},
        )
    assert r.status_code == 201, r.text
    assert r.json()["audit_recorded"] is False
