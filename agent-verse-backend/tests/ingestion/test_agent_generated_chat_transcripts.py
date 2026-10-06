"""CHAT-KB-2: the ``chat_transcript`` kind of agent_generated Sources (unit level).

A consenting person's own chat sessions become one document each
(``agentverse://chat-sessions/<id>``, origin ``{kind, chat_session_id, user_id}``),
with PII and secrets redacted before indexing. Only the owner's own messages (and
the replies to them) are part of it; a message another person wrote into the
session never is. Saving the kind is refused while the tenant switch is off.
The Postgres paths (keyset, consent, tenant switch, RLS, search) are covered by
``tests/ingestion/connectors/test_chat_transcripts_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.ingestion.connectors.agent_generated_connector import (
    KIND_CHAT_TRANSCRIPT,
    SUPPORTED_KINDS,
    AgentGeneratedConnector,
    _render_chat,
    parse_options,
)
from app.ingestion.source_config import SourceConfig, SourceFamily

AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def _config(**cfg: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-1", tenant_id="t1", name="chats", family=SourceFamily.AGENT_GENERATED,
        source_type="agent_generated", collection_id="col-1",
        connection_config={"source_types": [KIND_CHAT_TRANSCRIPT], **cfg},
    )


def _msg(role: str, content: str, author: str | None = None) -> dict[str, Any]:
    meta = {"author_user_id": author} if author else {}
    return {"role": role, "content": content, "metadata": meta, "created_at": AT}


def _row(messages: list[dict[str, Any]], owner: str = "user-a") -> dict[str, Any]:
    return {
        "_ts": AT, "_id": "sess-1", "session_id": "sess-1", "title": "Refund policy",
        "owner_user_id": owner, "agent_id": "", "updated_at": "2026-10-06T09:00:00+00:00",
        "messages": messages,
    }


def test_the_kind_is_supported_but_never_a_default() -> None:
    assert KIND_CHAT_TRANSCRIPT in SUPPORTED_KINDS
    assert KIND_CHAT_TRANSCRIPT not in parse_options({}).kinds
    assert parse_options({"source_types": [KIND_CHAT_TRANSCRIPT]}).kinds == (
        KIND_CHAT_TRANSCRIPT,)


def test_a_chat_stream_is_read_only_when_the_kind_is_listed() -> None:
    connector = AgentGeneratedConnector()
    keys = [s.key for s in connector._streams(parse_options({"source_types": ["goal_output"]}))]
    assert "chat_transcript" not in keys
    keys = [s.key for s in connector._streams(parse_options(
        {"source_types": [KIND_CHAT_TRANSCRIPT]}))]
    assert keys == ["chat_transcript"]


def test_a_transcript_cites_its_session_and_is_indexed_under_its_owner() -> None:
    doc = _render_chat(_config(), _row([
        _msg("user", "What is the refund window for pallets?", "user-a"),
        _msg("assistant", "Pallets can be refunded within 45 days."),
    ]))
    assert doc is not None
    assert doc.source_url == "agentverse://chat-sessions/sess-1"
    assert doc.metadata["origin"] == {
        "kind": KIND_CHAT_TRANSCRIPT, "chat_session_id": "sess-1", "user_id": "user-a"}
    assert doc.acl == ["user:user-a"]
    assert doc.author == "user-a"
    body = doc.content.decode()
    assert "refund window for pallets" in body and "within 45 days" in body


def test_pii_and_secrets_are_redacted_before_indexing() -> None:
    doc = _render_chat(_config(), _row([
        _msg("user", "Mail jane.doe@example.com the key AKIAIOSFODNN7EXAMPLE now", "user-a"),
        _msg("assistant", "Sent to jane.doe@example.com."),
    ]))
    assert doc is not None
    body = doc.content.decode()
    assert "jane.doe@example.com" not in body
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert "[REDACTED]" in body


def test_another_persons_message_and_its_reply_are_never_part_of_it() -> None:
    doc = _render_chat(_config(), _row([
        _msg("user", "owner question about dock doors", "user-a"),
        _msg("assistant", "answer to the owner"),
        _msg("user", "intruder private question", "user-b"),
        _msg("assistant", "answer to the intruder"),
        _msg("user", "unattributed api key message"),
        _msg("assistant", "answer to the api key"),
    ]))
    assert doc is not None
    body = doc.content.decode()
    assert "owner question" in body and "answer to the owner" in body
    for leaked in ("intruder", "api key"):
        assert leaked not in body


def test_nothing_of_the_owner_means_no_document() -> None:
    assert _render_chat(_config(), _row([_msg("user", "not mine", "user-b")])) is None
    assert _render_chat(_config(), _row([])) is None


def test_a_long_session_keeps_its_latest_messages() -> None:
    messages = []
    for i in range(400):
        messages.append(_msg("user", f"question {i} " + "x" * 1000, "user-a"))
    doc = _render_chat(_config(), _row(messages))
    assert doc is not None
    body = doc.content.decode()
    assert "question 399" in body and "question 0 " not in body
    assert "earlier messages omitted" in body
    assert len(body) <= 210_000


# ── save gate ────────────────────────────────────────────────────────────────


async def _create(app: Any, tenant: Any, kinds: list[str]) -> Any:
    from fastapi.testclient import TestClient

    from app.api.ingestion import router as ingestion_router

    app.include_router(ingestion_router)

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = tenant
        return await call_next(request)

    client = TestClient(app)
    return client, client.post("/sources", json={
        "name": "chats", "family": "agent_generated", "source_type": "agent_generated",
        "collection_id": "col-1", "connection_config": {"source_types": kinds}})


@pytest.mark.parametrize("enabled", [False, True])
async def test_saving_the_kind_needs_the_tenant_switch(enabled: bool) -> None:
    from fastapi import FastAPI

    from app.services.chat_knowledge import chat_knowledge_for
    from app.tenancy.context import PlanTier, TenantContext

    app = FastAPI()
    app.state.db_session_factory = None
    tenant = TenantContext("tenant-gate", PlanTier.ENTERPRISE, "k", roles=("admin",))
    await chat_knowledge_for(app.state).set_tenant_enabled("tenant-gate", enabled)
    client, resp = await _create(app, tenant, ["goal_output", KIND_CHAT_TRANSCRIPT])
    if enabled:
        assert resp.status_code == 201, resp.text
        sid = resp.json()["source_id"]
        # Switched off later: an update that keeps the kind is refused too.
        await chat_knowledge_for(app.state).set_tenant_enabled("tenant-gate", False)
        patched = client.patch(f"/sources/{sid}", json={
            "connection_config": {"source_types": [KIND_CHAT_TRANSCRIPT]}})
        assert patched.status_code == 422
    else:
        assert resp.status_code == 422
        assert "chat transcripts are not enabled" in resp.json()["detail"].lower()
    # Other kinds never need it.
    _, other = await _create(FastAPI(), tenant, ["goal_output"])
    assert other.status_code == 201, other.text


# ── events: a saved message triggers a sync only for a consented session ────


@pytest.mark.parametrize(
    "listening, eligible, queued",
    [(True, True, True), (True, False, False), (False, True, False)],
)
async def test_a_chat_message_notifies_only_for_a_consented_session(
    monkeypatch: pytest.MonkeyPatch, listening: bool, eligible: bool, queued: bool
) -> None:
    from app.ingestion import agent_generated_events as events

    probes: list[str] = []
    sent: list[tuple[Any, ...]] = []

    async def _kinds(tenant_id: str, **_: Any) -> frozenset[str]:
        return frozenset({KIND_CHAT_TRANSCRIPT} if listening else {"goal_output"})

    async def _rows(db: Any, tenant_id: str, sql: str, params: dict[str, Any]) -> list[Any]:
        probes.append(sql)
        assert "chat_kb_consents" in sql and "chat_transcripts_kb_enabled" in sql
        assert params == {"tid": "t1", "sid": "sess-1"}
        return [(1,)] if eligible else []

    monkeypatch.setattr(events, "listening_kinds", _kinds)
    monkeypatch.setattr(events, "_rows", _rows)
    monkeypatch.setattr(events, "enqueue_notify", lambda *a, **k: sent.append(a))
    result = await events.notify_chat_transcript("t1", "sess-1", db_factory=object())
    assert result is queued
    assert sent == ([("t1", KIND_CHAT_TRANSCRIPT, "sess-1")] if queued else [])
    assert len(probes) == (1 if listening else 0)


async def test_a_failing_notify_never_reaches_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingestion import agent_generated_events as events

    async def _boom(*_: Any, **__: Any) -> frozenset[str]:
        raise OSError("db down")

    monkeypatch.setattr(events, "listening_kinds", _boom)
    assert await events.notify_chat_transcript("t1", "sess-1", db_factory=object()) is False
