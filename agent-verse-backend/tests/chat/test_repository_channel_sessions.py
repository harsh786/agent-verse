"""PostgresChatRepository channel/principal mapping SQL shape (no database).

A scripted AsyncSession answers each statement, so we can pin: the tenant GUC is
set before any data statement, the found-path never inserts, the first-contact
path inserts session + ``ON CONFLICT DO NOTHING`` mapping in ONE transaction, and
a lost race deletes its own session and adopts the winner's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.chat.repository import PostgresChatRepository
from tests.db._recording_session import RecordingSession, _Result, factory_for

TID = "tenant-a"


class _ScriptedSession(RecordingSession):
    def __init__(self, answer: Callable[[str, int], list[tuple[Any, ...]]]) -> None:
        super().__init__()
        self._answer = answer
        self._selects = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        base = await super().execute(stmt, params)
        sql = str(stmt)
        if "set_config('app.tenant_id'" in sql:
            return base
        if sql.startswith("SELECT"):
            self._selects += 1
        return _Result(self._answer(sql, self._selects))


def _resolve(repo: PostgresChatRepository) -> Any:
    return repo.resolve_channel_session(
        tenant_id=TID, channel="telegram", channel_user_id="chat-1",
        new_session_id="new-sid", title="telegram:chat-1",
    )


def _sqls(session: RecordingSession) -> list[str]:
    return [" ".join(sql.split()) for sql, _ in session.statements]


async def test_existing_mapping_is_returned_without_insert() -> None:
    session = _ScriptedSession(lambda sql, _n: [("old-sid",)] if sql.startswith("SELECT") else [])
    assert await _resolve(PostgresChatRepository(factory_for(session))) == "old-sid"  # type: ignore[arg-type]
    assert session.began == 1 and session.log[0] == f"guc:{TID}"
    assert not any(s.startswith(("INSERT", "DELETE")) for s in _sqls(session))
    _, params = session.statements[0]
    assert params == {"t": TID, "c": "telegram", "u": "chat-1"}


async def test_first_contact_inserts_session_and_claims_mapping_in_one_tx() -> None:
    def answer(sql: str, _n: int) -> list[tuple[Any, ...]]:
        if "RETURNING chat_session_id" in sql:
            return [("new-sid",)]
        return []

    session = _ScriptedSession(answer)
    assert await _resolve(PostgresChatRepository(factory_for(session))) == "new-sid"  # type: ignore[arg-type]
    sqls = _sqls(session)
    assert session.began == 1 and session.log[0] == f"guc:{TID}"
    assert sqls[1].startswith("INSERT INTO chat_sessions")
    assert sqls[2].startswith("INSERT INTO chat_channel_sessions")
    assert "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING" in sqls[2]
    assert session.statements[2][1]["sid"] == "new-sid"
    assert not any(s.startswith("DELETE") for s in sqls)


async def test_lost_race_drops_own_session_and_adopts_winner() -> None:
    def answer(sql: str, n_selects: int) -> list[tuple[Any, ...]]:
        if sql.startswith("SELECT"):
            return [] if n_selects == 1 else [("winner-sid",)]
        return []  # ON CONFLICT DO NOTHING -> RETURNING yields no row

    session = _ScriptedSession(answer)
    assert await _resolve(PostgresChatRepository(factory_for(session))) == "winner-sid"  # type: ignore[arg-type]
    sqls = _sqls(session)
    delete = [s for s in sqls if s.startswith("DELETE FROM chat_sessions")]
    assert len(delete) == 1
    assert session.statements[sqls.index(delete[0])][1] == {"id": "new-sid", "t": TID}


async def test_conflict_without_visible_winner_fails_closed() -> None:
    session = _ScriptedSession(lambda _sql, _n: [])
    with pytest.raises(RuntimeError):
        await _resolve(PostgresChatRepository(factory_for(session)))  # type: ignore[arg-type]


async def test_principal_session_get_and_claim_are_tenant_scoped() -> None:
    session = _ScriptedSession(lambda sql, _n: [("s1",)] if sql.startswith("SELECT") else [])
    repo = PostgresChatRepository(factory_for(session))  # type: ignore[arg-type]
    assert await repo.get_principal_session(TID, "p1") == "s1"
    assert await repo.claim_principal_session(
        tenant_id=TID, principal_id="p1", session_id="s2"
    ) == "s1"  # an existing binding wins
    assert session.guc_calls == [TID, "", TID, ""]
    sqls = _sqls(session)
    assert any("ON CONFLICT (tenant_id, principal_id) DO NOTHING" in s for s in sqls)
    assert all(p.get("t") == TID for _, p in session.statements)


async def test_db_error_propagates() -> None:
    class _Boom(RecordingSession):
        async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
            if "set_config" in str(stmt):
                return _Result([])
            raise ConnectionError("db down")

    with pytest.raises(ConnectionError):
        await _resolve(PostgresChatRepository(factory_for(_Boom())))  # type: ignore[arg-type]
