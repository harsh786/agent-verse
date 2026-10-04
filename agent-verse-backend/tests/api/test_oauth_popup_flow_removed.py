"""OAUTH-06: the connector_name popup flow is gone; PKCE is the supported path.

POST /connectors/oauth/start kept its CSRF state in a process-local dict (lost
on another replica) and opened the provider page for a flow whose completion,
POST /connectors/oauth/callback, could only ever answer 501. The registered-
connector PKCE flow (GET /connectors/oauth/start?server_id, GET .../callback,
Redis-shared state) is the one way to connect OAuth connectors.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.connectors as connectors_api
from tests.api.test_connectors_comprehensive2 import _VALID_KEY, _make_app

_H = {"X-API-Key": _VALID_KEY}


def test_popup_endpoints_no_longer_exist() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    start = client.post("/connectors/oauth/start", json={"connector_name": "github"}, headers=_H)
    done = client.post(
        "/connectors/oauth/callback",
        json={"code": "c", "state": "s", "connector_name": "github"},
        headers=_H,
    )
    assert start.status_code == 405
    assert done.status_code == 405


def test_no_process_local_oauth_state_store() -> None:
    assert not hasattr(connectors_api, "_oauth_states")


def test_pkce_start_still_served() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors/oauth/start", params={"server_id": "missing"}, headers=_H)
    assert resp.status_code != 405
