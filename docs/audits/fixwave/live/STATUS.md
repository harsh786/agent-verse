# Live e2e status (sequential)

| Order | Item | Status | Evidence / commits |
|---|---|---|---|
| 1 | A1 File upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, OCR, ZIP) | COMPLETE (9/10); PDF table-row ranking → P2 | P1a merged 4e38f7fa8; report live/p1a-file-upload.md; KB-COMPLEX-CORPUS 10/10, KB-UPLOAD-HARD 18/19 |
| 2 | A2 S3, MinIO | IN PROGRESS (P1b) | |
| 3 | A3 PostgreSQL, MySQL | IN PROGRESS (P1b, after A2) | |
| 4 | A5 MongoDB, Redis, Elasticsearch | PENDING (MongoDB fix tracks running) | |
| 5 | A6 Kafka | PENDING | |
| 6 | A7 Google Drive, SharePoint, Confluence, Notion | PENDING | |
| 7 | A10 HTTP URL, web crawl | PENDING | |
| 8 | A12 Agent-generated | PENDING | |
| 9 | B1 Time triggers | PENDING | |
| 10 | B2 webhook, rest, event | PENDING | |
| 11 | B3 github, stripe, jira, teams_webhook | PENDING | |
| 12 | B7 Platform events | PENDING | |
| 13 | B8 Conversational | PENDING | |
| 14 | C1–C5 Channels: Telegram, WhatsApp, Slack, Teams, generic webhook (inbound → tenant binding → goal/chat → outbound reply) | PENDING — known: Teams routes by shared serviceUrl (cross-tenant), Slack slash commands single-tenant, channel→tenant binding only via CHANNEL_TENANT_MAP env, per-org gateway config 501, Telegram/WhatsApp have no e2e | |
