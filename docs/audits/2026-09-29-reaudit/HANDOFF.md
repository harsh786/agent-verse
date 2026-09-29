# AgentVerse re-audit — handoff for the next session

This file lets any Claude Code session (including one on another account) continue the
production audit/fix loop without the earlier conversation. Everything needed is in this
folder and in git; nothing depends on a session scratchpad.

| File | What it is |
|---|---|
| `HANDOFF.md` (this file) | How to resume, what is in flight, what is pending, how to verify. |
| `open-gaps.md` | Every open gap and new defect, per feature, with current `file:line` (475 gaps + 203 new defects). |
| `certification-matrix.json` | The full 192-feature matrix: status, evidence, fixed gaps, open gaps, new defects, tests. |
| `../../../REAUDIT_SESSION_REPORT.md` | The last published report (pre-dates the 2026-09-29 fresh audit). |

**Status on 2026-09-29** (fresh re-certification of `main` at `a4d172588`):

| | Audit start | Previous matrix | Now |
|---|---:|---:|---:|
| PASS | 12 | 22 | 20 |
| PARTIAL | 65 | 147 | 149 |
| FAIL | 100 | 7 | 9 |
| NOT_IMPLEMENTED | 15 | 16 | 14 |

Nothing is pushed: `main` is ~350 commits ahead of `origin/main`. Do not push, force-push
or rewrite history without the owner's go-ahead.

---

## 1. Paste this to resume

> Continue the AgentVerse production audit and fix loop. Read
> `docs/audits/2026-09-29-reaudit/HANDOFF.md` first and follow it: finish or redo fix
> wave 7 (section 4), then the backlog in section 5 in priority order, then verify
> (section 6) and re-certify (section 7). Work without asking me unless a decision in
> section 8 is needed. Loop: find → reproduce → fix with a failing regression test →
> verify. Never fake success; mark BLOCKED / NOT_IMPLEMENTED honestly. Run at most 4
> sub-agents at a time (each may use at most 1 helper) — more hits the usage limit.

---

## 2. The mandate (original requirement)

Aggressive, evidence-driven production readiness of the AgentVerse monorepo, covering
ingestion + connectors, knowledge bases, embeddings, goals, retrieval, triggers (incl.
Telegram and other external parties), workflows, scheduling, evals, governance, security
and grants — judged at **world-class scale**: millions of documents, multi-replica
deployment, Postgres as the source of truth (no per-process state that must be shared),
proper partitioning/indexing, fail-closed security, no fake success. No false
certification. Deliverables: certification matrix, final report
(PASS / PARTIAL / FAIL / BLOCKED / NOT_IMPLEMENTED), `REAUDIT_SESSION_REPORT.md`, HTML
report published as an Artifact.

---

## 3. Environment (this machine) — read before running anything

- Backend: `cd agent-verse-backend`; Python 3.12 via **`uv run`** (system Python is 3.9).
- Docker via **colima** (`colima start`); standalone `docker-compose` (no v2 plugin).
- Integration / e2e env: `DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock TESTCONTAINERS_RYUK_DISABLED=true`.
- pytest treats warnings as errors.
- Lint/type (must be clean before every commit): `uv run ruff check .` and `uv run mypy app` (strict).
- Alembic must have a **single head** (`uv run alembic heads`). When merging branches that
  each add a migration, re-chain `down_revision` so they form one line. Current head:
  `c8d2f4a6b1e3`.
- The local docker-compose Postgres is behind on migrations; tests that use the default
  `DATABASE_URL` against it can fail on missing columns. Run them against a fresh
  migrated container instead (see section 6).
- `graphify update .` after code changes (repo CLAUDE.md rule).

Test tiers:

