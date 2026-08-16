/**
 * Ingestion system TypeScript types — all 18 source families, ~230 sources.
 */

export type SourceFamily =
  | "object_storage" | "olap_database" | "oltp_database" | "nosql_database"
  | "streaming" | "file_system" | "document_store" | "communication"
  | "code_repository" | "web" | "crm_erp" | "support"
  | "iot_telemetry" | "observability" | "scientific"
  | "graph_database" | "vector_database" | "agent_generated";

export interface SourceConfig {
  source_id:            string;
  tenant_id:            string;
  name:                 string;
  family:               SourceFamily;
  source_type:          string;
  enabled:              boolean;
  sync_mode:            "full" | "incremental" | "streaming";
  sync_interval_seconds: number;
  connection_config:    Record<string, unknown>;
  cursor_value:         string;
  include_patterns:     string[];
  exclude_patterns:     string[];
  max_doc_size_bytes:   number;
  chunking_strategy:    string;
  chunk_size_tokens:    number;
  chunk_overlap_tokens: number;
  embedding_model:      string;
  language_hint:        string;
  inherit_source_acl:   boolean;
  min_quality_score:    number;
  pii_action:           "redact" | "reject" | "allow";
  freshness_ttl_seconds: number;
  collection_id:        string | null;
  tags:                 string[];
  last_synced_at:       string | null;
  total_docs_indexed:   number;
  total_chunks:         number;
  version:              number;
  created_at:           string;
  updated_at:           string;
}

export interface IngestionJob {
  job_id:           string;
  source_id:        string;
  tenant_id:        string;
  status:           "pending" | "running" | "completed" | "failed" | "paused";
  sync_mode:        "full" | "incremental" | "streaming";
  triggered_by:     string;
  started_at:       string | null;
  completed_at:     string | null;
  docs_discovered:  number;
  docs_indexed:     number;
  docs_skipped:     number;
  docs_failed:      number;
  chunks_created:   number;
  bytes_processed:  number;
  tokens_consumed:  number;
  cursor_before:    string;
  cursor_after:     string;
  error_message:    string;
  created_at:       string;
}

export interface ConnectionHealth {
  ok:         boolean;
  latency_ms: number;
  error:      string | null;
  metadata:   Record<string, unknown>;
}

export interface IndexedDocument {
  id:               string;
  source_id:        string;
  doc_id:           string;
  title:            string;
  source_url:       string;
  content_hash:     string;
  language:         string;
  chunk_count:      number;
  quality_score:    number;
  has_pii_redacted: boolean;
  ingested_at:      string;
  expires_at:       string | null;
}

export interface DLQEntry {
  id:            string;
  source_id:     string;
  doc_id:        string;
  failed_stage:  string;
  failure_type:  string;
  error_message: string;
  retry_count:   number;
  next_retry_at: string | null;
  created_at:    string;
}

export interface IngestionQuota {
  tenant_id:       string;
  plan:            string;
  sources_used:    number;
  sources_limit:   number | null;
  docs_used:       number;
  docs_limit:      number | null;
  tokens_used_month: number;
  tokens_limit_month: number | null;
  cost_usd_month:  number;
}

export interface ConnectorMeta {
  source_type:        string;
  class:              string;
  supports_streaming: boolean;
  supports_acl:       boolean;
  supports_deletion:  boolean;
  feature_flag:       string | null;
}

// ── Family metadata ───────────────────────────────────────────────────────────

export const FAMILY_CONFIG: Record<SourceFamily, {
  label: string;
  icon: string;
  color: string;
  description: string;
}> = {
  object_storage:  { label: "Object Storage",    icon: "Cloud",       color: "sky-500",     description: "S3, GCS, Azure Blob, MinIO, R2" },
  olap_database:   { label: "OLAP / Analytics",  icon: "BarChart3",   color: "violet-500",  description: "Snowflake, BigQuery, ClickHouse, Databricks" },
  oltp_database:   { label: "Relational DB",     icon: "Database",    color: "blue-500",    description: "PostgreSQL, MySQL, MSSQL, Oracle" },
  nosql_database:  { label: "NoSQL Database",    icon: "Layers",      color: "indigo-500",  description: "MongoDB, DynamoDB, Firestore, Cosmos DB" },
  streaming:       { label: "Streaming",         icon: "Zap",         color: "amber-500",   description: "Kafka, Kinesis, Pub/Sub, Pulsar" },
  file_system:     { label: "File System",       icon: "HardDrive",   color: "orange-500",  description: "Local FS, NFS, SFTP" },
  document_store:  { label: "Documents & Drive", icon: "FileText",    color: "yellow-500",  description: "GDrive, Notion, Confluence, SharePoint" },
  communication:   { label: "Communication",     icon: "MessageSquare",color: "green-500",  description: "Slack, Teams, Discord, Email, SMS" },
  code_repository: { label: "Code & Dev",        icon: "Code2",       color: "teal-500",    description: "GitHub, GitLab, Bitbucket, Jira, npm" },
  web:             { label: "Web & Internet",    icon: "Globe",       color: "cyan-500",    description: "URL crawl, RSS, arXiv, Reddit" },
  crm_erp:         { label: "CRM & ERP",         icon: "Briefcase",   color: "rose-500",    description: "Salesforce, HubSpot, SAP, Workday" },
  support:         { label: "Customer Support",  icon: "Headphones",  color: "pink-500",    description: "Zendesk, Intercom, ServiceNow, Freshdesk" },
  iot_telemetry:   { label: "IoT & Telemetry",   icon: "Cpu",         color: "lime-500",    description: "MQTT, InfluxDB, OPC-UA, Modbus" },
  observability:   { label: "Observability",     icon: "Activity",    color: "red-500",     description: "PagerDuty, Sentry, Grafana, Prometheus" },
  scientific:      { label: "Scientific",        icon: "FlaskConical",color: "purple-500",  description: "arXiv, PubMed, FHIR, SEC EDGAR" },
  graph_database:  { label: "Graph Database",    icon: "GitBranch",   color: "fuchsia-500", description: "Neo4j, Neptune, TigerGraph, Wikidata" },
  vector_database: { label: "Vector Store",      icon: "Sparkles",    color: "emerald-500", description: "Pinecone, Weaviate, Qdrant (as source)" },
  agent_generated: { label: "Agent-Generated",   icon: "Bot",         color: "stone-500",   description: "Goal outputs, HITL decisions, learnings" },
};

export const ALL_FAMILIES = Object.keys(FAMILY_CONFIG) as SourceFamily[];
