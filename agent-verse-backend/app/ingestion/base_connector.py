"""BaseConnector — the single interface every source adapter implements.

LAW-01: All sources → BaseConnector.get_delta() → IngestionPipeline
LAW-03: Every connector implements incremental delta with cursor
LAW-13: No credentials in connector code — vault:// references only
LAW-21: Every connector exposes validate_connection() health probe
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig


def stable_doc_id(config: SourceConfig, *key_parts: object) -> str:
    """A document id derived from the Source and the item's native identity.

    The same upstream item (an issue key, a blob path, a message id, a row's
    primary key) gets the same id on every sync, so a re-sync replaces the
    indexed document instead of adding a second copy beside it (connectors used
    to mint a random ``uuid4`` per sync and relied on content-hash dedup alone:
    an *edited* item was indexed again next to its stale version). The Source id
    is part of the key, so two Sources reading the same upstream never collide.
    """
    import uuid

    key = "\x1f".join(str(part) for part in key_parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentverse-source:{config.source_id}:{key}"))


def row_identity(row: dict[str, Any], id_column: str | None = None) -> str:
    """Native identity of a database row: its configured / conventional primary
    key, or — with no key column — a hash of the row's values (identical rows
    keep one id; an edited key-less row cannot be related to its old version)."""
    import hashlib
    import json

    candidates = [id_column] if id_column else ["id", "_id", "uuid", "pk", "key"]
    for column in candidates:
        if column and row.get(column) is not None:
            return f"{column}={row[column]}"
    digest = hashlib.sha256(
        json.dumps(row, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"row-sha256={digest}"


class ConnectorUnavailableError(RuntimeError):
    """The connector cannot run at all (e.g. its SDK is not installed).

    Raised from ``get_delta`` so the sync fails loudly — returning no documents
    would be reported as a successful, empty sync.
    """


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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Guard every connector's entry points on its SDK being installed.

        A connector whose SDK is missing used to log and ``return`` from
        ``get_delta`` — reported as a successful, empty sync. Each subclass's own
        ``get_delta`` / ``on_webhook`` / ``validate_connection`` is wrapped so that,
        before any connector code runs, a missing SDK (per
        :mod:`app.ingestion.connector_sdks`) raises
        :class:`ConnectorUnavailableError` / returns an unhealthy result with the
        reason.
        """
        super().__init_subclass__(**kwargs)
        own = vars(cls)
        if "get_delta" in own:
            cls.get_delta = _sdk_guarded_stream(own["get_delta"])  # type: ignore[method-assign]
        if "on_webhook" in own:
            cls.on_webhook = _sdk_guarded_stream(own["on_webhook"])  # type: ignore[method-assign]
        if "validate_connection" in own:
            cls.validate_connection = _sdk_guarded_validate(  # type: ignore[method-assign]
                own["validate_connection"]
            )

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

    async def delete_doc(  # noqa: B027  # intentional optional no-op hook, not abstract by design
        self,
        config: SourceConfig,
        doc_id: str,
    ) -> None:
        """Signal that a source document was deleted.

        Called when source-side deletion is detected via CDC or webhook.
        Default: no-op. Override for connectors that track deletions.
        """

    async def list_live_doc_ids(self, config: SourceConfig) -> set[str] | None:
        """Every document id that currently exists upstream, or None if unknowable.

        Override only where a complete, authoritative listing is cheap (an object
        store's key listing). After a sync with no failures, the scheduler
        deletes this Source's indexed documents whose ids are not in the set
        (upstream deletions), skipping any under legal hold. ``None`` — the
        default — means "cannot know", and nothing is ever deleted. Must raise
        rather than return a partial set.
        """
        return None

    def manages_doc_id(self, doc_id: str) -> bool:
        """True for ids this connector's *current* id scheme produces.

        Only such documents are deletion candidates: documents indexed before
        ids were stable (random ``uuid4``) can never appear in the live listing,
        and deleting them would drop content whose unchanged upstream item is
        not re-fetched by an incremental sync. Default: :func:`stable_doc_id`
        ids (UUID version 5).
        """
        import uuid

        try:
            return uuid.UUID(str(doc_id)).version == 5
        except ValueError:
            return False

    def estimate_doc_count(self, config: SourceConfig) -> int | None:
        """Estimate total document count for progress reporting.

        Returns None if unknown (streaming sources, large DBs without COUNT).
        Used to show progress bar in UI (docs_indexed / total_estimate).

        Synchronous by contract and may do network I/O (S3 lists the bucket):
        async code must call :meth:`estimate_doc_count_async` instead.
        """
        return None

    async def estimate_doc_count_async(self, config: SourceConfig) -> int | None:
        """:meth:`estimate_doc_count` on the bounded SDK pool, off the event loop."""
        from app.ingestion.sdk_executor import run_blocking

        return await run_blocking(self.estimate_doc_count, config)

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
        return not (exclude and any(fnmatch.fnmatch(name, p) for p in exclude))


# ── SDK availability guard (see BaseConnector.__init_subclass__) ──────────────


def _sdk_reason(connector: Any) -> str:
    from app.ingestion.connector_sdks import unavailable_reason

    source_type = getattr(connector, "source_type", "")
    return unavailable_reason(source_type) if isinstance(source_type, str) else ""


def _sdk_guarded_stream(fn: Any) -> Any:
    """Wrap an async-generator entry point (get_delta / on_webhook)."""

    @functools.wraps(fn)
    async def guarded(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        reason = _sdk_reason(self)
        if reason:
            raise ConnectorUnavailableError(reason)
        stream = fn(self, *args, **kwargs)
        try:
            async for item in stream:
                yield item
        finally:
            await stream.aclose()

    return guarded


def _sdk_guarded_validate(fn: Any) -> Any:
    @functools.wraps(fn)
    async def guarded(self: Any, *args: Any, **kwargs: Any) -> ConnectionHealth:
        reason = _sdk_reason(self)
        if reason:
            return ConnectionHealth(ok=False, error=reason)
        result: ConnectionHealth = await fn(self, *args, **kwargs)
        return result

    return guarded