```bash
# unit (≈17 min, ~28k tests)
uv run pytest -q --no-cov -p no:cacheprovider -m "not integration and not slow" --ignore=tests/e2e_full --ignore=tests/real_e2e
# e2e under a least-privilege (NOBYPASSRLS) DB role, real Postgres+pgvector+Redis (≈3 min)
env -u NVIDIA_API_KEY -u ONPREM_ENABLED DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock TESTCONTAINERS_RYUK_DISABLED=true E2E_LEAST_PRIVILEGE=1 uv run pytest tests/e2e_full -q --no-cov -p no:cacheprovider
# integration (testcontainers, ≈15 min)
DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -m integration -q --no-cov -p no:cacheprovider --ignore=tests/e2e_full --ignore=tests/real_e2e
```

Real-provider tests (opt-in, `-m slow`, need `REAL_PROVIDERS=1` and provider env vars):
`tests/e2e_full/test_knowledge_real_documents_e2e.py`,
`tests/e2e_full/test_knowledge_multiformat_real_e2e.py` (17 files / 14 formats, a
20-strategy sweep, RAPTOR + agentic chunking). Provider env vars (values are NOT in the
repo — ask the owner; the NVIDIA key must be rotated because it was pasted in chat):
`NVIDIA_API_KEY`, `NVIDIA_MODEL` (use `nvidia/nemotron-3-super-120b-a12b`; `kimi-k3`
returns 404 for the current key), `ONPREM_ENABLED=true`, `ONPREM_QWEN_BASE_URL`,
`ONPREM_QWEN_MODEL`, `ONPREM_EMBEDDING_BASE_URL`, `ONPREM_EMBEDDING_MODEL`,
`ONPREM_EMBEDDING_DIM=1024`, `ONPREM_RERANKER_URL`, `ONPREM_RERANKER_MODEL`. The on-prem
cluster is at 192.168.63.104 (ports 30080–30083) and needs the VPN. For web-augmented RAG
set `SEARXNG_URL=http://localhost:8081` (compose SearXNG). For NVIDIA embeddings:
`KB_E2E_EMBEDDER=nvidia NVIDIA_EMBED_MODEL=nvidia/nemotron-3-embed-1b NVIDIA_EMBED_DIM=2048 ONPREM_ENABLED=false`.

Last known results on `main` (`a4d172588` / just before): unit **28,107 passed, 0
failed**; least-privilege e2e **149 passed, 5 skipped**; `tests/rag tests/api` 5,166
passed; real-provider KB tests passed on the on-prem Qwen models.

---

## 4. In flight: fix wave 7 (started 2026-09-29)

Four sub-agents were started in git worktrees (`.claude/worktrees/agent-*`, branches
`worktree-agent-*`). **Check first whether each finished:**

```bash
git branch --list 'worktree-agent-*'           # candidate branches
git log --oneline main..<branch>               # commits not yet on main
```

For each wave: if its branch has the commits for every item below, merge it
(`git merge --no-edit <branch>`), re-chain migrations to a single head, run
`ruff`/`mypy` and the wave's test directories. If the branch is missing or incomplete,
**redo the missing items** — each item below is a complete task statement.

**All four wave-7 branches are merged to `main` (2026-09-29).** Next: full unit suite + least-privilege e2e on `main`, then re-certify the wave-7 features (section 7) and fix the follow-ups listed per wave below.

### Progress snapshot — 2026-09-29 ~11:10 UTC (weekly usage at 93%)

**Latest state: see `wave7-status.md`** in this folder — refreshed automatically every
10 minutes (commits per wave branch + uncommitted file counts per worktree). Refresh it by
hand with `sh docs/audits/2026-09-29-reaudit/update_wave7_status.sh`. The table below is
the manual snapshot taken when the limit hit 93%.

Branches (in `.claude/worktrees/agent-*`, NOT yet merged to `main`). Commits listed are
done; "uncommitted" means work was in progress in that worktree when this was written —
inspect it with `git -C <worktree> status` / `diff` and finish or discard it.

