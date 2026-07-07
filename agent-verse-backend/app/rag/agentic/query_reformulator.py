"""QueryReformulator — generates alternative query phrasings on empty results."""
from __future__ import annotations
import re


class QueryReformulator:
    def __init__(self, max_attempts: int = 2) -> None:
        self._max = max_attempts

    def reformulate(self, query: str) -> list[str]:
        alternatives: list[str] = []
        q = query.strip()
        keywords = re.sub(r"^(what is|how do|can you|please|find|get|list)\s+", "", q, flags=re.I)
        if keywords != q and keywords:
            alternatives.append(keywords)
        expanded = q.replace("kb", "knowledge base").replace("ltm", "long-term memory")
        if expanded != q:
            alternatives.append(expanded)
        else:
            alternatives.append(f"information about {q}")
        return alternatives[: self._max]
