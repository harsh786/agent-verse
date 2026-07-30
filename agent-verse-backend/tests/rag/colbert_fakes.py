"""Deterministic ColBERT test doubles for artifact-independent unit tests."""

from __future__ import annotations

import re


class DeterministicColBERTReranker:
    async def score(self, query: str, documents: list[str]) -> list[float]:
        return self.score_sync(query, documents)

    def score_sync(self, query: str, documents: list[str]) -> list[float]:
        query_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
        return [
            float(
                len(
                    query_tokens
                    & set(re.findall(r"[a-z0-9]+", document.casefold()))
                )
            )
            for document in documents
        ]
