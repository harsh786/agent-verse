"""B2-9: a workflow webhook declaring ``auth: hmac`` verifies the signature.

Live (TRIGGER-CHAIN, 2026-10-06): the workflow DSL accepted
``trigger.webhook.auth: hmac`` + ``hmac_secret`` but nothing ever read them, so
an unsigned or badly signed delivery to /wf-hooks/{token} started a run with the
forged payload (200). Now the body must carry an HMAC-SHA256 signature
(``X-AgentVerse-Signature`` or ``X-Signature``; with ``X-Webhook-Timestamp`` it
covers ``"{ts}.{body}"`` and is replay-windowed). A declared hmac auth with no
secret fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import pytest

from tests.workflow.test_webhook_hardening import _T, env  # noqa: F401  (fixture)

SECRET = "wf-hmac-" + "k" * 24


def _sig(secret: str, data: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


async def _published(svc: Any, webhook: dict[str, Any]) -> str:
    wf = await svc.create(
        tenant_id=_T,
        name="signed",
        definition={"name": "signed", "steps": [],
                    "trigger": {"type": "webhook", "webhook": webhook}},
    )
    wid = str(wf["id"])
    await svc.publish(tenant_id=_T, workflow_id=wid)
    return wid


@pytest.mark.asyncio
async def test_hmac_workflow_webhook_requires_a_valid_signature(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc, {"auth": "hmac", "hmac_secret": SECRET})
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    body = b'{"batch": "B-1"}'
    ct = {"Content-Type": "application/json"}

    assert client.post(path, content=body, headers=ct).status_code == 401
    bad = {**ct, "X-AgentVerse-Signature": _sig("nope", body)}
    assert client.post(path, content=body, headers=bad).status_code == 401
    ts = int(time.time()) - 3600
    stale = {**ct, "X-Webhook-Timestamp": str(ts),
             "X-AgentVerse-Signature": _sig(SECRET, f"{ts}.".encode() + body)}
    assert client.post(path, content=body, headers=stale).status_code == 401
    assert runner.runs == []

    good = {**ct, "X-AgentVerse-Signature": _sig(SECRET, body)}
    assert client.post(path, content=body, headers=good).status_code == 200
    assert len(runner.runs) == 1 and runner.runs[0]["inputs"] == {"batch": "B-1"}


@pytest.mark.asyncio
async def test_declared_hmac_without_a_secret_fails_closed(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc, {"auth": "hmac"})
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    r = client.post(path, json={}, headers={"X-Signature": _sig("", b"{}")})
    assert r.status_code == 401
    assert runner.runs == []


@pytest.mark.asyncio
async def test_token_only_webhook_still_fires(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc, runner = env["client"], env["svc"], env["runner"]
    wid = await _published(svc, {"auth": "none"})
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    assert client.post(path, json={"a": 1}).status_code == 200
    assert len(runner.runs) == 1
