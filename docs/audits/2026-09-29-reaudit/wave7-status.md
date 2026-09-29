# Wave 7 status (auto-refreshed)

Last refresh: 2026-09-29 11:28 UTC. See HANDOFF.md section 4 for what each item means.

## fix/hitl-estop-security — 5 commit(s) not yet on main

- `f24fa00ee` fix(hitl): attribute batch, org and MCP approvals to the authenticated caller (53 seconds ago)
- `ed55a2fb5` fix(governance): make the emergency stop real and enforced fleet-wide (9 minutes ago)
- `4c829c81c` fix(coordination): authenticate the group-chat WebSocket with the shared ws_auth (21 minutes ago)
- `683de71bc` fix(hitl): sign email approval links with a real secret, expiry and tenant binding (33 minutes ago)
- `23e47c7d3` fix(hitl): fail closed when the approval wait loses Redis (49 minutes ago)

Worktree `/Users/harsh/Documents/Learning/agent-verse/.claude/worktrees/agent-a9190bfa650bf6037`: 0 tracked file(s) with uncommitted changes.

## fix/audit-correctness-defects — 7 commit(s) not yet on main

- `9e03570d5` wip: worker builds its graph from the persisted runtime profile (7 minutes ago)
- `c15c1276d` test(worker): grant the goal claim in the worker usage-metering test (11 minutes ago)
- `337e78e6f` fix(llm): charge debate/supervisor submission calls; attribute goal-tree spend (14 minutes ago)
- `2d8ea71f7` fix(worker): never re-run a finished goal on Celery redelivery (20 minutes ago)
- `5a6d78ecf` fix(schedules): read and write schedules through the durable store on every request (33 minutes ago)
- `3960746c1` fix(goals): cancel/pause never overwrite a finished goal or report a lost write (40 minutes ago)
- `c2f04e29f` fix(workflow): make approval steps a hard barrier; reject stops the run (48 minutes ago)

Worktree `/Users/harsh/Documents/Learning/agent-verse/.claude/worktrees/agent-adef96f10a67bfa4a`: 2 tracked file(s) with uncommitted changes.

## fix/fake-success-defects — 0 commit(s) not yet on main


Worktree `/Users/harsh/Documents/Learning/agent-verse/.claude/worktrees/agent-a16ecc3b96bc5ae09`: 0 tracked file(s) with uncommitted changes.

## fix/audit-llm-ssrf-ingest — 16 commit(s) not yet on main

- `4af820248` fix(knowledge): bound legacy connector max_* limits (4 minutes ago)
- `a235a0c58` fix(ingestion): Azure Blob downloads are streamed under the document size cap (8 minutes ago)
- `2d6883bc1` fix(ingestion): Excel truncation is raised, marked and recorded (9 minutes ago)
- `9cb62bb9c` fix(embeddings): /embeddings/usage returns only the caller's usage (10 minutes ago)
- `6780d5d65` fix(worker): isolated execution fails closed when the BYOK key cannot be read (12 minutes ago)
- `d45704de3` test(ssrf): fail when an SSRF-checked URL is fetched with an unpinned client (14 minutes ago)
- `2d9d9d074` fix(ssrf): pin SAML test probe and knowledge-ingest tool URL fetch (14 minutes ago)
- `fdc81e6a5` fix(ssrf): pin API-poll and RSS trigger fetches via a sync pinned client (14 minutes ago)
- `6418541c2` fix(ssrf): pin ingestion connector and hosted-reranker fetches to validated IPs (14 minutes ago)
- `65805e8c5` fix(ssrf): pin knowledge URL ingest and gateway file downloads, re-check redirects (14 minutes ago)
- `c623ab44e` fix(ssrf): pin outbound A2A calls and connector test probes to validated IPs (14 minutes ago)
- `f537bbeee` fix(ssrf): pin MCP client, Jira/GitHub servers and OAuth exchange to validated IPs (14 minutes ago)
- `ff36dadf4` fix(evals): GET /eval reads persisted scorecards; bound the per-replica cache (14 minutes ago)
- `e8d4dfe7b` fix(memory): screen and re-embed memories written through the API (19 minutes ago)
- `340f40776` fix(llm): route remaining narrow/indexing LLM calls through complete_decision (19 minutes ago)
- `2d2a0c461` fix(ingestion): RAG_INGEST guardrail fails closed (47 minutes ago)

Worktree `/Users/harsh/Documents/Learning/agent-verse/.claude/worktrees/agent-a3a70de6b38289044`: 0 tracked file(s) with uncommitted changes.

