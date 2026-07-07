from __future__ import annotations
import re
from app.ingestion.chunkers.base import Chunk, ChunkerBase

_SCENE_PATTERN = re.compile(r"\[SCENE\s+(\d+)(?::\s*([^\]]+))?\]", re.IGNORECASE)


class SceneChunker(ChunkerBase):
    def chunk(self, content: str) -> list[Chunk]:
        matches = list(_SCENE_PATTERN.finditer(content))
        if not matches:
            paras = [p.strip() for p in content.split("\n") if p.strip()]
            return [Chunk(content=p, chunk_index=i, metadata={"scene_number": i+1})
                    for i, p in enumerate(paras)]
        chunks = []
        for i, m in enumerate(matches):
            scene_num = int(m.group(1)); scene_info = m.group(2) or ""
            end = matches[i+1].start() if i+1 < len(matches) else len(content)
            scene_content = content[m.start():end].strip()
            if scene_content:
                chunks.append(Chunk(content=scene_content, chunk_index=i,
                                    metadata={"scene_number": scene_num, "timestamp": scene_info}))
        return chunks
