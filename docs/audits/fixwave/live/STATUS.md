# Live e2e status (sequential)

| Order | Item | Status | Evidence / commits |
|---|---|---|---|
| 1 | A1 File upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, OCR, ZIP) | COMPLETE (9/10); PDF table-row ranking → P2 | P1a merged 4e38f7fa8; report live/p1a-file-upload.md; KB-COMPLEX-CORPUS 10/10, KB-UPLOAD-HARD 18/19 |
| 2 | A2 S3, MinIO | COMPLETE (fixed: P1b-1..6, -8, -9, -10) | P1b branch `live/p1b-storage-oltp`; report live/p1b-storage-oltp.md; SRC-OBJ-* all pass (both flavors), UI wizard verified |
| 3 | A3 PostgreSQL, MySQL | COMPLETE (fixed: P1b-7, + P1b-1/-5) | report live/p1b-storage-oltp.md; SRC-DB-SYNC / TABLE-RETRY / FAILURES pass on both engines |
| 4 | A5 MongoDB, Redis, Elasticsearch | COMPLETE — MongoDB ingestion (fixed: P1c-1, -2, -3, -4, -13); MongoDB MCP (fixed: P1c-5); Redis (fixed: P1c-7..-10, -13); Elasticsearch (fixed: P1c-6, -11, -12, -13) | P1c branch `live/p1c-nosql`; report live/p1c-nosql.md; SRC-MONGO-*, MCP-MONGO-* (incl. HITL, kill switches), SRC-REDIS-*, SRC-ES-* all pass on rebuilt images; P1b regression 12/12; open items → P2 (rerank calibration), P4/P5 (repeated approved delete), owner (Redis host-based ids) |
| 5 | A6 Kafka | PENDING | |
| 6 | A7 Google Drive, SharePoint, Confluence, Notion | PENDING | |
| 7 | A10 HTTP URL, web crawl | COMPLETE — HTTP URL ingest (fixed: P1d-1, -2, -3, -4, -5); web crawl (fixed: P1d-6, -7, -8, -9, -11); remaining regex HTML paths (fixed: P1d-10) | P1d branch `live/p1d-web-crawl`; report live/p1d-web-crawl.md; WEB-URL-* (7) and WEB-CRAWL-* (4) all pass on rebuilt images (baseline 0/11); P1b/P1c regression 28/28 on the P1d images (final 39/39), WEB-* 11/11 again after merging main 639d30ce7; open items → product (URL Source type), P2 (pipeline min length), P9 (crawl live set in the cursor, throughput) |
| 8 | A12 Agent-generated | PENDING | |
| 9 | B1 Time triggers | PENDING | |
| 10 | B2 webhook, rest, event | PENDING | |
| 11 | B3 github, stripe, jira, teams_webhook | PENDING | |
| 12 | B7 Platform events | PENDING | |
| 13 | B8 Conversational | PENDING | |
| 14 | C1–C5 Channels: Telegram, WhatsApp, Slack, Teams, generic webhook (inbound → tenant binding → goal/chat → outbound reply) | PENDING — known: Teams routes by shared serviceUrl (cross-tenant), Slack slash commands single-tenant, channel→tenant binding only via CHANNEL_TENANT_MAP env, per-org gateway config 501, Telegram/WhatsApp have no e2e | |