| Wave | Branch | Done (commit → item) | Still to do |
|---|---|---|---|
| 7A | `fix/hitl-estop-security` | **MERGED to `main`** (all 5 items; 987 targeted tests + agent-reported 10,102 passed) | Follow-ups: `run_goal` start-of-goal e-stop check fails open on a Redis error (per-step check fails closed); email/notification approve links point at a frontend route `/hitl/{id}/approve` that doesn't exist and need an authenticated page; org task approve/reject lacks an org-role check; Slack slash command still uses `SLACK_TENANT_ID`; chat HITL card builds an unverifiable `?token=` link (`app/chat/stream.py`, `ChatHITLCard.tsx`). No Redis ⇒ e-stop endpoints 503 (by design). |
| 7B | `fix/audit-correctness-defects` | **MERGED to `main`** (`c5a602fdf`; all 6 items; agent-reported 5,174 + 4,381 + 661 passed; ruff/mypy clean after merge) | Follow-ups: `app/api/triggers.py` reads schedules non-strict (DB outage → 500 not 503); sync cache-only schedule calls in `app/api/agents.py`, `app/chat/skills/builtin.py`, `app/enterprise/compliance.py`; per-tenant goal counter only incremented at submit; workflow HITL e2e not run (needs Docker). |
| 7C | `fix/fake-success-defects` | **MERGED to `main`** (all 6 items, 11 commits; 445 targeted tests passed, ruff/mypy clean) | Follow-ups: regenerate `openapi.json` after all waves merge (`uv run python scripts/export_openapi.py`); `AgentStore.list_async` falls back to its cache on DB error (export may be incomplete); export pages by offset; video ingest / goal-with-image placeholders; civilization graph/metrics reads swallow errors; marketplace purchase completion not built (501 blocks charges) |
| 7D | `fix/audit-llm-ssrf-ingest` | **MERGED to `main`** (all 5 items, 17 commits; 15,063 tests passed after merge; guard tests `tests/providers/test_no_direct_llm_complete.py` and `tests/net/test_no_unpinned_public_fetch.py` green) | Follow-ups: 91 direct `.complete(` calls remain on the guard test's allowlist (~60 ordinary debt: chat, RAG engine, agent patterns, OCR…; 18 RAG adapters already budgeted); `check_mcp_health` in `app/scaling/tasks.py` still unpinned; MCP WebSocket transport and tenant LLM `base_url` providers resolve DNS themselves; `tests/intelligence/test_eval_runner.py::test_llm_for_accuracy_overrides_heuristic` fails only after `tests/services` (pre-existing order dependence). |

To merge a finished branch: `git merge --no-edit <branch>`, re-chain any new migration's
`down_revision` so `uv run alembic heads` shows one head, then `ruff`/`mypy` and the
wave's tests. The older `worktree-agent-*` branches with commits (marketplace, org twin,
chat sessions, observability) are already on `main` (`git cherry main <branch>` shows no
`+`) — ignore or delete them.

### Wave 7A — HITL / emergency-stop security
1. **[high] Supervised tool approval fails open.** `app/agent/nodes/executor_mixin.py:2315-2330`
   blocks only on REJECTED/TIMED_OUT; `app/governance/hitl.py:541-556, 1102-1108` return
   PENDING immediately when the Redis wait errors → a brief Redis error runs a high-risk
   tool unapproved. Require APPROVED (like `app/agent/tool_gate.py:193`); fail closed on
   Redis errors. Regression test simulating the Redis error.
2. **[high] Forgeable HITL email-approval links.** Default secret
   `changeme-please-set-HITL_EMAIL_SECRET` (`app/integrations/email/approval_sender.py:20`),
   no production refusal, no approver-role check, no expiry (`app/api/governance.py:1514-1547`).
   Refuse the default in production (derive from `VAULT_MASTER_KEY` like
   `app/auth/stream_tokens.py`), sign expiry + approval id + tenant, require still-pending.
   Notification links use `?token=` while the endpoint expects `?sig=` (always 403) — fix.
