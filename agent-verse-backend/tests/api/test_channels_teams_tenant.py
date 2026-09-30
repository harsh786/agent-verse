"""TRG-01: Teams inbound activities resolve the AgentVerse tenant from the
Microsoft 365 tenant (organisation) id carried in the Bot Framework activity —
never from ``serviceUrl``, which is a regional Bot Framework endpoint shared by
every Teams organisation (e.g. https://smba.trafficmanager.net/amer/)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels.ingestion import router

_SHARED_SERVICE_URL = "https://smba.trafficmanager.net/amer/"
_ORG_A = "72f988bf-86f1-41af-91ab-2d7cd011db47"
_ORG_B = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


def _mapping_db(mappings: dict[tuple[str, str], str]) -> Any:
    """A session factory whose lookup honours (channel_type, channel_id)."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    async def execute(query: Any, params: dict[str, Any] | None = None) -> Any:
        result = MagicMock()
        params = params or {}
        tid = mappings.get((params.get("ct", ""), params.get("ci", "")))
        result.fetchone = MagicMock(return_value=(tid,) if tid else None)
        return result

    session.execute = AsyncMock(side_effect=execute)
    return lambda: session


@pytest.fixture
def teams_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, AsyncMock]:
    from app.gateway.channels.teams import MicrosoftTeamsAdapter

    monkeypatch.setattr(MicrosoftTeamsAdapter, "verify_auth", AsyncMock(return_value=True))
    app = FastAPI()
    app.include_router(router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.db = _mapping_db(
        {
            ("teams", _ORG_A): "tenant-a",
            ("teams", _ORG_B): "tenant-b",
            # A legacy serviceUrl mapping must not route anything any more.
            ("teams", _SHARED_SERVICE_URL): "tenant-legacy",
        }
    )
    return app, gateway


def _activity(org_id: str | None, *, where: str = "channelData") -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "message",
        "text": "hello",
        "serviceUrl": _SHARED_SERVICE_URL,
        "from": {"id": "29:user"},
        "conversation": {"id": "19:conv"},
    }
    if org_id is not None:
        if where == "channelData":
            body["channelData"] = {"tenant": {"id": org_id}}
        else:
            body["conversation"]["tenantId"] = org_id
    return body


def test_two_orgs_sharing_a_service_url_each_reach_only_their_own_tenant(teams_app):
    app, gateway = teams_app
    client = TestClient(app)

    assert client.post("/channels/teams/events", json=_activity(_ORG_A)).status_code == 200
    assert client.post("/channels/teams/events", json=_activity(_ORG_B)).status_code == 200

    tenants = [c.kwargs["tenant_id"] for c in gateway.ingest.await_args_list]
    assert tenants == ["tenant-a", "tenant-b"]


def test_org_id_from_conversation_tenant_id_and_case_insensitive(teams_app):
    app, gateway = teams_app
    client = TestClient(app)
    body = _activity(_ORG_B.upper(), where="conversation")
    assert client.post("/channels/teams/events", json=body).status_code == 200
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "tenant-b"


def test_activity_without_org_id_is_not_routed_by_service_url(teams_app):
    app, gateway = teams_app
    client = TestClient(app)
    resp = client.post("/channels/teams/events", json=_activity(None))
    assert resp.status_code == 200
    gateway.ingest.assert_not_called()


def test_unmapped_org_is_not_routed(teams_app):
    app, gateway = teams_app
    client = TestClient(app)
    other = "11111111-2222-3333-4444-555555555555"
    assert client.post("/channels/teams/events", json=_activity(other)).status_code == 200
    gateway.ingest.assert_not_called()


# ── mapping creation ────────────────────────────────────────────────────


def _mapping_client(db: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.channel_gateway = None
    app.state.db = db

    @app.middleware("http")
    async def inject_tenant(req: Any, call_next: Any) -> Any:
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    return TestClient(app)


def _recording_db() -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=session)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)

    async def execute(query: Any, params: dict[str, Any] | None = None) -> Any:
        calls.append((str(query), dict(params or {})))
        result = MagicMock()
        # No verified claim elsewhere / no own row; the INSERT returns its id.
        result.fetchone = MagicMock(
            return_value=("m-new",) if "INSERT INTO" in str(query) else None
        )
        return result

    session.execute = AsyncMock(side_effect=execute)
    return (lambda: session), calls


@pytest.mark.parametrize(
    "channel_id",
    [_SHARED_SERVICE_URL, "https://smba.trafficmanager.net/emea/", "not-a-guid", ""],
)
def test_teams_mapping_requires_m365_tenant_guid(channel_id: str) -> None:
    db, calls = _recording_db()
    resp = _mapping_client(db).post(
        "/channels/mappings", json={"channel_type": "teams", "channel_id": channel_id}
    )
    assert resp.status_code == 422
    assert not any("INSERT" in sql for sql, _ in calls)


def test_teams_mapping_stores_normalised_guid() -> None:
    db, calls = _recording_db()
    resp = _mapping_client(db).post(
        "/channels/mappings",
        json={"channel_type": "teams", "channel_id": f"  {_ORG_A.upper()} "},
    )
    assert resp.status_code == 200
    inserts = [p for sql, p in calls if "INSERT INTO channel_tenant_mappings" in sql]
    assert inserts and inserts[0]["ci"] == _ORG_A


def test_list_flags_legacy_teams_service_url_mappings() -> None:
    rows = []
    for mid, ct, ci in (
        ("m1", "teams", _SHARED_SERVICE_URL),
        ("m2", "teams", _ORG_A),
        ("m3", "slack", "T1"),
    ):
        r = MagicMock()
        r._mapping = {"id": mid, "channel_type": ct, "channel_id": ci, "created_at": None}
        rows.append(r)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=rows)
    resp = _mapping_client(lambda: session).get("/channels/mappings")
    assert resp.status_code == 200
    flags = {m["id"]: m["needs_remapping"] for m in resp.json()}
    assert flags == {"m1": True, "m2": False, "m3": False}
