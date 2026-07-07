from __future__ import annotations
import re
from app.ingestion.chunkers.base import Chunk, ChunkerBase

_TS_PATTERN = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]")


def _to_seconds(ts: str) -> int:
    h, m, s = ts.split(":")
    return int(h)*3600 + int(m)*60 + int(s)


class TimestampChunker(ChunkerBase):
    def __init__(self, chunk_duration_seconds: int = 60) -> None:
        self._duration = chunk_duration_seconds

    def chunk(self, content: str) -> list[Chunk]:
        matches = list(_TS_PATTERN.finditer(content))
        if not matches:
            paras = [p.strip() for p in content.split("\n") if p.strip()]
            return [Chunk(content=p, chunk_index=i, metadata={"start_time": "00:00:00"})
                    for i, p in enumerate(paras)]
        chunks: list[Chunk] = []
        chunk_start_ts = matches[0].group(1)
        chunk_start_sec = _to_seconds(chunk_start_ts)
        chunk_lines: list[str] = []
        for i, m in enumerate(matches):
            ts = m.group(1)
            sec = _to_seconds(ts)
            end = matches[i+1].start() if i+1 < len(matches) else len(content)
            line = content[m.start():end].strip()
            if sec - chunk_start_sec >= self._duration and chunk_lines:
                chunks.append(Chunk(content="\n".join(chunk_lines), chunk_index=len(chunks),
                                    metadata={"start_time": chunk_start_ts}))
                chunk_lines = [line]; chunk_start_ts = ts; chunk_start_sec = sec
            else:
                chunk_lines.append(line)
        if chunk_lines:
            chunks.append(Chunk(content="\n".join(chunk_lines), chunk_index=len(chunks),
                                metadata={"start_time": chunk_start_ts}))
        return chunks
