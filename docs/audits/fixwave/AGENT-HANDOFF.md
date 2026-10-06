# Agent handoff — AgentVerse fix / live-e2e program

> Resume prompt for a new agent:
> "Read `docs/audits/fixwave/AGENT-HANDOFF.md` and continue from **§4 In flight** then **§5 Queue**,
> following **§3 Procedure** exactly. Do not redo anything in **§2 Done**."

Last updated: 2026-10-06. `origin/main` = `f0dedbde9` (local `main` identical at time of writing).

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
| 7 | B1 Time triggers (cron, interval, once, relative_delay, deadline, business_calendar) | COMPLETE — merged to main 8f6621d5e (push after its full suite) |

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
collab presence dead-peer pruning, Stripe-literal guard test.

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
   (paths starting with `/` are not secrets).
3. Only then `git push origin main`. GitHub push protection once rejected a fake `sk_live_...` test literal; if that
   happens, rewrite the unpushed range (filter-branch tree-filter to split the literal) — never use the bypass URL.

## 4. In flight (resume these first)

| Item | Branch / worktree | State |
|---|---|---|
| **B1 push** | main `8f6621d5e` | B1 merged locally; full suite was running in `.claude/worktrees/verify`. If not yet pushed: rerun §3.2 suite, gate §3.3, push. |
| **8. B2 webhook / rest / event** (live) | `live/p3-b2-ingress-triggers` · `.claude/worktrees/p3b2` | Started from main 8f6621d5e. Scope: signed webhooks (HMAC, replay window, dedup, size cap, mapping, filters, tenant isolation, quotas, audit, DLQ, token rotation, indexed lookup), REST trigger, event-bus triggers (reconnect after Redis error, exactly-once), dispatcher order (rate limit before dedup claim; caller role not defaulting to operator). If its agent is gone: WIP-commit in the worktree, check `docs/audits/fixwave/live/p3-b2-ingress-triggers.md`, continue. |

Note: the live stack currently mounts the p3b1/p3b2 worktree; B1 added a `schedule-worker` service. The launchd
`run_forever.py` starts its own beat whenever compose's beat disappears (even briefly during redeploy) — owner decision pending.

## 5. Queue (strict order)

1. **9. B7** platform events (live): goal_completed/goal_failed self-loop, goal_score_below, hitl_approved/rejected,
   memory_created (worker never publishes it).
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
   Known: Teams routes by shared serviceUrl (cross-tenant), Slack slash commands single-tenant, channel binding only
   via `CHANNEL_TENANT_MAP` env (per-org gateway config 501), Telegram/WhatsApp no e2e, Kafka commits offsets before
   indexing. No real vendor accounts: use exact payload formats + signing and local mock vendor APIs.
9. **Final sweep**: every branch/worktree merged (intentional skips: `backup-g04gov-pre-rv`, plus single duplicate
   commits on 3 old agent branches — already on main in another form), all tiers green (unit, frontend, integration,
   e2e_full normal + `E2E_LEAST_PRIVILEGE=1`, live real-world), push. Then ask the owner before deleting superseded
   branches/old worktrees.

Remaining backlog after the queue: `pending-all-2026-10-05.json` (re-verify each item first).

## 6. Open findings not yet assigned
- Chat clarify-round counter is per-process; ORM declares a chat-folder FK the DB lacks (chatd.progress.json).
- URL ingest is synchronous (no retry queue for single URLs); pipeline skips very short pages; crawl live set in cursor.
- Two S3 sources over the same objects in one collection share one document.
- Workflow-builder RAG step offers made-up strategy names (not `/rag/strategies` ids).
- Legacy helm chart (`agent-verse-backend/helm/agentverse`) workers lack the wait-for-schema init; k8s pgbouncer has
  no entry for a separate maintenance role.
- Postgres FTS splits `TJ-5531` into `tj` + `-5531` (bare "5531" misses FTS; other legs catch it).

## 7. Owner decisions (recorded in LIVE-E2E-PLAN.md)
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
  rw-mongo-alt,rw-mongo-tls,rw-mongo-stall,rw-redis,rw-redis-tls,rw-es,rw-web,rw-web-b` (backups under
  `/private/tmp/claude-501/rw/p1*/dotenv.before-*`). Production defaults unchanged.
- Test containers (labels `p1b=live-test`, `p1c=live-test`, `p1d=live-test`): agentverse-rw-{pg,mysql,s3,mongo,
  mongo-tls,mongo-stall,redis,redis-tls,es,web,ingestion-worker}. Throwaway creds in `/private/tmp/claude-501/rw/p1c/infra.env`.
- Live tenants/keys: `/private/tmp/claude-501/rw/{primary,second,enterprise,approver,enterprise_approver}.json`
  (never commit). Real-world runner: `scripts/run_real_world.sh`, per-phase wrappers `/private/tmp/claude-501/rw/<phase>/rw.sh`.
- `/private/tmp` can be wiped by a machine restart; everything durable is in the repo docs.
