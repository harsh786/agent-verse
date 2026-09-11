"""Gateway command routing (item 7): an inbound chat command must become real
platform work — a submitted goal for text, and knowledge ingestion for files.

These cover the helper wiring with fakes (no DB/LLM), guarding against the
regression where _process_command was a stub that only echoed the text.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gateway import router as gw
from app.gateway.command import CommandFile, OrgCommand

pytestmark = pytest.mark.asyncio

TID = "tenant-xyz"


def _cmd(**kw):
    base = dict(
        command_id="c1",
        tenant_id=TID,
        org_id="org1",
        text="do the thing",
        actor_channel="telegram",
        conversation_id="chat1",
    )
    base.update(kw)
    return OrgCommand(**base)


async def test_tenant_ctx_uses_only_the_trusted_tenant_id():
    # _tenant_ctx_for trusts ONLY command.tenant_id (already validated by the
    # webhook handler); it must NOT fall back to the spoofable org_id.
    _, tid = gw._tenant_ctx_for(_cmd())
    assert tid == TID
    _, tid2 = gw._tenant_ctx_for(_cmd(tenant_id="", org_id="orgAsTenant"))
    assert tid2 == ""


async def test_trusted_gateway_tenant_requires_ingress_secret(monkeypatch):
    # No secret configured → never trust x-tenant-id (anonymous cross-tenant hole).
    monkeypatch.delenv("GATEWAY_INGRESS_SECRET", raising=False)
    assert gw.trusted_gateway_tenant({"x-tenant-id": "victim"}) == ""
    # Secret configured but not presented / wrong → still untrusted.
    monkeypatch.setenv("GATEWAY_INGRESS_SECRET", "s3cret")
    assert gw.trusted_gateway_tenant({"x-tenant-id": "victim"}) == ""
    assert gw.trusted_gateway_tenant({"x-tenant-id": "victim", "x-gateway-secret": "wrong"}) == ""
    # Correct secret → the header tenant is trusted.
    assert (
        gw.trusted_gateway_tenant({"x-tenant-id": "acme", "x-gateway-secret": "s3cret"}) == "acme"
    )


async def test_submit_goal_routes_text_to_goal_service():
    goal_service = SimpleNamespace(
        submit_goal=AsyncMock(return_value={"goal_id": "g-123"})
    )
    state = SimpleNamespace(goal_service=goal_service)
    ctx, _ = gw._tenant_ctx_for(_cmd())
    gid = await gw._submit_goal_from_command(_cmd(), state, ctx, "summarize sales")
    assert gid == "g-123"
    assert goal_service.submit_goal.await_args.kwargs["goal"] == "summarize sales"


async def test_ingest_command_files_ingests_each_document():
    # Fake pipeline that reports each doc indexed; fake KS that resolves a collection.
    pipeline = SimpleNamespace(ingest=AsyncMock(return_value=SimpleNamespace(status="indexed")))
    ks = SimpleNamespace(
        list_collections_async=AsyncMock(return_value=[]),
        create_collection_async=AsyncMock(return_value="coll-1"),
    )
    state = SimpleNamespace(ingestion_pipeline=pipeline, knowledge_store=ks)
    cmd = _cmd(
        text="(attachment)",
        files=[
            CommandFile(filename="a.txt", content_type="text/plain", data=b"hello"),
            CommandFile(filename="b.txt", content_type="text/plain", data=b"world"),
        ],
    )
    ctx, tid = gw._tenant_ctx_for(cmd)
    n = await gw._ingest_command_files(cmd, state, ctx, tid)
    assert n == 2
    assert pipeline.ingest.await_count == 2
    # collection created once and named per channel
    assert ks.create_collection_async.await_count == 1


async def test_ingest_skips_when_no_pipeline():
    state = SimpleNamespace(ingestion_pipeline=None, knowledge_store=None)
    ctx, tid = gw._tenant_ctx_for(_cmd())
    n = await gw._ingest_command_files(
        _cmd(files=[CommandFile(filename="a", content_type="text/plain", data=b"x")]),
        state, ctx, tid,
    )
    assert n == 0
