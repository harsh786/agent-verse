"""The typed webhook fires only the caller's trigger, matched by token, signed when required.

Regression: it took the tenant from an ``X-Tenant-ID`` header and ignored the
path token ("for now just broadcast"), so any API-key holder could fire every
webhook trigger of any other tenant. A missing signature header also skipped
verification even when the trigger had a signing secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router


class _Store:
    def __init__(self, triggers: dict[str, list[Any]]) -> None:
        self._triggers = triggers
        self.queried: list[str] = []

    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str) -> list[Any]:
        self.queried.append(tenant_id)
        return self._triggers.get(tenant_id, [])


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[tuple[str, str]] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any) -> None:
        self.fired.append((tenant_ctx.tenant_id, spec.webhook_token))


def _app(store: _Store, dispatcher: _Dispatcher, caller: str | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        if caller:
            request.state.tenant = SimpleNamespace(tenant_id=caller, plan="free")
        return await call_next(request)

    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    return TestClient(app, raise_server_exceptions=False)


def _spec(token: str, secret: str = "") -> Any:
    return SimpleNamespace(webhook_token=token, webhook_signature_secret=secret)


def test_header_cannot_redirect_to_another_tenant() -> None:
    store = _Store({"victim": [_spec("victim-token")], "attacker": []})
    disp = _Dispatcher()
    client = _app(store, disp, caller="attacker")
    r = client.post("/triggers/webhooks/custom/victim-token", json={"x": 1},
                    headers={"X-Tenant-ID": "victim"})
    assert r.status_code == 404
    assert disp.fired == [] and store.queried == ["attacker"]


def test_only_the_matching_token_fires() -> None:
    store = _Store({"t1": [_spec("tok-a"), _spec("tok-b")]})
    disp = _Dispatcher()
    client = _app(store, disp, caller="t1")
    assert client.post("/triggers/webhooks/custom/tok-b", json={}).status_code == 200
    assert disp.fired == [("t1", "tok-b")]
    assert client.post("/triggers/webhooks/custom/nope", json={}).status_code == 404


def test_signature_required_when_secret_is_set() -> None:
    secret = "s3cret"
    store = _Store({"t1": [_spec("tok", secret)]})
    disp = _Dispatcher()
    client = _app(store, disp, caller="t1")
    body = json.dumps({"ref": "main"}).encode()
    # No signature header at all: rejected (used to skip verification).
    assert client.post("/triggers/webhooks/github/tok", content=body).status_code == 401
    bad = {"x-hub-signature-256": "sha256=" + "0" * 64}
    assert client.post("/triggers/webhooks/github/tok", content=body, headers=bad).status_code == 401
    good_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post("/triggers/webhooks/github/tok", content=body,
                     headers={"x-hub-signature-256": good_sig})
    assert ok.status_code == 200, ok.text
    assert disp.fired == [("t1", "tok")]


def test_unauthenticated_is_401() -> None:
    client = _app(_Store({}), _Dispatcher(), caller=None)
    assert client.post("/triggers/webhooks/custom/x", json={}).status_code == 401
