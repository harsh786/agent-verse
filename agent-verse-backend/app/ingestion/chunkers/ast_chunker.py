from __future__ import annotations

import re

from app.ingestion.chunkers.base import Chunk, ChunkerBase

_SYMBOL_PATTERN = re.compile(r"(?m)^(?=def |class |function |const |let |var |public class )")


class ASTChunker(ChunkerBase):
    def chunk(self, content: str) -> list[Chunk]:
        try:
            return self._python_chunk(content)
        except Exception:
            return self._regex_chunk(content)

    def _python_chunk(self, content: str) -> list[Chunk]:
        import ast
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._regex_chunk(content)
        lines = content.splitlines(keepends=True)
        chunks: list[Chunk] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno - 1
                end = getattr(node, "end_lineno", start + 10)
                symbol_content = "".join(lines[start:end]).strip()
                if symbol_content:
                    symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
                    chunks.append(Chunk(content=symbol_content, chunk_index=len(chunks),
                                        metadata={"symbol_type": symbol_type, "name": node.name}))
        return chunks or [Chunk(content=content.strip(), chunk_index=0, metadata={"symbol_type": "module"})]

    def _regex_chunk(self, content: str) -> list[Chunk]:
        blocks = _SYMBOL_PATTERN.split(content)
        chunks = []
        for i in range(1, len(blocks)):
            block = blocks[i].strip()
            if block:
                chunks.append(Chunk(content=block, chunk_index=len(chunks),
                                    metadata={"symbol_type": "function"}))
        return chunks or [Chunk(content=content.strip(), chunk_index=0, metadata={"symbol_type": "module"})]
