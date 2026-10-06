"""The typed webhook fires only the token owner's trigger, signed when required.

Regression 1: it took the tenant from an ``X-Tenant-ID`` header and ignored the
path token ("for now just broadcast"), so any API-key holder could fire every
webhook trigger of any other tenant. A missing signature header also skipped
verification even when the trigger had a signing secret.

Regression 2 (see test_typed_webhook_token_lookup.py): the tenant then came from
the caller's API key, which a third-party sender never has. The tenant is now
resolved from the path token alone; header and API-key tenant are ignored.
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

VICTIM_TOKEN = "victim-" + "v" * 40
TOK_A = "tok-a-" + "a" * 40
TOK_B = "tok-b-" + "b" * 40
TOK = "tok-" + "t" * 40


class _Store:
    def __init__(self, triggers: dict[str, list[Any]]) -> None:
        self._triggers = triggers
        self.queried: list[str] = []

    async def find_tenant_by_webhook_token(self, token: str, **_k: Any) -> Any:
        owners = {
            tid for tid, specs in self._triggers.items() for s in specs if s.webhook_token == token
        }
        return owners.pop() if len(owners) == 1 else None

    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str, **_k: Any) -> list[Any]:
        self.queried.append(tenant_id)
        return self._triggers.get(tenant_id, [])


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[tuple[str, str]] = []

    async def dispatch(
        self, spec: Any, payload: dict[str, Any], tenant_ctx: Any, **kw: Any
    ) -> None:
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
    store = _Store({"victim": [_spec(VICTIM_TOKEN)], "attacker": [_spec(TOK)]})
    disp = _Dispatcher()
    client = _app(store, disp, caller="attacker")
    r = client.post(
        f"/triggers/webhooks/custom/{TOK}", json={"x": 1}, headers={"X-Tenant-ID": "victim"}
    )
    assert r.status_code == 200
    # The header named the victim, but only the token's owner was queried/fired.
    assert disp.fired == [("attacker", TOK)] and store.queried == ["attacker"]


def test_only_the_matching_token_fires() -> None:
    store = _Store({"t1": [_spec(TOK_A), _spec(TOK_B)]})
    disp = _Dispatcher()
    client = _app(store, disp, caller=None)
    assert client.post(f"/triggers/webhooks/custom/{TOK_B}", json={}).status_code == 200
    assert disp.fired == [("t1", TOK_B)]
    assert client.post("/triggers/webhooks/custom/" + "n" * 40, json={}).status_code == 404


def test_signature_required_when_secret_is_set() -> None:
    secret = "s3cret"
    store = _Store({"t1": [_spec(TOK, secret)]})
    disp = _Dispatcher()
    client = _app(store, disp, caller=None)
    body = json.dumps({"ref": "main"}).encode()
    url = f"/triggers/webhooks/github/{TOK}"
    # No signature header at all: rejected (used to skip verification).
    assert client.post(url, content=body).status_code == 401
    bad = {"x-hub-signature-256": "sha256=" + "0" * 64}
    assert client.post(url, content=body, headers=bad).status_code == 401
    good_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post(url, content=body, headers={"x-hub-signature-256": good_sig})
    assert ok.status_code == 200, ok.text
    assert disp.fired == [("t1", TOK)]


def test_unknown_token_without_api_key_is_404() -> None:
    client = _app(_Store({}), _Dispatcher(), caller=None)
    assert client.post("/triggers/webhooks/custom/" + "x" * 40, json={}).status_code == 404
