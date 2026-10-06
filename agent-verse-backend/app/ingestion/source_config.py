"""SourceConfig, RawDocument, IngestionJob, SourceFamily — unified data model.

LAW-04: tenant_id on every record
LAW-05: provenance fields are immutable after first write
LAW-08: embedding_model tracked per chunk for re-embedding on model change
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field

# RawDocument.metadata key a connector sets on a document it could not read
# (download failure, over the size cap). The pipeline fails such a document
# with that reason (→ DLQ, visible in the job) instead of indexing it.
CONNECTOR_FAILURE_KEY = "connector_failure"
# Optional companion: False when retrying cannot help (object deleted, access
# denied, over the size cap). The DLQ retry loop gives up on such an entry at once.
CONNECTOR_FAILURE_RETRYABLE_KEY = "connector_failure_retryable"
# Optional companion: a JSON-safe reference to the original event (e.g. the S3
# bucket + key a webhook named). Stored with the DLQ entry, it lets a retry ask
# the connector to fetch the item again (BaseConnector.replay_event) instead of
# replaying the empty failure document.
CONNECTOR_REPLAY_KEY = "connector_replay"
# RawDocument.metadata key a connector sets when the URL it was configured with
# moved permanently (301/308): ``{"from": old, "to": new, "status": code}``. The
# sync surfaces it on the job and records the new URL on the Source (USR-5).
CONNECTOR_MOVED_KEY = "connector_moved_permanently"
# RawDocument.metadata key: the id this document had under the connector's
# previous id scheme (e.g. S3's bare ``s3://bucket/key``). The pipeline replaces
# that legacy document with this one in the same transaction — only when the
# legacy document is attributed to the same Source.
CONNECTOR_LEGACY_DOC_ID_KEY = "connector_legacy_doc_id"


class IngestionStatus(enum.StrEnum):
    """Job completion statuses used by scheduler and job tracker."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # some docs failed but most succeeded
    FAILED = "failed"
    PAUSED = "paused"


class SourceFamily(enum.StrEnum):
    """18 source families covering ~230 source types."""

    OBJECT_STORAGE = "object_storage"  # S3, GCS, Azure Blob, MinIO
    OLAP_DATABASE = "olap_database"  # ClickHouse, Snowflake, BigQuery
    OLTP_DATABASE = "oltp_database"  # PostgreSQL, MySQL, MSSQL, Oracle
    NOSQL_DATABASE = "nosql_database"  # MongoDB, DynamoDB, Firestore
    STREAMING = "streaming"  # Kafka, Pulsar, Kinesis, Pub/Sub
    FILE_SYSTEM = "file_system"  # Local FS, NFS, SFTP
    DOCUMENT_STORE = "document_store"  # GDrive, Notion, Confluence, SharePoint
    COMMUNICATION = "communication"  # Slack, Teams, Discord, Email
    CODE_REPOSITORY = "code_repository"  # GitHub, GitLab, Bitbucket
    WEB = "web"  # URL crawl, RSS, Reddit, HN
    CRM_ERP = "crm_erp"  # Salesforce, HubSpot, SAP, Workday
    SUPPORT = "support"  # Zendesk, Intercom, ServiceNow
    IOT_TELEMETRY = "iot_telemetry"  # MQTT, InfluxDB, OPC-UA, Modbus
    OBSERVABILITY = "observability"  # PagerDuty, Sentry, Grafana, Prometheus
    SCIENTIFIC = "scientific"  # arXiv, PubMed, FHIR, SEC EDGAR
    GRAPH_DATABASE = "graph_database"  # Neo4j, Neptune, TigerGraph, Wikidata
    VECTOR_DATABASE = "vector_database"  # Pinecone, Weaviate, Qdrant (as source)
    AGENT_GENERATED = "agent_generated"  # Goal outputs, HITL decisions, learnings


