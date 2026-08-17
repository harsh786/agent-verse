"""ArXivConnector — arXiv preprint ingestion.

Uses arXiv API (no auth required). Cursor: last submission date.
Fetches abstract + full text via arXiv PDF → text extraction.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)
_ARXIV_BASE = "https://export.arxiv.org/api/query"


@register("arxiv")
class ArXivConnector(BaseConnector):
    """arXiv preprint connector — no API key required."""

    source_type = "arxiv"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time, httpx
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(_ARXIV_BASE, params={"search_query": "all:quantum", "max_results": 1})
                r.raise_for_status()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"api": "arxiv.org"})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        import httpx

        cc = config.connection_config
        categories = cc.get("categories") or []
        keywords = cc.get("keywords") or []
        max_results = int(cc.get("max_results", 50))

        # Build search query
        parts: list[str] = []
        for cat in categories:
            parts.append(f"cat:{cat}")
        for kw in keywords:
            parts.append(f"all:{kw}")
        search_query = " AND ".join(parts) if parts else "all:machine+learning"

        new_cursor = cursor or ""

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                _ARXIV_BASE,
                params={
                    "search_query": search_query,
                    "max_results": max_results,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            r.raise_for_status()

            # Parse Atom XML response
            from xml.etree import ElementTree as ET
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }
            root = ET.fromstring(r.text)
            for entry in root.findall("atom:entry", ns):
                arxiv_id_raw = (entry.findtext("atom:id", "", ns) or "").strip()
                arxiv_id = re.sub(r".*abs/", "", arxiv_id_raw)
                published = entry.findtext("atom:published", "", ns).strip()
                if cursor and published and published <= cursor:
                    continue
                new_cursor = max(new_cursor, published)

                title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
                abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
                authors_el = entry.findall("atom:author/atom:name", ns)
                authors = ", ".join(a.text for a in authors_el if a.text)
                categories_el = entry.findall("arxiv:primary_category", ns)
                primary_cat = categories_el[0].get("term", "") if categories_el else ""

                text = f"# {title}\n\nAuthors: {authors}\nCategories: {primary_cat}\nPublished: {published}\n\n## Abstract\n\n{abstract}"

                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"https://arxiv.org/abs/{arxiv_id}",
                    content=text.encode(),
                    content_type="text/plain",
                    metadata={"arxiv_id": arxiv_id, "title": title, "authors": authors, "published": published},
                )
                yield doc, new_cursor
