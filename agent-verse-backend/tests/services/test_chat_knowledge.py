"""CHAT-KB: chat transcripts as knowledge — the double consent and the purge.

Owner decision 7 (2026-10-06): off by default; a tenant admin's switch enables
the ``chat_transcript`` kind, and each person opts in for their own chats
(revocable; revoking removes their indexed transcripts, except under legal hold).
These are the in-memory paths; the Postgres paths are covered by
``tests/ingestion/connectors/test_chat_transcripts_integration.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.services.chat_knowledge import (
    KIND_CHAT_TRANSCRIPT,
    ChatKnowledgeSettings,
    ChatKnowledgeUnavailableError,
    purge_transcripts,
)
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-chatkb"
CTX = TenantContext(T, PlanTier.FREE, "k")


# ── switches ─────────────────────────────────────────────────────────────────


async def test_tenant_switch_is_off_by_default_and_per_tenant() -> None:
    settings = ChatKnowledgeSettings()
    assert await settings.tenant_enabled(T) is False
    await settings.set_tenant_enabled(T, True)
    assert await settings.tenant_enabled(T) is True
    assert await settings.tenant_enabled("other-tenant") is False
    await settings.set_tenant_enabled(T, False)
    assert await settings.tenant_enabled(T) is False


async def test_user_opt_in_is_off_by_default_per_user_and_revocable() -> None:
    settings = ChatKnowledgeSettings()
    assert (await settings.consent(T, "user-a")).opted_in is False
    state = await settings.set_consent(T, "user-a", True)
    assert state.opted_in is True and state.opted_in_at is not None
    assert (await settings.consent(T, "user-b")).opted_in is False
    assert (await settings.consent("other-tenant", "user-a")).opted_in is False
    revoked = await settings.set_consent(T, "user-a", False)
    assert revoked.opted_in is False and revoked.revoked_at is not None
    assert (await settings.consent(T, "user-a")).opted_in is False


async def test_a_consent_needs_a_person() -> None:
    settings = ChatKnowledgeSettings()
    with pytest.raises(ValueError):
        await settings.set_consent(T, "", True)


async def test_an_unreadable_switch_fails_closed() -> None:
    class _Broken:
        def __call__(self) -> Any:
            raise OSError("db down")

    settings = ChatKnowledgeSettings(db=_Broken())
    with pytest.raises(ChatKnowledgeUnavailableError):
        await settings.tenant_enabled(T)
    with pytest.raises(ChatKnowledgeUnavailableError):
        await settings.consent(T, "user-a")


# ── purge ────────────────────────────────────────────────────────────────────


async def _store_with_transcripts() -> tuple[KnowledgeStore, str]:
    store = KnowledgeStore()
    cid = await store.create_collection_async(KnowledgeCollection(name="kb"), tenant_ctx=CTX)

    async def _doc(doc_id: str, origin: dict[str, str]) -> None:
        await store.ingest_chunks_async(
            [Chunk(document_id=doc_id, content=f"text of {doc_id}", embedding=[0.1] * 8,
                   chunk_index=0, metadata={"origin": origin})],  # type: ignore[dict-item]
            collection_id=cid, tenant_ctx=CTX,
        )

    for i in range(3):
        await _doc(f"a-{i}", {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-a",
                        "chat_session_id": f"sa-{i}"})
    await _doc("b-0", {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-b", "chat_session_id": "sb-0"})
    await _doc("goal-0", {"kind": "goal_output", "goal_id": "g1", "user_id": "user-a"})
    return store, cid


def _doc_ids(store: KnowledgeStore, cid: str) -> set[str]:
    return {c.document_id for c in store._data[(T, cid)].chunks}


async def test_purge_removes_only_that_users_transcripts() -> None:
    store, cid = await _store_with_transcripts()
    report = await purge_transcripts(store, T, user_id="user-a")
    assert report.removed == 3 and report.held == 0 and report.truncated is False
    assert _doc_ids(store, cid) == {"b-0", "goal-0"}


async def test_purge_by_session_and_whole_tenant() -> None:
    store, cid = await _store_with_transcripts()
    assert (await purge_transcripts(store, T, session_ids=["sa-1"])).removed == 1
    assert "a-1" not in _doc_ids(store, cid)
    assert (await purge_transcripts(store, T)).removed == 3  # every transcript, no goals
    assert _doc_ids(store, cid) == {"goal-0"}


async def test_purge_keeps_and_reports_held_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    store, cid = await _store_with_transcripts()

    async def _held(collection_id: str, ids: list[str], **_: Any) -> set[str]:
        return {i for i in ids if i == "a-2"}

    monkeypatch.setattr(store, "held_document_ids_async", _held)
    report = await purge_transcripts(store, T, user_id="user-a")
    assert (report.removed, report.held) == (2, 1)
    assert report.held_document_ids == ["a-2"]
    assert "a-2" in _doc_ids(store, cid)


async def test_purge_is_bounded_and_says_so() -> None:
    store, cid = await _store_with_transcripts()
    report = await purge_transcripts(store, T, user_id="user-a", batch=1, max_batches=2)
    assert report.removed == 2 and report.truncated is True
    again = await purge_transcripts(store, T, user_id="user-a", batch=1, max_batches=2)
    assert again.removed == 1 and again.truncated is False


async def test_a_failed_hold_check_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    store, cid = await _store_with_transcripts()

    async def _broken(*_: Any, **__: Any) -> set[str]:
        raise OSError("hold table unavailable")

    monkeypatch.setattr(store, "held_document_ids_async", _broken)
    with pytest.raises(ChatKnowledgeUnavailableError):
        await purge_transcripts(store, T, user_id="user-a")
    assert len(_doc_ids(store, cid)) == 5


async def test_purge_never_touches_another_tenant() -> None:
    store, cid = await _store_with_transcripts()
    assert (await purge_transcripts(store, "other-tenant", user_id="user-a")).removed == 0
    assert len(_doc_ids(store, cid)) == 5


async def test_a_truncated_removal_queues_its_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import app.services.chat_knowledge as mod

    store, cid = await _store_with_transcripts()
    real = mod.purge_transcripts
    queued: list[dict[str, Any]] = []

    async def _small(*args: Any, **kwargs: Any) -> Any:
        return await real(*args, **{**kwargs, "batch": 1, "max_batches": 1})

    monkeypatch.setattr(mod, "purge_transcripts", _small)
    monkeypatch.setattr(
        mod, "enqueue_purge_continuation",
        lambda tenant_id, **kw: queued.append({"tenant_id": tenant_id, **kw}),
    )
    state = SimpleNamespace(knowledge_store=store, db_session_factory=None)
    out = await mod.remove_transcripts(state, T, user_id="user-a")
    assert out["removed_documents"] == 1 and out["pending"] is True
    assert out["continuation"] == "queued"
    assert queued == [{"tenant_id": T, "user_id": "user-a", "session_ids": None,
                       "unconsented_only": True}]


async def test_a_person_who_opts_in_again_is_not_purged_by_a_late_continuation() -> None:
    store, cid = await _store_with_transcripts()
    settings = ChatKnowledgeSettings()
    await settings.set_tenant_enabled(T, True)
    await settings.set_consent(T, "user-a", True)
    report = await purge_transcripts(
        store, T, user_id="user-a", unconsented_only=True, settings=settings
    )
    assert report.removed == 0
    assert len(_doc_ids(store, cid)) == 5
