"""KB-23: retrieval never guesses a 1536-dim table for a collection.

When collection metadata could not be read, or ``embedding_dim`` was NULL,
``hybrid_search`` searched ``knowledge_chunks_1536`` — so a collection built
with a 768/1024-dim embedder (common since the embedder resolver) returned
nothing. The width now comes from the query embedding itself (the resolved
embedder's real output), and an unreadable metadata row is a leg failure.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.engine import RetrievalLegExecutionError, hybrid_search


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows

    def mappings(self) -> Any:
        rows = self._rows

        class _M:
            def all(self) -> list[Any]:
                return rows

        return _M()


class _Session:
    def __init__(self, metadata: Any) -> None:
        self._metadata = metadata
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        if sql.startswith("SELECT embedding_dim FROM knowledge_collections"):
            if isinstance(self._metadata, Exception):
                raise self._metadata
            return _Result(self._metadata)
        return _Result([])


def _tables(session: _Session) -> set[str]:
    import re

    return {m for s in session.sql for m in re.findall(r"knowledge_chunks_\d+", s)}


@pytest.mark.parametrize("strict", [True, False])
async def test_null_embedding_dim_uses_the_query_embedding_width(strict: bool) -> None:
    session = _Session(metadata=[(None,)])
    await hybrid_search(
        session,  # type: ignore[arg-type]
        query="retention policy",
        query_embedding=[0.1] * 768,
        collection_id="col-768",
        strict=strict,
    )
    assert _tables(session) == {"knowledge_chunks_768"}


async def test_unreadable_metadata_is_a_leg_failure_in_strict_mode() -> None:
    session = _Session(metadata=ConnectionError("db blip"))
    with pytest.raises(RetrievalLegExecutionError):
        await hybrid_search(
            session,  # type: ignore[arg-type]
            query="q",
            query_embedding=[0.1] * 768,
            collection_id="c",
            strict=True,
        )


async def test_unreadable_metadata_never_searches_a_guessed_table() -> None:
    session = _Session(metadata=ConnectionError("db blip"))
    results = await hybrid_search(
        session,  # type: ignore[arg-type]
        query="q",
        query_embedding=[0.1] * 768,
        collection_id="c",
        strict=False,
    )
    assert results == []
    assert _tables(session) == set()