3. **[high] Emergency stop is fake success.** `app/api/governance.py:1319-1355, 1449`: cancels
   only this replica's in-memory goals, publishes to a channel nobody subscribes to, flag
   TTL 300 s, answers "All running goals cancelled" even without Redis. Make it real:
   durable stop (no TTL until lifted), checked by every replica and the Celery worker at
   submit and each step/wave, cross-replica cancel of running goals, 503 when it cannot be
   enforced, UI banner state from the server. Org e-stop/resume
   (`app/org/router.py:1970-2040`) needs an org-admin/role check.
4. **[medium]** HITL batch/org approvals take the approver from the request body; batch
   sends the wrong action value — use the authenticated principal (`_approver_identity`).
   MCP gateway approve tool calls `hitl.approve` with the wrong signature
   (`app/gateway/mcp_server/__init__.py:~597`). Slack HITL takes the tenant from an env var —
   derive it from the verified Slack binding.
5. **[medium]** Coordination group-chat WebSocket (`app/api/coordination_group_chat.py:51-83`)
   has its own authenticator that skips IP allowlist, scopes and MFA — use the shared
   WebSocket authenticator.

### Wave 7B — workflows / worker correctness
1. **[high] Workflow approval steps don't block** (`app/workflow/compiler.py:186-190, 215-253`):
   with no explicit next step, downstream steps run while approval is pending and reject
   doesn't stop them. Make approval a hard barrier; reject stops/fails the run.
2. **[high] run_goal re-runs finished goals** (`app/scaling/tasks.py:1938-1941, 2051`): no
   terminal-state check before marking executing; Celery redelivery re-runs long goals.
   Terminal check + atomic claim (`UPDATE … WHERE status NOT IN terminal RETURNING`),
   redelivery defers to the goal lock, set `visibility_timeout`/`acks_late` for long goals.
3. **[high] `/schedules` only sees its own process** (`app/api/schedules.py:161` and siblings):
   read/write through the durable `ScheduleStore` (Postgres, RLS) per request.
4. **[high] Worker ignores each goal's runtime profile / pattern flags** (`tasks.py:2661`):
   build the worker graph like `GoalService._make_agent_loop_for_tenant` / `GraphFactory`
   with the persisted profile + execution_context; write `strategy_execution` for worker
   goals; pass the observed profile for scorecards; record honest downgrades.
5. **[medium]** Debate/supervisor workflow modes run LLM calls in the HTTP request,
   uncharged, before the budget check (`app/api/goals.py:329-365`) — route through
   `app.providers.guarded_completion`; goal-tree sub-goals use an empty goal id
   (`app/agent/goal_tree.py:86-90`).
6. **[medium]** Cancel/pause can overwrite a completed goal and swallow a failed status
   write — conditional transitions + 503. Worker does not apply the per-tenant
   concurrency limit.

### Wave 7C — fake-success API surfaces
1. **[high] Insights API** queries nonexistent `goals.cost_usd`, `duration_s`, `embedding`,
   swallows errors, returns made-up defaults (0.82 / 0.7). Rewrite against the real schema
   under RLS, 503 on error, honest nulls; test that referenced columns exist in the ORM.
2. **[high] Golden datasets** return made-up ids / "promoted" while saving nothing; promote
   accepts another tenant's goal. Implement persistence (RLS table, tenant-checked promote)
   or honest 501 + remove frontend fake success.
3. **[high] Platform admin** `/admin/usage` reads nonexistent `GoalService._active_goals`
   (always 0); `/admin/incidents` reads nonexistent `_incidents` (always empty). Real
   sources via the maintenance session, or 501.
4. **[high] Marketplace monetization** creates a real Stripe PaymentIntent but nothing marks
   purchases paid, installs aren't gated, authors aren't paid. Honest 501 on purchase until
   completion is built (or implement fully). Stripe billing webhook must also handle
   `customer.subscription.updated` / `invoice.payment_failed` (past-due keeps paid plan).
5. **[medium]** Multimodal marks failed/text-free PDFs completed with placeholder text;
   Google Drive folder ingest reports success when all files failed; perception batch
   analyse returns "No vision provider configured." as success
   (`app/perception/page_analyzer.py:57-61`).
