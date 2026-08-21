from __future__ import annotations

from app.ingestion.chunkers.base import Chunk, ChunkerBase


class TableChunker(ChunkerBase):
    def __init__(self, rows_per_chunk: int = 50) -> None:
        self._rows_per_chunk = rows_per_chunk

    def chunk(self, content: str) -> list[Chunk]:
        lines = [l for l in content.splitlines() if l.strip()]
        if not lines:
            return [Chunk(content=content.strip(), chunk_index=0)]
        header = lines[0]
        data_rows = lines[1:]
        if not data_rows:
            return [Chunk(content=content.strip(), chunk_index=0, metadata={"row_start": 0})]
        chunks = []
        for i in range(0, len(data_rows), self._rows_per_chunk):
            batch = data_rows[i : i + self._rows_per_chunk]
            chunk_content = "\n".join([header] + batch)
            chunks.append(
                Chunk(
                    content=chunk_content,
                    chunk_index=len(chunks),
                    metadata={"row_start": i + 1, "row_end": i + len(batch)},
                )
            )
        return chunks
