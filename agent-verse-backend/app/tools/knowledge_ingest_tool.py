"""knowledge.ingest — tool for agents and workflows to index content on-the-fly.

Agents invoke this as a workflow step:
  tool: knowledge.ingest
  inputs:
    content_or_url: "{{trigger.payload.report_url}}"
    wait_for_completion: true

Supports:
  - URL fetching → full pipeline
  - Raw text input → pipeline
  - Auto-collection routing (uses agent's default collection)
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

_log = logging.getLogger(__name__)


class KnowledgeIngestTool:
    """Ingest a URL or text content into the knowledge store.

    Registered in the tool registry at app startup.
    Used in workflow steps and by agents via tool calls.
    """

    name = "knowledge.ingest"
    description = (
        "Ingest a URL or raw text content into the knowledge store for future "
        "retrieval by agents. Returns job status and chunk count."
    )
    parameters = {
        "type": "object",
        "required": ["content_or_url"],
        "properties": {
            "content_or_url": {
                "type": "string",
                "description": "URL to fetch and ingest, or raw text content to index directly",
            },
            "collection_id": {
                "type": "string",
                "description": "Target knowledge collection ID. Uses agent default if not specified.",  # noqa: E501
            },
            "title": {
                "type": "string",
                "description": "Optional document title for better citation display",
            },
            "wait_for_completion": {
                "type": "boolean",
                "description": "Block until content is indexed (default: true). Set false for fire-and-forget.",  # noqa: E501
                "default": True,
            },
            "dry_run": {
                "type": "boolean",
                "description": "Parse and chunk without embedding (for cost estimation).",
                "default": False,
            },
        },
    }

    async def execute(
        self,
        content_or_url: str,
        *,
        collection_id: str = "",
        title: str = "",
        wait_for_completion: bool = True,
        dry_run: bool = False,
        tenant_ctx: Any = None,
        pipeline: Any = None,  # IngestionPipeline
        agent_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Execute the ingestion tool.

        Returns dict with: job_status, chunks_created, doc_id, skip_reason, cost_estimate
        """
        if not content_or_url.strip():
            return {"error": "content_or_url is required", "job_status": "failed"}

        if pipeline is None:
            return {"error": "Ingestion pipeline not configured", "job_status": "failed"}

        tenant_id = getattr(tenant_ctx, "tenant_id", "") if tenant_ctx else ""

        # Resolve collection — use agent default if not specified
        if not collection_id and tenant_ctx:
            collection_id = getattr(tenant_ctx, "default_collection_id", "") or ""

        # Build RawDocument
        raw_doc = await self._build_raw_doc(content_or_url, title=title, tenant_id=tenant_id)
        if raw_doc is None:
            return {"error": f"Failed to fetch: {content_or_url}", "job_status": "failed"}

        # Build ad-hoc SourceConfig
        from app.ingestion.source_config import SourceConfig, SourceFamily

        config = SourceConfig(
            source_id=f"adhoc_{raw_doc.doc_id[:12]}",
            tenant_id=tenant_id,
            name="Agent Ingest",
            family=SourceFamily.AGENT_GENERATED,
            source_type="adhoc",
            collection_id=collection_id,
        )

        # Run pipeline (with dry_run=LAW-22 if requested)
        if dry_run:
            pipeline._dry_run = True

        try:
            result = await pipeline.ingest(raw_doc, config)
        finally:
            if dry_run:
                pipeline._dry_run = False

        return {
            "job_status": result.status,
            "chunks_created": result.chunks_created,
            "tokens_consumed": result.tokens_consumed,
            "doc_id": result.doc_id,
            "skip_reason": result.skip_reason or None,
            "processing_ms": round(result.processing_ms, 0),
            "collection_id": collection_id,
            "error": result.error or None,
        }

    @staticmethod
    async def _build_raw_doc(content_or_url: str, *, title: str, tenant_id: str) -> Any:
        """Fetch URL or wrap raw text as RawDocument."""
        from app.ingestion.source_config import RawDocument

        doc_id = uuid.uuid4().hex

        # URL → fetch
        if content_or_url.startswith(("http://", "https://")):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                    r = await c.get(content_or_url)
                    r.raise_for_status()
                    content_bytes = r.content
                    content_type = r.headers.get("content-type", "text/html").split(";")[0]
                    inferred_title = title or content_or_url.split("/")[-1].split("?")[0]
                    return RawDocument(
                        doc_id=uuid.uuid5(uuid.NAMESPACE_URL, content_or_url).hex,
                        source_id="adhoc",
                        tenant_id=tenant_id,
                        content=content_bytes,
                        content_type=content_type,
                        source_url=content_or_url,
                        title=inferred_title,
                    )
            except Exception as exc:
                _log.warning("knowledge_ingest_fetch_error url=%s: %s", content_or_url, exc)
                return None

        # Raw text → encode as UTF-8
        content_bytes = content_or_url.encode("utf-8")
        return RawDocument(
            doc_id=hashlib.sha256(content_bytes).hexdigest()[:32],
            source_id="adhoc",
            tenant_id=tenant_id,
            content=content_bytes,
            content_type="text/plain",
            source_url=f"adhoc://{doc_id}",
            title=title or content_or_url[:80],
        )
