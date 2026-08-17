"""AgentGeneratedConnector — auto-ingest goal outputs, HITL decisions, learnings.

The highest-quality knowledge source: human-approved answers and
high-scoring agent outputs are indexed for future agent retrieval.
Uses Redis pub/sub for real-time ingestion on goal completion.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("agent_generated")
class AgentGeneratedConnector(BaseConnector):
    """Ingest agent goal outputs and HITL decisions as knowledge.

    sync_mode=streaming: subscribes to Redis goal.completed events
    sync_mode=incremental: queries goal_outputs table with cursor
    """

    source_type = "agent_generated"
    supports_streaming = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        return ConnectionHealth(
            ok=True,
            latency_ms=0.0,
            metadata={"source_types": config.connection_config.get("source_types", [])},
        )

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield high-quality goal outputs since cursor timestamp."""

        min_score = config.connection_config.get("min_eval_score", 0.7)
        source_types = config.connection_config.get(
            "source_types", ["goal_output", "hitl_decision"]
        )
        agent_ids = config.connection_config.get("agent_ids", [])

        # Query goal_outputs table for completed goals above quality threshold
        # cursor = ISO timestamp of last ingested goal
        # This is a polling path; streaming path uses on_webhook
        _log.debug(
            "agent_generated_delta cursor=%s min_score=%.2f types=%s",
            cursor, min_score, source_types,
        )

        # Yield nothing in pull mode — streaming mode via on_webhook is primary
        # Implement DB query when DB integration is available
        return
        yield  # Make this an async generator  # noqa: unreachable

    async def on_webhook(
        self,
        config: SourceConfig,
        payload: bytes,
        headers: dict[str, str],
    ) -> AsyncIterator[RawDocument]:
        """Handle goal.completed Redis events → immediately index output."""
        import json

        from app.ingestion.source_config import RawDocument

        try:
            data = json.loads(payload)
        except Exception:
            return

        min_score = config.connection_config.get("min_eval_score", 0.7)
        score = data.get("score", 1.0)

        if score < min_score:
            _log.debug(
                "agent_generated_skip score=%.2f < min=%.2f goal=%s",
                score, min_score, data.get("goal_id"),
            )
            return

        output = data.get("output", {})
        text = output if isinstance(output, str) else str(output)

        if not text.strip():
            return

        goal_id = data.get("goal_id", uuid.uuid4().hex)
        yield RawDocument(
            doc_id=goal_id,
            source_id=config.source_id,
            tenant_id=data.get("tenant_id", config.tenant_id),
            content=text.encode("utf-8"),
            content_type="text/plain",
            source_url=f"agentverse://goals/{goal_id}",
            title=data.get("goal_text", "")[:200],
            metadata={
                "goal_id": goal_id,
                "agent_id": data.get("agent_id", ""),
                "eval_score": score,
                "source_type": "agent_generated",
            },
        )