6. **[medium]** GDPR tenant export caps at 50 goals (`app/api/tenants.py:1129`); civilization
   list/members turn DB errors into empty lists.

### Wave 7D — LLM metering, ingest guardrail, DNS pinning
1. **[high] Unmetered LLM calls** — route through `complete_decision`
   (`app/providers/guarded_completion.py`): `app/rag/indexing.py:~425` (RAPTOR/agentic
   chunking; scales with documents), `app/knowledge_graph/extractor.py:92,154`,
   `app/workflow/steps/llm_step.py:101`, `app/triggers/nl_scheduler.py:482`,
   `app/orchestration/goal_classifier.py:325`, `app/org/*`, memory consolidation, tool
   intelligence, RAFT inference, and `app/rag/gateway.py:~297` strategy calls (already
   budgeted → breaker+timeout only, `charge=False`). Add an AST guard test that fails on
   new direct `provider.complete(` calls outside an allowlist.
2. **[high] RAG_INGEST guardrail fails open** (`app/ingestion/pipeline.py:526-531`) and
   ingestion indexes unscreened if rules fail to load — fail closed.
3. **[medium] DNS-rebinding window** at ~20 sites that validate then connect with a plain
   `httpx` client: `app/mcp/client.py:478,785,866,1058`, connector test probes, OAuth code
   exchange, outbound A2A call tool, knowledge URL ingest, gateway file download → use
   `public_async_client` / `request_public`. Add a guard test for the pattern.
4. **[medium]** `PATCH /memory/{id}` skips the memory-write guardrail and re-embedding
   (`app/api/memory.py:397-431`); GET `/eval` never reads persisted `eval_scorecards` and the
   per-replica `_eval_scores` cache is unbounded (`goal_service.py:~566, ~4536`); worker BYOK
   isolated path swallows tenant key errors (`tasks.py:2960-2966`).
5. **[medium]** Ingestion bounds: legacy connector `max_*` unbounded; Azure Blob downloads
   whole blobs; Excel silently truncates at 5,000 rows / 20 sheets; `/embeddings/usage` is
   process-global across tenants.

---

## 5. Backlog beyond wave 7 (priority order)

Full detail with `file:line` is in `open-gaps.md`; these are the items that matter most.

**P0 — security / wrong results**
- Agent-scoped API keys are issued and shown but authenticate nothing
  (`app/auth/agent_credentials.py:38,103` have no caller in the auth path); the credentials
  page reads fields the API doesn't return (`AgentCredentialsPage.tsx:42-48,185`). (FAIL)
- Custom RBAC tables (`user_roles`, `role_assignments`, `api_key_scopes`, `custom_roles`) are
  never written by the API and only partly read.
- OAuth token-save error swallowed while the callback reports "connected"
  (`app/mcp/oauth.py:~570`).
- A2A execution is fire-and-forget: a restart leaves tasks "accepted" forever (`app/api/a2a.py:~397`).
- Liveness probe points at `/health`, which 503s during a DB outage → all API pods restart
  (k8s/Helm manifests). Add a separate liveness endpoint.
- High-risk grounding check fails a goal only when tool evidence exists; ungrounded
  text-only answers complete.
- Bare Stripe/Google/GitLab secrets pass event sanitization.

**P1 — multi-replica / Postgres as source of truth**
- Per-process state that must be shared: GET `/memory/long-term` and `/memory/execution`,
  department memory, suggestions, calibration feedback, skill enable/disable
  (`app/skills_runtime/executor.py:90-123`), guardrail violations, chat search/folders/usage,
  voice consent, proactive-outreach daily cap, file workspace (`/tmp`), connector
  registry + secrets (Redis-only).
- Goal dedup check-then-register is not atomic (`goal_service.py:~3436-3476`).
- The first goal in a fresh worker ignores tenant routing policies (store wired after read).
- Self-optimizer v2 winners reach API replicas only after restart (AgentStore cache).
- Worker logs never reach `/observability/logs`; worker traces never record their result.
- Collab sync: every 50th update saved as the "full snapshot"; CRDT relay on a
  text-decoding Redis client breaks binary sync across replicas (`collab.py:821`).
