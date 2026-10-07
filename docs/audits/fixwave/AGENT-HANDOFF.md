# Agent handoff — AgentVerse fix / live-e2e program

> Resume prompt for a new agent:
> "Read `docs/audits/fixwave/AGENT-HANDOFF.md` and continue from **§4 In flight** then **§5 Queue**,
> following **§3 Procedure** exactly. Do not redo anything in **§2 Done**."

Last updated: **2026-10-08** — see **§00** first, then §0 (both supersede §4/§5 below; older sections kept
as history). `origin/main` = `d2b89e360` at time of writing.

---

## 00. 2026-10-08 update (READ FIRST)

### 00.1 Merged and pushed to main (do not redo)
| Commit | What |
|---|---|
| `d4ae1f61d` | Model Registry UI complete: thinking control, real per-capability Test connection, `GET /models/resolution` + Resolved models panel, accessible ordering, polish |
| `41b717e57` | Every reasoning call resolves from the Model Registry (`resolve_reasoning` + central hook for `""`/`"default"`; router/orchestrator literal profiles removed; Strategy C never picks an unconfigured model; guard test) |
| `06d83f25a` | Speech-to-text / text-to-speech from the registry (`resolve_stt`/`resolve_tts`, seeding, real probes, `/voice/status` truthful; no silent whisper-1/tts-1) |
| `15f3a2a66` | Every embedding side path uses the registry embedder (workflow RAG step, content-type routing, on-prem/env/BYOK providers, memory, seeder; dead embedder roles removed; literal guard) |
| `d2b89e360` | Final branch sweep: API never wires the Redis-only connector secret store (SECRET-01); restored tool-gate integration test; test fixture resets a cached Settings whose ENVIRONMENT a test changed (order-dependent failures) |

Branch sweep (2026-10-08): all 68 branches not ancestors of main were checked. 64 have ≥97% of their added lines
on main (the rest was rewritten later); 3 are superseded by newer designs (prospective-intention auth, golden-task
dataset versions); 1 is an obsolete merge-heads migration. Nothing else is missing. Their deletion needs the owner's OK.

Tests at `d2b89e360`: frontend typecheck clean, lint 0 errors, vitest 5,374/5,374. Backend full unit suite on the
previous build: 2 failures of 36,068 (code-step sandbox, order-dependent), both fixed in `d2b89e360`; a
confirmation run on `d2b89e360` was in progress at time of writing.

