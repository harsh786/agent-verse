"""BaseConnector — the single interface every source adapter implements.

LAW-01: All sources → BaseConnector.get_delta() → IngestionPipeline
LAW-03: Every connector implements incremental delta with cursor
LAW-13: No credentials in connector code — vault:// references only
LAW-21: Every connector exposes validate_connection() health probe
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig


@dataclass
class ConnectionHealth:
    """Result of BaseConnector.validate_connection()."""

    ok: bool
    latency_ms: float = 0.0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata examples:
    #   S3:         {"bucket": "x", "region": "us-east-1", "doc_count_estimate": 12000}
    #   Snowflake:  {"account": "x", "warehouse": "COMPUTE_WH", "tables": ["ORDERS"]}
    #   Slack:      {"workspace": "My Corp", "channel_count": 48}


class BaseConnector(ABC):
    """Abstract base every ingestion connector implements.

    Connectors are thin stateless adapters. They:
      1. Authenticate with the source
      2. Fetch content incrementally (get_delta)
      3. Normalize content into RawDocument
      4. Yield (RawDocument, new_cursor)

    All intelligence (PII, quality, chunking, embedding, indexing)
    lives in IngestionPipeline. Connectors never call these directly.

    Usage:
        @register("s3")
        class S3Connector(BaseConnector):
            source_type = "s3"
            ...
    """

    # ── Subclass MUST set these ───────────────────────────────────────────────

    @property
    @abstractmethod
    def source_type(self) -> str:
        """e.g. 's3', 'snowflake', 'kafka', 'slack', 'github'"""

    # ── Subclass MUST implement these ─────────────────────────────────────────

    @abstractmethod
    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        """Test connectivity and auth.

        Called:
        - During source creation (pre-save validation)
        - Scheduled health checks every 5 minutes (LAW-21)
        - Circuit breaker reset decision (probe in half_open state)

        Must complete within 10 seconds or raise TimeoutError.
        """

    @abstractmethod
    async def get_delta(
        self,
        config: SourceConfig,
        cursor: str | None,
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield (document, new_cursor) tuples incrementally.

        Contract (LAW-03):
        - cursor=None means full initial sync
        - Must yield in source-modified-at ascending order
        - MUST be resumable: interrupted at any point, resume from last cursor
        - MUST be idempotent: same cursor produces same documents

        Cursor format examples:
        - S3:        "2026-08-17T10:00:00Z"     (LastModified timestamp)
        - Kafka:     '{"orders": {"0": 1234}}'   (partition:offset JSON)
        - Snowflake: "2026-08-17T10:00:00.000Z"  (ISO timestamp)
        - MongoDB:   '{"_data": "...token..."}'   (resume token)
        - GitHub:    "abc123def456"               (commit SHA)
        """

    # ── Optional overrides ────────────────────────────────────────────────────

    async def on_webhook(
        self,
        config: SourceConfig,
        payload: bytes,
        headers: dict[str, str],
    ) -> AsyncIterator[RawDocument]:
        """Handle real-time push events (webhooks/notifications).

        Override for: S3 event notifications, GitHub webhooks,
        Slack events, DB change notifications, etc.

        Default: raises NotImplementedError (poll-only sources).
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support webhook mode")

    async def get_acl(
        self,
        config: SourceConfig,
        doc_id: str,
    ) -> list[str]:
        """Return allowed principals for a document (LAW-07).

        Default: empty list (tenant-wide access).
        Override for: GitHub (repo visibility + teams), Slack (channel members),
        GDrive (file permissions), Confluence (space/page restrictions).
        """
        return []

    async def delete_doc(
        self,
        config: SourceConfig,
        doc_id: str,
    ) -> None:
        """Signal that a source document was deleted.

        Called when source-side deletion is detected via CDC or webhook.
        Default: no-op. Override for connectors that track deletions.
        """

    def estimate_doc_count(self, config: SourceConfig) -> int | None:
        """Estimate total document count for progress reporting.

        Returns None if unknown (streaming sources, large DBs without COUNT).
        Used to show progress bar in UI (docs_indexed / total_estimate).
        """
        return None

    # ── Capability flags ──────────────────────────────────────────────────────

    @property
    def supports_streaming(self) -> bool:
        """True if this connector supports real-time push/streaming mode."""
        return False

    @property
    def supports_acl_propagation(self) -> bool:
        """True if source permissions can be read and stored on chunks."""
        return False

    @property
    def supports_deletion_tracking(self) -> bool:
        """True if the source exposes deleted-document events (CDC/webhooks)."""
        return False

    @property
    def supports_dry_run(self) -> bool:
        """True if validate_connection can also estimate doc count (LAW-22)."""
        return False

    @staticmethod
    def _matches(name: str, include: list[str], exclude: list[str]) -> bool:
        """Return True if `name` passes include/exclude glob patterns.

        - If include is non-empty, name must match at least one include pattern.
        - If exclude is non-empty, name must NOT match any exclude pattern.
        - Empty lists mean "no filter" (all pass).
        """
        import fnmatch

        if include and not any(fnmatch.fnmatch(name, p) for p in include):
            return False
        if exclude and any(fnmatch.fnmatch(name, p) for p in exclude):
            return False
        return True