@dataclass
class SourceConfig:
    """Full configuration for one knowledge source.

    All secret values stored as vault:// references (LAW-13).
    Cursor tracks incremental sync position (LAW-03).
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    source_id: str
    tenant_id: str
    name: str
    family: SourceFamily
    source_type: str  # e.g. "s3", "snowflake", "slack"
    enabled: bool = True

    # ── Connection (LAW-13: secrets as vault:// references) ──────────────────
    connection_config: dict = field(default_factory=dict)

    # ── Sync policy ───────────────────────────────────────────────────────────
    sync_mode: str = "incremental"  # full | incremental | streaming
    sync_interval_seconds: int = 3600
    cursor_field: str = ""  # e.g. "updated_at"
    cursor_value: str = ""  # last processed position

    # ── Content filtering ─────────────────────────────────────────────────────
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    max_doc_size_bytes: int = 10_485_760  # 10 MB

    # ── Processing policy ─────────────────────────────────────────────────────
    chunking_strategy: str = "auto"
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    embedding_model: str = "auto"
    language_hint: str = ""

    # ── Access control (LAW-07) ───────────────────────────────────────────────
    inherit_source_acl: bool = True
    allowed_roles: list[str] = field(default_factory=list)
    allowed_user_ids: list[str] = field(default_factory=list)

    # ── Quality & PII (LAW-06) ────────────────────────────────────────────────
    min_quality_score: float = 0.3
    pii_action: str = "redact"  # redact | reject | allow
    # Semantic near-duplicate chunk removal (pipeline Stage 11). Chunks whose
    # embedding cosine-similarity to an already-kept chunk is >= this threshold
    # are dropped as near-dupes (on top of exact SHA-256 dedup). 0.0 disables it
    # (exact-hash only) — the safe default, since a too-low threshold would drop
    # legitimately distinct-but-similar chunks. ~0.97-0.99 catches boilerplate.
    near_dup_threshold: float = 0.0

    # ── Freshness (LAW-08) ────────────────────────────────────────────────────
    freshness_ttl_seconds: int = 86400  # 24h default

    # ── Routing ───────────────────────────────────────────────────────────────
    collection_id: str = ""  # target knowledge collection
    tags: list[str] = field(default_factory=list)

    # ── Configuration health (L-02) ───────────────────────────────────────────
    # ``needs_configuration`` parks the Source: the beat due-scan and the DLQ
    # retry skip it and a manual sync is refused with ``config_status_reason``
    # until an update fixes it (see :func:`configuration_problem`).
    config_status: str = "ok"  # ok | needs_configuration
    config_status_reason: str = ""

    # ── Stats (read-only, managed by IngestionJobTracker) ─────────────────────
    last_synced_at: str | None = None
    total_docs_indexed: int = 0
    total_chunks: int = 0
    consecutive_failures: int = 0  # for exponential backoff (LAW-09)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


CONFIG_STATUS_OK = "ok"
CONFIG_STATUS_NEEDS_CONFIGURATION = "needs_configuration"


def configuration_problem(config: SourceConfig) -> str | None:
    """Why this Source can never index anything as configured, or None.

    Checked before a sync runs: a Source without a target collection made every
    document fail the pipeline's index stage (and land in the DLQ) on every
    scheduled run, forever.
    """
    if not (config.collection_id or "").strip():
        return (
            "no target knowledge collection (collection_id) is set; "
            "choose a collection for this source to resume syncing"
        )
    if config.source_type == "minio" and not str(
        (config.connection_config or {}).get("endpoint_url") or ""
    ).strip():
        # MinIO has no public default endpoint; the sync could only fail.
        return "no MinIO endpoint (connection_config.endpoint_url) is set"
    return None


def apply_configuration_health(config: SourceConfig) -> None:
    """Set ``config_status`` / ``config_status_reason`` from the config itself."""
    problem = configuration_problem(config)
    if problem is None:
        config.config_status = CONFIG_STATUS_OK
        config.config_status_reason = ""
    else:
        config.config_status = CONFIG_STATUS_NEEDS_CONFIGURATION
        config.config_status_reason = problem


@dataclass
class RawDocument:
    """Normalised document yielded by any BaseConnector.

    LAW-17: correlation_id tracks the document's full pipeline journey.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    doc_id: str  # source-assigned unique ID
    source_id: str  # SourceConfig.source_id
    tenant_id: str

    # ── Content ───────────────────────────────────────────────────────────────
    content: bytes  # raw bytes (any format)
    content_type: str  # MIME type e.g. "application/pdf"

    # ── Provenance (LAW-05: immutable after write) ────────────────────────────
    title: str = ""
    source_url: str = ""
    author: str = ""
    created_at: str = ""
    modified_at: str = ""
    version: str = ""
    language: str = ""

    # ── Access control (LAW-07) ───────────────────────────────────────────────
    acl: list[str] = field(default_factory=list)

    # ── Pipeline state ────────────────────────────────────────────────────────
    content_hash: str = ""  # SHA-256; populated by pipeline Stage 3
    correlation_id: str = ""  # LAW-17: full journey tracking
    metadata: dict = field(default_factory=dict)

    def compute_hash(self) -> str:
        """Compute and cache the SHA-256 hash of the content bytes."""
        self.content_hash = hashlib.sha256(self.content).hexdigest()
        return self.content_hash


@dataclass
class IngestionJob:
    """Tracks one ingestion job (full, incremental, or streaming batch).

    LAW-18: state changes are appended as events, not mutated directly.
    """

    job_id: str
    source_id: str
    tenant_id: str
    status: str  # pending | running | completed | failed | paused
    sync_mode: str  # full | incremental | streaming
    triggered_by: str = ""  # scheduler | webhook | manual | trigger_id

    # ── Progress ──────────────────────────────────────────────────────────────
    started_at: str | None = None
    completed_at: str | None = None
    docs_discovered: int = 0
    docs_skipped: int = 0  # dedup hit
    docs_failed: int = 0
    docs_indexed: int = 0
    chunks_created: int = 0
    bytes_processed: int = 0
    tokens_consumed: int = 0

    # ── Cursor (LAW-03) ───────────────────────────────────────────────────────
    cursor_before: str = ""
    cursor_after: str = ""

    # ── Error tracking ────────────────────────────────────────────────────────
    error_message: str = ""
    created_at: str = ""


@dataclass
class PipelineResult:
    """Result of processing one RawDocument through the IngestionPipeline."""

    doc_id: str
    source_id: str
    tenant_id: str
    status: str  # indexed | skipped | failed
    skip_reason: str = ""  # dedup | quality | pii_rejected | quota | empty
    chunks_created: int = 0
    tokens_consumed: int = 0
    processing_ms: float = 0.0
    error: str = ""
    collection_id: str = ""
    kg_entities: int = 0  # D-15: entities added to the knowledge graph
    kg_relations: int = 0  # D-15: relations added to the knowledge graph

    # P0-11: read-only status accessors the scheduler consumes.
    @property
    def success(self) -> bool:
        return self.status == "indexed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"
