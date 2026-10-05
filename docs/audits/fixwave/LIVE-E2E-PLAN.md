# Live real-e2e program (sequential, 2026-10-05)

Goal (user): every capability verified **in depth on the live stack** with complex and difficult real
use cases, every failure fixed at the root, one area at a time.

Mechanics per phase (one agent, in its own worktree off `main`):
1. Redeploy the live compose stack from the phase branch (`docker-compose -f infra/docker-compose.yml up -d --build`
   for backend/worker/beat/frontend only; never drop volumes).
2. Run the existing `tests/real_world` scenarios for the area (`scripts/run_real_world.sh`, `RW_ONLY=...`).
3. Add the missing complex/difficult scenarios for the area (real HTTP against the stack, realistic fixtures,
   assert real outcomes: content, counts, citations, statuses, audit rows, timings).
4. Fix every failure at the root (TDD unit/integration test + the live scenario), commit per fix.
5. Report → `docs/audits/fixwave/live/<phase>.md` (scenario verdicts, metrics, fixes, open items).
6. I merge the phase into local `main`, rerun unit/vitest, redeploy, next phase. Nothing is pushed without the user.

| Phase | Areas |
|---|---|
| P0 | Finalize main (USR-1..7), redeploy, full real-world baseline report |
| P1 | Knowledge base, ingestion (all sources), multi-format processing, chunking, embeddings |
| P2 | Retrieval, all RAG patterns/strategies, hallucination/grounding |
| P3 | Scheduling (all schedule types), triggering (all trigger types + channels) |
| P4 | Workflows (all step types), approvals, HITL |
| P5 | Goals, agent core, agent patterns, multi-model routing, agents lifecycle |
| P6 | Agent memories, self-improvement |
| P7 | Evals, golden datasets, rollout gate |
| P8 | Guardrails, governance, grants |
| P9 | Scalability: million-document ingestion/retrieval load, multi-worker |
| P10 | Frontend e2e (Playwright against the live backend) |
| P11 | Full real-world re-run of every scenario + final report |

## Owner priority (2026-10-05)
- **Ingestion (P1), do:** A1 file upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, scanned PDF/PNG OCR, ZIP) ·
  A2 S3, MinIO · A3 PostgreSQL, MySQL · A5 MongoDB, Redis, Elasticsearch · A6 Kafka ·
  A7 Google Drive, SharePoint, Confluence, Notion · A10 HTTP URL, web crawl · A12 agent-generated.
  P1 runs in sub-phases: P1a A1 → P1b A2+A3 → P1c A5+A6 → P1d A7+A10 → P1e A12.
- **Triggering (P3), do:** B1 time (cron, interval, once, relative_delay, deadline, business_calendar) ·
  B2 webhook, rest, event · B3 github, stripe, jira, teams_webhook · B7 platform events
  (goal_completed/goal_failed loop, goal_score_below, hitl_approved/rejected, memory_created) ·
  B8 conversational (chat_command/keyword/mention, email_intent/arrival, sms_inbound, voice_transcript,
  form_submission, meeting_ended, discord_event).
- **Parked by owner:** B9 data/polling, B10 conditional/composite, B11 not-implemented (incl. kafka trigger,
  s3_event, google_sheets, sharepoint, log_pattern, graphql_subscription, websocket_message, price_threshold);
  sources A4, A8, A9, A11; B4–B6; channels C1–C5 unless the owner re-enables them.

## Execution rule (owner, 2026-10-05)
Strictly sequential, one item at a time, in the priority order above. For each item: verify end-to-end on the
live stack first; if it already passes every scenario, mark it **COMPLETE** (evidence: scenario names + run
date) and move on without code changes; otherwise fix at the root, re-run live, then mark COMPLETE.
Per-item status is tracked in `docs/audits/fixwave/live/STATUS.md`.

## Owner instruction (2026-10-05, later)
Complete ALL line items (13-item live sequence + the three MongoDB tracks + every finding routed to them),
fix and verify each e2e, merge every branch/worktree/feature into `main`, run the full suite (unit, frontend,
integration, e2e_full both modes, live real-world), and **then push `main` to origin** (authorized by the owner
for that final push once everything is green).
