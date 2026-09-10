"""WS-13: RPA scrape → knowledge-base chunks (the ONE reachable RPA→KB bridge).

The RPA subsystem is the single reachable web scraper feeding the knowledge
base. This module turns a scrape run (driven through :class:`RPAExecutor` via
:func:`app.rpa.report.run_scrape_report`) into provenance-tagged chunk dicts
ready for the shared KB persistence helper
(:func:`app.api.knowledge._ingest_chunks_from_source`).

Every chunk records its origin — ``source_url``, ``source_type`` and a
``doc_content_hash`` (SHA-256 of the full extracted page text) — so the SAME
content re-scraped, or arriving from ingestion/OCR, dedups against the one store
via ``KnowledgeStore.exists_by_hash``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScrapedPage:
    """Result of scraping one URL into KB-ready chunks."""

    source_url: str
    content: str
    content_hash: str
    chunks: list[dict[str, Any]]
    provenance: dict[str, Any] = field(default_factory=dict)


async def scrape_url_to_chunks(
    executor: Any,
    *,
    url: str,
    selectors: list[str] | None = None,
    source_type: str = "rpa",
    max_chars: int = 50_000,
    session_id: str | None = None,
) -> ScrapedPage:
    """Scrape ``url`` via ``executor`` and return provenance-tagged chunk dicts.

    Uses the WS-13 real-HTTP fallback (``allow_http_fetch=True``) so a browser-less
    deployment still returns REAL page text. Text-bearing scrape sections are
    concatenated, token-chunked, and each chunk is stamped with the shared
    ``doc_content_hash`` for cross-source dedup.
    """
    from app.knowledge.chunker_v2 import chunk_by_tokens
    from app.rpa.report import run_scrape_report

    report, _results = await run_scrape_report(
        executor,
        url=url,
        selectors=selectors,
        session_id=session_id,
        allow_http_fetch=True,
    )

    content = "\n\n".join(s.body for s in report.sections if s.body.strip()).strip()
    content = content[:max_chars]
    if not content:
        return ScrapedPage(source_url=url, content="", content_hash="", chunks=[])

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    raw_chunks = chunk_by_tokens(content, max_tokens=512, overlap_tokens=64) or [content]

    provenance = {
        "source_url": url,
        "source_type": source_type,
        "ingestion_provenance": "rpa",
        "doc_content_hash": content_hash,
    }
    chunks: list[dict[str, Any]] = []
    for i, chunk_text in enumerate(raw_chunks):
        chunks.append(
            {
                "content": chunk_text,
                "source_url": url,
                "source_type": source_type,
                "source_doc_id": url,
                "page_number": None,
                "metadata": {
                    "source_url": url,
                    "source_type": source_type,
                    "ingestion_provenance": "rpa",
                    "doc_content_hash": content_hash,
                    "content_hash": content_hash,
                    "chunk_index": str(i),
                },
            }
        )

    return ScrapedPage(
        source_url=url,
        content=content,
        content_hash=content_hash,
        chunks=chunks,
        provenance=provenance,
    )