### 00.2 World-class E2E program — consolidated status (single source of truth)
Owner decisions in effect: redeploy (#6) and live re-runs (#7) are PARKED by the owner; nothing may be mocked in
the live suites.

**A. Done and live-verified**
- A1 file upload (9/10, table ranking fixed later), A2 S3/MinIO, A3 PostgreSQL/MySQL, A5 MongoDB/Redis/Elasticsearch
  (33/33), A10 web crawl (11/11), A12 agent-generated knowledge (5/5).
- B1 time triggers (12/12), B2 webhook/REST/event triggers (10/10 + multi-replica 12/12), B7 platform events (9/9).
- New no-mock live suites (`tests/real_world`): MongoDB pipeline + failures **14/14**, OCR **6/6**, on-prem Model
  Registry **6 pass / 3 env-skips / 1 fail** (supervisor routing — fixed in `26e01b9b5`, not yet re-run).

**B. Blocked on the redeploy (owner-parked #6/#7)**
1. Baseline live suite re-run (last: 59 pass / 17 fail / 6 skipped on the PRE-fix build).
2. On-prem Model Registry suite re-run (includes the supervisor fix).
3. MongoDB + OCR suite regression on the new build.
4. Live OCR parallelism check (multi-page + concurrent documents).
5. Live re-checks P2 retrieval, P4 workflows/HITL, P5 agent core, P7 evals, P8 guardrails/grants (code pushed).
6. **Chaos** (`RW_CHAOS=1`): kill worker / Redis / Postgres / Mongo mid-run; assert retries and recovery.
7. **Scale, P9** (`RW_SCALE=1`): million-document ingestion + retrieval, multi-worker, on-prem embedder.
8. **Frontend E2E, P10**: Playwright against the live backend.
9. **Full rerun, P11** + final report.

Redeploy recipe: build ALL images **including db-migrate and code-sandbox** BEFORE running db-migrate; start only
core services (`up -d --no-deps …`); restart launchd
(`launchctl kickstart -k gui/$(id -u)/com.local.agentverse.runforever`). Run suites ONE at a time.

**C. Not started and NOT blocked by the redeploy**
10. P6 memories & self-improvement — live E2E.
11. A7 Google Drive / SharePoint / Confluence / Notion connectors — code not done.
12. B8 conversational triggers — code not done.
13. A6 Kafka — code done (DEF-4), live check needs a Kafka container.
14. B3 GitHub / Stripe / Jira / Teams webhooks — code done (DEF-5), live check pending.

**D. Parked by the owner**
C1–C5 channels (Telegram/WhatsApp/Slack/Teams/generic webhook), org-collab, B9–B11, architecture docs 1–32/39
(brief: `docs/world-class/_BRIEF.md`), the five capstone scenarios, the 149 out-of-scope backlog items.

### 00.3 Code-level test status (2026-10-08, main `497875650`)
- Backend unit (full, not integration/slow): **36,068 passed, 0 failed**.
- Integration (testcontainers): 1,099 passed / 7 failed → all 7 fixed in `53f6e56ec` (tests violated the
  duplicate-source rule; cross-encoder fallback test raced the model warm-up). Not yet re-run as a full tier.
- `tests/e2e_full` (normal): 217 passed / 2 failed → cost-breakdown expectation fixed in `497875650`.
  **Open:** `test_org_any_task_e2e.py::test_arbitrary_mission_executes_forms_team_completes_and_emits_events`
  — the org mission goal ends `failed` (log shows `team_formation.llm_extract_failed`: the role model's reply is
  not JSON). Org-collab is owner-PARKED; investigate when it is un-parked (likely the registry-only reasoning
  change: the e2e harness's fake model output for team formation).
- **Not run yet:** `E2E_LEAST_PRIVILEGE=1 uv run pytest tests/e2e_full`; a full integration re-run.
- Frontend: typecheck clean, lint 0 errors, vitest 5,374/5,374.

### 00.4 Owner actions (cluster; real keys — an agent must not do these)
- ONE `VAULT_MASTER_KEY` on every pod (`--set-string secrets.vaultMasterKey=…`, keep
  `secrets.vaultPreviousMasterKeys=dev-insecure-master-key`), verify with a sha256 prefix per pod, then re-save the
  S3 / Gmail / model API credentials saved while pods disagreed.
- Register models in the Model Registry: embedder (NVIDIA `nvidia/nemotron-3-embed-1b` 2048-d, or on-prem
  Qwen3-Embedding-0.6B 1024-d per collection), reasoning (Qwen3.5-4B with `thinking: off`), OCR, vision, reranker,
  optionally speech. Use Test connection and the Resolved models panel to confirm.

### 00.5 Needs the owner's OK
Delete the old branches/agent worktrees (content verified on main), the `wc-probe` tenant and the
`agentverse-rw-mongo-broken` container; whether to commit the generated test-report files and the local-only
`playwright.local-novideo.config.ts` (left uncommitted on purpose).

---

## 0. 2026-10-07 update (READ FIRST — supersedes §4 and §5)

### 0.1 Merged and pushed to main today (do not redo)
Batches: batch 3 `46ba316a1` (governance/core/tools/services-B/critic-2/restorer), fork-2 full-suite fixes
`3e3ae9f27`, batch 4 `d8bfb6c7e` (critic-1/3/4/5, services-A, decisions: subgoals/email/cleanup/ssrf/
prompt-injection/autonomy, frontend waiting_children). Final full backend suite on d95fdbea2: **36,665 passed,
2 load flakes** (both made load-tolerant in `e365edee8`). Frontend 5,311/5,311.

Live-E2E driven fixes (all on main):
| Commit | Fix |
|---|---|
| `32be0339f` | access-log redaction kept %-args (uvicorn crash per line); 429 Retry-After delta-seconds; compose backend start_period 150s |
| `e8b4fc063` | NEW live suites (no mocks): MongoDB pipeline + failures, chaos, scale, on-prem Model Registry, OCR (tests/real_world) |
| `786f36fff` / `0b7a984c8` | OCR honest 422/502 + one 25 MiB cap; prod nginx client_max_body_size 64m |
| `3f154b4b2` | Redis blip no longer 500s every authenticated request (permission cache) |
| `c9b5f21cf` | DB outage → retryable 503 (never 500 / empty 200) |
| `a36df92a1` | registry embedding model with its own base_url/key used everywhere; dimension guard |
| `d95fdbea2` | orphaned ingestion sync detected + resumed in ~6 min (heartbeat lease, fencing, 3 attempts) |
| `8ae1de0df` | webhook replay refusal audited; voice model loading off the event loop |
| `8f078be27` | thinking-model support (registry `thinking` auto/off/on; Qwen3.5-4B works with `"thinking":"off"`) |
| `84262a07d` | failover provenance (fallback_from), no complete-with-empty-answer, registry never shows a refused embedder as selected |
| `9a9678c1e` | `kill -USR2 <pid>` dumps all thread stacks (faulthandler) |
| `102e537ad` / `64512f79d` | workflow HITL timeout actions (escalate/auto_approve/auto_reject/pause) + 60 s indexed sweep; builder timeout options match DSL |
| `377609e6c` / `a0dfc3cf5` | bounded cross-encoder + ColBERT reranking with a 2.5 s budget (root cause of KB search 503 "strategy deadline exceeded": CPU/GIL starvation) |
| `08b7a8709` | MongoDB poison docs: per-document decode isolation → DLQ, odd BSON rendered, operator retry |
| `649ac4da9` | S3: tenant AWS clients never use ambient creds / IMDS (worker blanked undecryptable secrets → default chain → 169.254.169.254); Helm embedding settings + AWS_EC2_METADATA_DISABLED |
| `587e615f8` | RAG grounding: citation context budget, key-path vs marker parsing, structured/multilingual judge prompt, real refusal reason |
| `0d8541d07` | workflow tool-step retry classification (non-retryable stop), skipped steps replayed not re-run, errors/attempts recorded |
| `ac555e4b5` | duplicate source registration refused (409, canonical target + unique index); search collapses duplicate docs |
| `5f4da8d28` | no SIGTERM handler at import time (only Celery worker processes) |
| `26248d881` / `ab425a0ed` | live-test drift fixes (connect-failure collections; MCP gate approval) |
| `eb6c8e95b` | Model Registry dialog: write-only API key field, key badges, output_dimensions for embeddings |
| `26e01b9b5` | every agent/runtime LLM role resolves through role_preference (supervisor leaked to cloud); simple goals no longer fan out |
| `fed6c29d9` | workflow worker connector secrets + OAuth manager; undecryptable secret names VAULT_MASTER_KEY |
| `0dbca85aa` | workflow code steps run in a hardened code-sandbox runner service (compose + both Helm charts) |
| `66f27dab1` | goals/workflows use Model Registry models, never a silent FakeProvider; builder lists registry models; truthful registry status |

Live results on the rebuilt stack: **MongoDB pipeline + failures 14/14**, **OCR 6/6**, on-prem registry 6 pass /
3 env-skips (gemma served without chat template; one global EMBEDDING_DIM) / 1 fail (supervisor routing, fixed in
`26e01b9b5`, re-run pending).

### 0.2 In flight (branches/worktrees under .claude/worktrees/)
- `fix/reasoning-registry-everywhere` (fx-reasoning-all): resolve_reasoning + central hook for model ""/"default";
  RAG/chat/org/guardrails/evals/self-optimizer/NL triggers/KG/memory; remove router literal profiles; guard test.
- `fix/vision-ocr-registry` (fx-vision-ocr): resolve_vision/resolve_ocr, no reasoning fallback, seeder merge bug,
  browser_agent/pipeline/vision_parser via dispatch, general.py "default" id.
- `fix/rerank-registry` (fx-rerank-registry): registry reranker as default tier, configurable cross-encoder,
  ColBERT checkpoint, seed on-prem reranker.
- `feat/per-collection-embedders` (fx-percoll-embed): each KB collection bound to its own embedder/dimension.
- Baseline live suite (tests/real_world minus the new suites) running; 35 pass / 8 fail so far on the PRE-fix
  deployment (goal strategies, approvals, budget audit, KB re-embed/hard retrieval) — re-run after redeploy.
Audit driving the model work: `docs/audits/fixwave/model-registry-audit-2026-10-07.md`.

### 0.3 Queue (strict order)
1. Speech STT/TTS through the registry. 2. Embedding side paths (after per-collection embedders): llm_step
embedding with the chat provider, content-type embed literals, provider `.embed`, BYOK embed. 3. Redeploy: build
ALL images **including db-migrate** (and code-sandbox) BEFORE running db-migrate; restart launchd
(`launchctl kickstart -k gui/$(id -u)/com.local.agentverse.runforever`). 4. Re-run baseline failures, on-prem suite,
MongoDB + OCR regression. 5. Chaos (`RW_CHAOS=1`). 6. Scale (`RW_SCALE=1`, on-prem embedder). 7. Zero-failure sweep
(full backend, e2e_full normal + E2E_LEAST_PRIVILEGE=1, frontend vitest, Playwright). 8. Report + this doc.
9. Cleanup old branches/worktrees + `wc-probe` tenant — ASK the owner first.

### 0.4 Owner actions on the Kubernetes cluster (cannot be done by an agent: real keys / cluster access)
- ONE `VAULT_MASTER_KEY` on every pod (root cause of the S3 IMDS error and the Gmail "could not resolve credential").
- Deploy the new image; embedder via Helm (`secrets.nvidiaApiKey`, `embedding.nvidiaEmbedModel`, dims 2048) or the
  Model Registry (provider nvidia, base URL https://integrate.api.nvidia.com/v1, key, Test connection).
- `secrets.codeSandboxToken` + code-sandbox image. Commands: docs/ops/k8s-redeploy-checklist.md §4-5,
  docs/ops/code-sandbox.md. Gemini embeddings: model `gemini-embedding-001` (not gemini-2.5-flash), output dims = index.

### 0.5 Live E2E environment (this machine)
- Runner env: `/private/tmp/claude-501/rw/wc/env.sh` (tenants primary=enterprise, second=free, approver key;
  infra creds from earlier runs' infra.env files; chaos container names; on-prem URLs). Keys live only in 0600
  json files; never print them. `PLATFORM_ADMIN_TENANT_IDS` added to agent-verse-backend/.env (backup in rw/wc).
- On-prem cluster (vLLM): Qwen/Qwen3.5-4B :30080 (thinking model → `thinking: off`), gemma-4-E2B :30081 (no chat
  template), Qwen3-Embedding-0.6B :30082 (1024-d), Qwen3-Reranker-0.6B :30083 — all at http://192.168.63.104.
- Run suites ONE at a time; concurrent suites interfere (shared fixtures/cleanup) and overload the colima VM.
- Langfuse/ClickHouse are optional extras: start only the core services (`up -d --no-deps <svc…>`).
- MongoDB fixture `agentverse-rw-mongo` was recreated (old one kept as `agentverse-rw-mongo-broken`) because its
  start command could not rewrite its own key file after a crash.
- Lessons: build db-migrate before migrating (a migration importing new app code failed with a stale image);
  testcontainers left behind accumulate (Ryuk disabled) — remove ones >12 h old; a hot backend can be diagnosed
  with `docker kill -s USR2 agentverse-backend-backend-1` + `docker logs`.

### 0.6 Parked by the owner
Architecture docs 1–32/39 (brief in branch docs/world-class), the five capstone scenarios, the 149 out-of-scope
backlog items, org-collab and channels.

---

## 1. Mandate (owner)

- Fix everything **end to end**, verified on the **live local Docker stack** with real-world, complex use cases.
- Work **sequentially**, one live item at a time (the live stack is shared).
- After **every** completion: merge into `main` → commit → full test suite green → secret-scan gate → **push to origin**.
- Owner rule per item: verify live first; if it already passes mark **COMPLETE (already)**; otherwise fix at the
  root (TDD), re-verify live, mark **COMPLETE (fixed: commits)** or **OPEN (reason)**.
- Everything (branches, worktrees, fixes, features) must end up merged in `main` and pushed.

Key docs: `docs/audits/fixwave/LIVE-E2E-PLAN.md` (plan, owner priority, re-ordering, owner decisions 1–7),
`docs/audits/fixwave/live/STATUS.md` (per-item tracker), `docs/audits/fixwave/BRIEF.md` (rules for fix agents),
`docs/audits/fixwave/live/*.md` (per-phase reports), `docs/audits/fixwave/progress/*.json` (per-package progress +
`_new_findings`), `docs/audits/fixwave/pending-all-2026-10-05.{md,json}` (backlog of 355 medium/low items re-verified
at 3b5d35c13 — many fixed since; re-check before working one), repo-root `CLAUDE.md` (env quirks).

## 2. Done and pushed (do not redo)

Live sequence (STATUS.md items):

| # | Item | Result |
|---|---|---|
| 1 | A1 File upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, OCR, ZIP) | COMPLETE 9/10 (PDF table ranking fixed later in P2) |
| 2 | A2 S3 / MinIO | COMPLETE |
| 3 | A3 PostgreSQL / MySQL | COMPLETE |
| 4 | A5 MongoDB (ingestion + MCP) / Redis / Elasticsearch | COMPLETE (33/33 live) |
| 5 | A10 HTTP URL / web crawl | COMPLETE (11/11 live) |
| 6 | A12 Agent-generated knowledge | COMPLETE (memory consolidations → P6) |
| 7 | B1 Time triggers (cron, interval, once, relative_delay, deadline, business_calendar) | COMPLETE, pushed |
| 8 | B2 Webhook / REST / event triggers | COMPLETE (fixed B2-1..B2-9; INGRESS-* 10/10 live, multi-replica 1/1) |

Fix batches (all merged + pushed): user's 7 ingestion items (USR-1..7), MongoDB audit (49 items + NF-1..5, MCP,
ingestion, frontend), owner decisions D1–D5, BYOK (vault key on every workload, workflows use tenant BYOK, no fake LLM
outside dev/test, `LLM_REQUIRE_PLATFORM_KEY`), P5 agent-core (429 retry/scoped breakers, supervisor/debate on v2, MoA
hang, expired approvals, risk false positives, grounding), findings NF-10..18 (+ pgbouncer app role, wait-for-schema
init), P2 retrieval code (FTS long queries, expansion, hyphenated ids, top_k 422, URL boilerplate, honest readiness,
agentic citations, strategy picker), P4/P7/P8 code (workflow create audit, audit column widths, golden-run deadlock,
eval answer field, dataset versions, worker guardrail rules, call-time grant deny), P8b (output PII screening on every
surface, workflow step guardrails, violations endpoint, 236 id columns → 64), open items OI-1..5 (approved action runs
once, workflow tool-step risk gate, log redaction, Redis host-independent ids, reranker), GRD-1 (structured tool
results ground answers), chat transcripts opt-in (decision 7), CHAT-SEC-1..3 (owner-scoped chats + RLS, real message
delete, chat TTL), CHAT-D-1..3 (folders in PG, saved feedback, GOAL turns use session agent), run_goal heartbeat leak,
collab presence dead-peer pruning, Stripe-literal guard test, EGRESS-CFG (operator egress allowlist in .env.example /
both Helm charts / raw k8s / prod compose), B7-1..5 platform-event trigger code (loop guard, goal_failed publishers,
score_below threshold+dimension, workflow HITL events, feedback memory.created), DEF-1..5 deferred code (Teams JWT
binding, Slack workspace secrets, self-service channel bindings, Kafka commit-after-index, SaaS webhook signatures),
suite fixes (Teams test timestamp stamped at run time; Slack button test stubs the async signature check).

## 3. Procedure (follow exactly)

### 3.1 Per item / fix package
1. Create a worktree from `main`: `git worktree add .claude/worktrees/<name> -b <branch> main`
   (the Agent tool's `isolation: worktree` has been unreliable — create worktrees manually).
2. Delegate to one agent with a prompt that points at BRIEF.md, the plan, STATUS.md, the previous phase report, and
   the specific scope. Rules to always include: never push / never touch main / **never use `git stash`** (shared
   across worktrees — use WIP commits) / **never commit provider-key-shaped literals** (build fakes from split parts,
   e.g. `"sk_" "live_..."`) / timeouts on every wait / testcontainers one file at a time / `git merge main` before
   finishing and keep ONE alembic head.
3. Live phases additionally: redeploy the app services from the item's worktree first, run a short regression of
   earlier scenarios, verify live first, add scenarios to `agent-verse-backend/tests/real_world`, write
   `docs/audits/fixwave/live/<phase>.md`, update STATUS.md.
4. Keep ≤3 agents at once and only ONE on the live stack (machine load has caused stalls; resume a stalled agent with
   SendMessage telling it to WIP-commit first).

### 3.2 Merge → test → push (after each completion)
```bash
cd /Users/harsh/Documents/Learning/agent-verse
git merge --ff-only <branch>            # or: git merge --no-ff <branch> -m "merge: ... Co-Authored-By..."
cd agent-verse-backend && uv run alembic heads   # must be ONE head; else `uv run alembic merge -m "..." <h1> <h2>`
                                                 # (pass head ids explicitly; never run `ruff format` with an empty path)
uv run ruff check . -q && uv run mypy app
```
Full suite (~25–35 min) in the dedicated worktree `.claude/worktrees/verify`:
```bash
cd .claude/worktrees/verify && git checkout -q --detach main && cd agent-verse-backend && uv sync -q
O=/private/tmp/claude-501/vsuite; rm -f $O/out0* $O/fe_tsc.txt $O/fe_vitest.txt $O/DONE
find tests -name 'test_*.py' | sort > $O/all.txt
python3 -c "l=open('$O/all.txt').read().split();[open('$O/chunk0%d'%k,'w').write('\n'.join(l[k::4])) for k in range(4)]"
nohup $O/run.sh > $O/run.log 2>&1 &      # run.sh: 4 parallel pytest chunks (-o faulthandler_timeout=300) + tsc + vitest, touches $O/DONE
```
`run.sh` content (recreate if /private/tmp was wiped):
```zsh
#!/bin/zsh
O=/private/tmp/claude-501/vsuite
cd /Users/harsh/Documents/Learning/agent-verse/.claude/worktrees/verify/agent-verse-backend
for k in 00 01 02 03; do
  (DATABASE_URL="postgresql+asyncpg://nouser:nopass@127.0.0.1:1/none" REDIS_URL="redis://127.0.0.1:1/0" uv run pytest -q -p no:cacheprovider --no-cov -o faulthandler_timeout=300 -m "not integration and not slow and not real_openai" $(cat $O/chunk$k) > $O/out$k.txt 2>&1; echo "EXIT $?" >> $O/out$k.txt) &
done
wait
cd ../agent-verse-frontend && (npm run typecheck > $O/fe_tsc.txt 2>&1; echo "EXIT $?" >> $O/fe_tsc.txt); (npx vitest run > $O/fe_vitest.txt 2>&1; echo "EXIT $?" >> $O/fe_vitest.txt)
touch $O/DONE
```
(The verify worktree needs `agent-verse-frontend/node_modules` symlinked from the main checkout.)

Failure handling: a test failing only in suite order → find the leaking test / state and fix the root cause (recent
examples: heartbeat thread leak, presence registry leak, boto3 submodule leak). A hang → read the faulthandler dump
(`grep -n "Timeout (0:" out0X.txt`). `tests/e2e/test_smoke.py` hits the live server on :8000 and can fail while a live
agent redeploys — rerun it. Behaviour changes made on purpose → update the old test's assertion and say why.

### 3.3 Secret-scan gate (push ONLY if clean)
1. `uv run pytest tests/security/test_no_provider_key_literals.py` must pass.
2. Scan `git log -p origin/main..main` added lines for provider-key patterns (Stripe `[srp]k_(live|test)_`, Slack
   `xox[abpr]-`, GitHub `gh[pousr]_`, `AIza`, `glpat-`, `AKIA` (except AWS's documented `AKIAIOSFODNN7EXAMPLE`),
   `nvapi-`) and for any PASS/SECRET/KEY/TOKEN value from `/private/tmp/claude-501/rw/**/*.env|*.json`
   (paths starting with `/` are not secrets; values already present on `origin/main` — e.g. the MinIO dev default
   `minioadmin` in docker-compose — are not a new disclosure: skip them with `git grep -q -F <v> origin/main`).
3. Only then `git push origin main`. GitHub push protection once rejected a fake `sk_live_...` test literal; if that
   happens, rewrite the unpushed range (filter-branch tree-filter to split the literal) — never use the bypass URL.

## 3a. Task-wise status (owner's list) — 2026-10-06

| # | Task | Code | Live verified | Status |
|---|---|---|---|---|
| A1 | File upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, OCR, ZIP) | ✅ | ✅ 9/10 (+table ranking fixed in P2) | DONE |
| — | OCR parallelism (owner request 2026-10-06) | ✅ pushed | benchmark only (~5x on 20-page scan) | DONE in code; live check queued |
| A2 | S3 / MinIO | ✅ | ✅ | DONE |
| A3 | PostgreSQL / MySQL | ✅ | ✅ | DONE |
| A5 | MongoDB / Redis / Elasticsearch | ✅ | ✅ 33/33 | DONE |
| A10 | HTTP URL / web crawl | ✅ | ✅ 11/11 | DONE |
| A12 | Agent-generated knowledge | ✅ | ✅ 5/5 | DONE (memory consolidations → P6) |
| B1 | Time triggers | ✅ | ✅ 12/12 | DONE |
| B2 | Webhook / REST / event triggers | ✅ | ✅ 10/10 + multi-replica 12/12 | DONE |
| B7 | Platform events (goal completed/failed, score below, HITL approved/rejected, memory created) | ✅ B7-1..5 + B7-L1..L4 | ✅ 9/9 + INGRESS/TIME regression | DONE (`b7495f911`) |
| B3 | GitHub / Stripe / Jira / Teams webhooks | ✅ DEF-5 | ❌ | code done; live check deferred (item 11) |
| C1–C5 | Telegram / WhatsApp / Slack / Teams / generic-webhook channels | ✅ DEF-1..3 | ❌ | code done; live check deferred (item 13) |
| A6 | Kafka | ✅ DEF-4 (commit after index, real Kafka container test) | ❌ | code done; live check deferred (item 14) |
| A7 | Google Drive / SharePoint / Confluence / Notion | ❌ | ❌ | NOT STARTED (deferred, item 10) |
| B8 | Conversational triggers | ❌ | ❌ | NOT STARTED (deferred, item 12) |
| B9–B11 | — | — | — | PARKED by owner |
| — | GDPR export gaps from orphaned worktree (RV-08) | ✅ pushed | n/a | DONE |
| — | Live re-checks P2 retrieval, P4 workflows/HITL, P5 agent core, P7 evals, P8 guardrails/grants | ✅ code pushed | ❌ | NOT STARTED (queue §5 item 3) |
| — | P6 memories & self-improvement | ❌ | ❌ | NOT STARTED |
| — | P9 scale (million docs) · P10 frontend e2e · P11 full rerun | ❌ | ❌ | NOT STARTED |

## 4. In flight (resume these first)

State at 2026-10-06: origin/main = local main = `b7495f911`. Everything is merged and pushed (B7 live, OCR parallelism,
worktree salvage, test fixes); the suite is green: backend 33,463 passed, frontend 5,238 passed.

**Resolved (kept for history), suite on `36a353a61`:**
- `tests/ocr/test_engine_parallel.py::test_real_render_is_page_by_page_at_the_configured_dpi` FAILS in the full run.
- `tests/ingestion/test_ocr_pdf_pages_parallel.py::test_render_pdf_page_accepts_a_path_and_maps_poppler_errors` FAILS in the full run.
- One chunk passes every test, then the interpreter aborts at exit:
  `libc++abi: ... recursive_mutex lock failed: Invalid argument` (EXIT 134). This is new with the OCR merge. Likely the
  OCR `ThreadPoolExecutor` / tesseract or poppler threads are still alive at interpreter shutdown; add an orderly
  shutdown (atexit / pool `shutdown(wait=True)`) and a test-session teardown.

Fixed: both tests now skip without the optional `pdf2image` extra (`43bd97335`). The exit abort did not reproduce on rerun
or in the next full suite — a watch item only.

| Item | Branch / worktree | State |
|---|---|---|
| B1, B2, B7-code, DEF-1..5, EGRESS-CFG | — | DONE, merged + pushed (B7 code `fcf68e1c9`, B2 `1d81f2cdc`, DEF `874712eb2`, egress `10c22195d`, test fixes `4d3f63be6`). |
| **9. B7 platform events** (live) | `live/p3-b7-platform-events` · `.claude/worktrees/p3b7` | Live verification of all six platform-event types. Also asked to merge main (DEF-5 changed vendor-signed webhook dedup) and rerun ALL INGRESS-* scenarios. Report target `live/p3-b7-platform-events.md`. If its agent is gone: WIP-commit, read the report, continue. |
| **OCR parallelism** (owner request) | `fix/ocr-parallelism` — MERGED locally `36a353a61` | Commits OCR-PAR-1..6. Suite issues above. Then a live check on the stack (A1 upload scenarios plus concurrent multi-document scans). |
| **Worktree salvage** (owner: "merge all branches/worktrees into main") | `fix/worktree-salvage` — MERGED locally | 17/17 checked; only the RV-08 GDPR gaps were missing (4a8bed96a, 0048fd34d, 080571bb5). Report `docs/audits/fixwave/worktree-salvage-2026-10-06.md`. |

**OCR parallelism (merged).** Benchmark: 20-page scan 18.2 s → 3.6 s; 12-page upload 16.2 s → 3.6 s; 5 docs × 4
pages 6.1 s → 3.1 s; worst event-loop stall ~3 s → 0.05 s. The default render is now grayscale at 300 dpi (was colour
at 200 dpi); `OCR_RENDER_DPI=200` restores the old cost. Settings: `OCR_MAX_CONCURRENCY` (0 = CPUs this process may
use, cgroup-aware, split across prefork children), `OCR_PAGE_CONCURRENCY` (0 = max − 1), `OCR_VISION_CONCURRENCY` (4),
`OCR_RENDER_DPI` (300), `OMP_THREAD_LIMIT` (1), wired into every deployment.

Open from OCR:
- fairness is per document, not per tenant;
- connector syncs still ingest documents one at a time (pages within a document are parallel);
- the vision cost guard can overshoot by up to `OCR_VISION_CONCURRENCY` calls;
- remove the image `agentverse-ocrpar-bench:ocr-par` when done.

Original request (2026-10-06): OCR must process multiple documents and multi-page documents at the
same time. Root causes found:
- pages OCR'd one after another in `OcrEngine.extract` and `document_text.ocr_pdf_pages`;
- `pdf2image.convert_from_bytes` rasterises all pages on the event loop and holds them in memory;
- tesseract runs on the unbounded default executor;
- tesseract's OpenMP oversubscribes CPU when several run at once.

Fix scope:
- bounded concurrent pages, order and page numbers preserved;
- off-loop page-by-page render;
- one process-wide OCR limiter with per-document fairness;
- concurrent multi-file/ZIP;
- bounded vision fallback;
- `OMP_THREAD_LIMIT=1`;
- settings `OCR_MAX_CONCURRENCY`, `OCR_PAGE_CONCURRENCY`, `OCR_RENDER_DPI`;
- before/after benchmark (1×20-page scan; 5 docs × 4 pages).

After merge, verify live on the stack (A1 upload scenarios plus a concurrent multi-document scan).

**Worktree salvage.**
- **Committed work.** Every local/remote branch's committed work was verified on main by patch-id plus subject; the
  4 leftover commits are covered by RV-05/RV-09, RV-07, NATIVE-05, and a no-longer-needed alembic merge.
- **Uncommitted work.** 17 old worktrees held uncommitted edits (most killed by the 2026-10-02 Mac restart; 4
  `wf_01f33451-68a-*` audit ones from 09-27). Copies are backed up in `/private/tmp/claude-501/wtcheck/`:
  `<name>.patch`, `untracked/<name>/`, `index.txt`.
- **Final: 17/17 checked.** 16 are already on main or superseded, including the SAML-01 user sessions →
  `2572a5ba7` / migration `a7c3e9f1b2d4`. Only the RV-08 GDPR gaps were missing; they are ported and merged.
  Evidence:
  - FE-01 → `9477db129`
  - system-jobs-scaling → `1e0942ab6`
  - system-jobs-ingestion → `826f92c7c`
  - governance-startup → `fdb076128`
  - request-paths → `bd64943cf`
  - CORE-18 → `fd531b0bf`
  - WF-TIMEOUT → `18a6a0482`
  - real-world fixtures → `199cec12a` / `8f3630f09`
  - OPS-37 → `03a897c8c`
  - SVC-05 → `15be56b1c` / `fb988562f`
  - CHAT-CHANNEL-DEAD → `ac6458380`
  - ORG-32 → `af1e9bb43`
  - MCP health → `3e31d3df8` (optional partial index skipped, as before)
  - KB-44 → `969c2eeef`
- **MISSING, now ported and merged** (from `agent-a9c06085c934d55b1`, RV-08 GDPR):
  1. The async `run_gdpr_export` exports only goals + audit; add agents, schedules, knowledge_collections and
     api_keys metadata (never the hash).
  2. A strict save of the sync export result, with 503 if it cannot be recorded.
  3. The async payload write and job completion in one transaction.
  4. Log instead of `pass` when marking a job failed fails.

  Do NOT port the unlimited sync export (main caps it at 10k on purpose, a09-F212-11) or "no DB means failed".
- **After the salvage merge:** ask the owner before deleting the 17 worktrees and superseded branches.

**Live stack.**
- The live stack currently runs app containers from the p3b7 worktree (B7 live agent).
- The launchd `run_forever.py` starts its own beat whenever compose's beat disappears (even briefly during redeploy);
  this is an owner decision, pending.
- Owner MinIO `http://192.168.63.104:30900` (k8s NodePort): `192.168.63.104` is on the local `.env` allowlist (the
  allowlist matches hosts, so every port is covered). It is reachable from the live worker through the guarded client
  (health 200, anonymous 403). Port 9000 there is closed.

## 5. Queue (strict order)

1. OWNER SAID (2026-10-06): do NOT start new implementation until the owner picks what to do next.
2. Live OCR parallelism check.
3. **Live re-checks** of code fixes already pushed: P2 retrieval/grounding, P4 workflows/HITL, P5 agent core, P7 evals,
   P8 guardrails/grants (each: redeploy, run the scenarios, fix, merge, push).
4. **P6** memories & self-improvement (no scenarios yet; includes memory consolidations from A12).
5. **P9** scale: million-document ingestion/retrieval load, multi-worker; known: maintenance queue backlog
   (~6.5k tasks), ingestion ~4–6 docs/s, crawl live-set kept in the sync cursor.
6. **P10** frontend e2e (Playwright against the live backend).
7. **P11** full real-world rerun of every scenario + report; push.
8. **Deferred by owner to the very end** (after everything above is committed and pushed), in this order:
   10 A7 Google Drive/SharePoint/Confluence/Notion · 11 B3 GitHub/Stripe/Jira/Teams webhooks · 12 B8 conversational
   triggers · 13 C1–C5 channels Telegram/WhatsApp/Slack/Teams/generic webhook · 14 A6 Kafka.
   Code fixes for B3, C1–C5 and A6 are already on main (DEF-1..5, `874712eb2`); these items are now the LIVE
   verification, plus A7 and B8, which are not started. Wire `GATEWAY_PUBLIC_BASE_URL` into all deployments during
   item 13. No real vendor accounts: use exact payload formats + signing and local mock vendor APIs.
9. **Final sweep**: every branch/worktree merged (intentional skips: `backup-g04gov-pre-rv`, plus single duplicate
   commits on 3 old agent branches — already on main in another form), all tiers green (unit, frontend, integration,
   e2e_full normal + `E2E_LEAST_PRIVILEGE=1`, live real-world), push. Then ask the owner before deleting superseded
   branches/old worktrees.

Remaining backlog after the queue: `pending-all-2026-10-05.json` (re-verify each item first).

## 6. Open findings not yet assigned

- DEF-NEW-1: `GATEWAY_PUBLIC_BASE_URL` (needed for Telegram setWebhook) is only in `.env.example` — wire it into both
  Helm charts, raw k8s and prod compose (as done for the egress allowlist in `10c22195d`) during item 13.
- DEF-NEW-2 (owner decision): per-org `/v1/gateway/{org}/config` and `/channels/status` still return 501 (they sit
  on an unauthenticated prefix; replacement is `/channels/bindings`) — remove from OpenAPI/UI?
- DEF-NEW-3 [low]: GitHub/Jira Cloud signatures carry no timestamp, so replay protection relies on the trigger_events
  dedup row; a replay after retention purges that row fires again.
- DEF-NEW-4 [low]: webhook dedup family ignores SNS MessageId / Salesforce notification id (harmless: inside signed body).

- B2-OPEN-1: workflow webhook `hmac_secret` stored in plain text in the workflow definition (move to vault; P4).
- B2-OPEN-2: replaying a throttled delivery from the DLQ can run twice if the sender also redelivered it (P4).

- B7-NEW-1: goal approvals publish `hitl.approved` via a fire-and-forget background task — lost if the process
  shuts down mid-publish (should be awaited or outboxed).
- Chat clarify-round counter is per-process; ORM declares a chat-folder FK the DB lacks (chatd.progress.json).
- URL ingest is synchronous (no retry queue for single URLs); pipeline skips very short pages; crawl live set in cursor.
- Two S3 sources over the same objects in one collection share one document.
- Workflow-builder RAG step offers made-up strategy names (not `/rag/strategies` ids).
- Legacy helm chart (`agent-verse-backend/helm/agentverse`) workers lack the wait-for-schema init; k8s pgbouncer has
  no entry for a separate maintenance role.
- Postgres FTS splits `TJ-5531` into `tj` + `-5531` (bare "5531" misses FTS; other legs catch it).

## 7. Owner decisions (recorded in LIVE-E2E-PLAN.md)

Pending owner questions (2026-10-06):
- (a) ANSWERED 2026-10-06: the owner's MinIO is `http://192.168.63.104:30900` (a k8s NodePort). Verified from the live
  worker: guard ALLOWED, `/minio/health/live` 200. Port 9000 there is closed. A full S3 sync needs the owner's access key + bucket.
- (b) Remove the 501 per-org `/v1/gateway/{org}/config` and `/channels/status` from OpenAPI/UI?
- (c) launchd `run_forever.py` beat takeover: keep it, add a longer grace period, or disable?
- (d) Remote branch `origin/feature/isolated-agent-execution-environment` (one Jul-10 commit "Agent Isloation"):
  PaymentSystemMatrix diagrams + `docs/merchant-onboarding-requirements.md` + pyc/SDK `dist` build output (SDKs were
  removed). Bring in only the docs/diagrams, or skip?
- (e) GDPR export: include the tenant row (name, email, created_at) in the profile section? Currently tenant_id + plan only.
- (f) Per-Source reconcile interval override (from the KB-44 worktree) or keep the global
  `ingestion_reconcile_interval_seconds`?

1 scoped re-upload replace · 2 MongoDB auto id reindex · 3 A2A public directory opt-in · 4 Helm 4 procs/3.5Gi ·
5 no RPA plan limit · 6 channels C1–C5 in scope (deferred to end) · 7 chat transcripts indexed with admin switch +
per-user opt-in · `mongodb_delete_one` approvable via HITL · `LLM_REQUIRE_PLATFORM_KEY` for BYOK-only production ·
Redis doc ids host-independent.

## 8. Live stack notes
- Docker via colima (`colima start`; standalone `docker-compose`). Never drop volumes; prune only dangling images /
  build cache when disk is high (`docker system df`).
- Live app containers mount whichever worktree last redeployed them — the next live phase must redeploy from its own
  worktree (and after the final merge, from `main`).
- Local-only egress allowlist in the main checkout's gitignored `agent-verse-backend/.env`:
  `INGESTION_ALLOW_INTERNAL_SOURCES=true`, `INGESTION_INTERNAL_SOURCE_ALLOWLIST=minio,rw-s3,rw-pg,rw-mysql,rw-mongo,
  rw-mongo-alt,rw-mongo-tls,rw-mongo-stall,rw-redis,rw-redis-tls,rw-es,rw-web,rw-web-b,192.168.63.104` (backups under
  `/private/tmp/claude-501/rw/p1*/dotenv.before-*`). Production defaults unchanged.
- Test containers (labels `p1b=live-test`, `p1c=live-test`, `p1d=live-test`): agentverse-rw-{pg,mysql,s3,mongo,
  mongo-tls,mongo-stall,redis,redis-tls,es,web,ingestion-worker}. Throwaway creds in `/private/tmp/claude-501/rw/p1c/infra.env`.
- Live tenants/keys: `/private/tmp/claude-501/rw/{primary,second,enterprise,approver,enterprise_approver}.json`
  (never commit). Real-world runner: `scripts/run_real_world.sh`, per-phase wrappers `/private/tmp/claude-501/rw/<phase>/rw.sh`.
- `/private/tmp` can be wiped by a machine restart; everything durable is in the repo docs.