- Celery workers keep LangGraph checkpoints only in memory (retry restarts from scratch).

**P1 — scale**
- Memory-records vector recall: one HNSW index across tenants with post-filtering; no
  trigram index for the keyword fallback (migration `c8d2f4a6b1e3`, `postgres_repository.py:87-103`).
- Strategy evidence table: no retention; catalogue reads one 5,000-row query across all strategies.
- Connector health: 30 s schedule probes up to 5,000 connectors serially; snapshots never pruned.
- Agent router: one DB query per agent over an unbounded list.
- SSO: every request scans the in-memory tenant list twice (`tenant_service.py:745,782`).
- Deleting a collection/document leaves knowledge-graph entries and cached answers (no
  `knowledge.updated` subscriber).

**P2 — not implemented / partial features (keep honest)**
- SAML and Google SSO verify identity but issue no session (honest 501); SCIM has only /Users.
- RAFT: OpenAI is the only fine-tune provider; RAFT inference not charged/circuit-broken.
- Reflexion: lessons are deterministic; isolated execution and the API `multi_agent`
  workflow path don't learn.
- ColBERT needs its optional library; prospective memory and the A/B testing engine are inert.
- MFA enforcement is off by default (see decisions).

---

## 6. Verification checklist (after each merge)

1. `uv run ruff check .` and `uv run mypy app` clean; `uv run alembic heads` single head.
2. Tests for touched directories, then the full unit suite (section 3).
3. Least-privilege e2e tier (section 3). It catches RLS mistakes the unit tests can't.
4. Integration tier for any new migration/RLS table. For tests that read the default
   `DATABASE_URL`, run them against a fresh migrated container:
   ```python
   # run_on_fresh_db.py — pytest args against a fresh migrated pgvector container
   import os, subprocess, sys
   from testcontainers.postgres import PostgresContainer
   with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
       env = {**os.environ, "DATABASE_URL": pg.get_connection_url()}
       subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
       sys.exit(subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], env=env).returncode)
   ```
5. Knowledge/RAG changes: the real-provider KB tests (section 3).
6. Regression guards that must stay green: `tests/core/test_lifespan_singleton_isolation.py`
   (lifespan-wired singletons restored between tests), `tests/frontend/test_api_contract.py`
   (frontend calls match backend routes), `tests/e2e_full/test_security_sweep_e2e.py`.

Known traps from this audit: a test that runs the app lifespan with a fake DB factory
leaks it into module singletons (fixed by `tests/_lifespan_singletons.py` — add new
singletons there); `ENVIRONMENT` must not be popped without monkeypatch; the circuit
breaker is process-global (give fake providers unique `_default_model` values).

---

## 7. Re-certification and report

- Re-certify changed features read-only against current `main` using the rubric: **PASS**
  (works end to end, no gap causing wrong results / fake success / security exposure /
  multi-replica incorrectness, tested), **PARTIAL**, **FAIL** (core broken, fake success or
  security hole), **NOT_IMPLEMENTED** (absent / honest 501), **BLOCKED** (can't verify —
  say what's missing). Update `certification-matrix.json` (keep `baseline_status`) and
  regenerate `open-gaps.md`.
- Update `REAUDIT_SESSION_REPORT.md` and republish the HTML report (Artifact titled
  "AgentVerse readiness audit") from the matrix.

---

## 8. Decisions only the owner can make

- Exempt machine credentials (agent RS256 JWTs) from MFA enforcement? Today they are
  rejected on tenants that enforce MFA.
- Turn MFA enforcement on by default (`mfa_enforcement_enabled=False` today)?
- Make RAFT deploy/submit endpoints admin-only?
- Keep paid marketplace templates disabled (honest 501) until purchase completion,
  install gating and author payouts exist?
- Rotate the NVIDIA API key (it was pasted in chat); push `main` when ready.
