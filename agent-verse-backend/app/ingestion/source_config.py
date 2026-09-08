"""SourceConfig, RawDocument, IngestionJob, SourceFamily — unified data model.

LAW-04: tenant_id on every record
LAW-05: provenance fields are immutable after first write
LAW-08: embedding_model tracked per chunk for re-embedding on model change
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field


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

    # ── Freshness (LAW-08) ────────────────────────────────────────────────────
    freshness_ttl_seconds: int = 86400  # 24h default

    # ── Routing ───────────────────────────────────────────────────────────────
    collection_id: str = ""  # target knowledge collection
    tags: list[str] = field(default_factory=list)

    # ── Stats (read-only, managed by IngestionJobTracker) ─────────────────────
    last_synced_at: str | None = None
    total_docs_indexed: int = 0
    total_chunks: int = 0
    consecutive_failures: int = 0  # for exponential backoff (LAW-09)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


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
