# AgentVerse Full-Product Live Certification

Run ID: `AVCERT-20260729-001`  
Executed: 2026-07-28/29 (Asia/Kolkata)  
Revision: `ca400a37cbe0caa6d520d41cfd31e9de7bbadde5`  
Verdict: **NOT CERTIFIED**

## Executive summary

AgentVerse has substantial working coverage: the complete local infrastructure starts, the
OpenAI connection is real, advanced reasoning and RAG pattern modules produce useful output,
Postgres/pgvector and Redis are reachable, both SDKs pass their isolated suites, and read-only
Jira operations work against the configured Jira account.

The product is not safe to certify as production-ready. The most important blockers are:

1. The Compose application database role is a PostgreSQL superuser with `BYPASSRLS`, so the
   advertised database-enforced tenant boundary is bypassed.
2. A real goal submitted through the public API reaches Celery and fails at iteration zero
   because the worker gives async LangGraph a synchronous Redis checkpointer whose async
   methods raise `NotImplementedError`.
3. Persisted Fusion RAG performs concurrent operations through one `AsyncSession`, which
   asyncpg does not permit.
4. Adaptive/HyDE retrieval constructs `CompletionRequest` without its required `model`.
5. The live RAG API returns HTTP 200 while its vector leg fails because the retriever and
   `KnowledgeStore.search()` signatures disagree.
6. Static, backend, frontend-component, integration, and browser gates all contain material
   failures.

No AI provider was mocked in the live-provider certification modules. Mock-heavy frontend
coverage was kept separate and is not counted as live certification evidence.

## Environment and infrastructure

| Area | Result | Evidence |
|---|---:|---|
| Colima / Docker | PASS | Colima running on Apple Virtualization Framework; Docker 28.3.3 |
| Compose topology | PASS | 20 services running; all declared health checks healthy |
| PostgreSQL | PASS with critical security defect | pgvector and pg_trgm installed; migrations at `0090_parent_child_retrieval` |
| Redis | PASS | `PING` returned `PONG` |
| PgBouncer | PASS | Connection on port 6432 and `SELECT 1` succeeded |
| Celery | PASS for availability | Worker ping succeeded; live execution fails at the checkpointer boundary |
| Frontend/backend HTTP | PASS | Both returned HTTP 200 |
| Keycloak, MinIO, SearXNG | PASS | Functional HTTP health/readiness checks |
| Prometheus, Grafana, Loki, Jaeger, OTel | PASS | Functional readiness/API/metrics checks |
| Kong | PARTIAL | DB-less proxy and `/api/health` work on 8082; published 8001 has no listener |

Toolchain used: uv 0.11.23, backend Python 3.12.12 through uv, Node 24.10.0,
npm 11.6.0, docker-compose 2.39.2.

## Live, no-mock certification results

### Real OpenAI agent patterns

Command:

```bash
uv run pytest tests/real_e2e/test_agent_patterns_real_openai.py -v -s --no-cov
```

Result: **12 passed in 911.54s**

Exercised Chain-of-Thought, Self-Refine, Reflexion, Tree-of-Thoughts, Peer Review,
Self-Consistency, ReAct, Plan-and-Execute, Debate/Voting, execution memory, multi-step
planning, and high-iteration execution.

Important limitation: every test accepts both `complete` and `failed` as a valid terminal
status. The results prove real model calls and pattern output, but do not prove successful
goal completion. The suite's claim that it covers “ALL agent patterns” also omits the
top-level `SupervisorAgent` execution path.

### Real OpenAI RAG patterns

Command:

```bash
uv run pytest tests/real_e2e/test_rag_patterns_real_openai.py -v -s --no-cov
```

Result: **16 passed in 178.63s**

Exercised Fusion, Corrective/CRAG, Adaptive, FLARE, Self-RAG and critique metadata,
Speculative RAG, RAPTOR, ColBERT, agentic chunking, pattern metadata, query dependence,
and Fusion-to-ColBERT composition.

These tests use real OpenAI generation. Most retrieval inputs are an in-process corpus, so
the persisted Postgres layer was certified separately.

### Real-world full AgentGraph scenarios

Command:

```bash
uv run pytest tests/real_e2e/test_full_pipeline_real_world.py -v -s --no-cov
```

Result: **11 passed, 1 failed in 1081.21s**

Passing scenarios included DevOps incident analysis, payment compliance, code review, data
analysis, security audit, technical documentation, RAG-powered analysis, multi-step
analysis, product requirements, Self-Refine with memory, and Tree-of-Thoughts.

Failed scenario:

- REST refund API design completed with repeated
  `INSUFFICIENT DATA: No specific context or details about the API resources are provided`
  instead of endpoints, methods, schemas, and error codes.

Like the agent-pattern module, this suite's `is_done()` accepts both `complete` and `failed`,
so its passing count is output-oriented rather than a strict successful-completion count.

### Real Postgres, pgvector, Redis, and OpenAI

Command:

```bash
uv run pytest tests/real_e2e/test_truly_live_everything.py -k 'not jira' -v -s --no-cov
```

Result: **15 passed, 3 failed, 5 deselected in 483.30s**

Confirmed real embedding ingestion, pgvector search, AgentGraph with DB knowledge,
real Redis I/O, Corrective RAG, FLARE, Self-RAG, Speculative RAG, RAPTOR, ColBERT,
agentic chunking, payment/SRE pipelines, Self-Refine, and Tree-of-Thoughts.

Failures:

- `test_b1_postgres_hybrid_bm25_vector_rrf`: calls synchronous in-memory
  `KnowledgeStore.hybrid_search()` on a newly constructed empty store. Despite its name,
  this test does not call the DB RRF implementation.
- `test_b3_fusion_rag_real_postgres`: concurrent variants share one `AsyncSession`;
  SQLAlchemy/asyncpg reports concurrent provisioning/operations are not permitted.
- `test_b3_adaptive_rag_real`: HyDE creates `CompletionRequest` without `model`, then its
  fallback retrieval is affected by the session/loop failure.

The test named `test_b2_agent_with_redis_checkpointing` performs direct Redis reads/writes
but passes `MemorySaver` to AgentGraph. It does not certify Redis LangGraph checkpointing.

### Jira

Only read-only nodes were run. Create/comment/delete nodes were intentionally excluded so
the certification did not alter or remove external Jira data.

Result: **2 passed in 1.16s**

- Listed 20 projects.
- POST search succeeded and returned configured Jira data.

### Live public API on a dedicated tenant

Result: **7 passed in 2.64s**

Covered health, model catalog, RAG query shape, knowledge-graph add/get, PII guardrails,
skills runtime, and secret scanning. The skills runtime made a confirmed OpenAI request.

This pass has a hidden functional defect: backend logs show the RAG vector leg failed with
`KnowledgeStore.search() got an unexpected keyword argument 'tenant_id'`; the endpoint still
returned HTTP 200 and the test only asserted response shape.

### Direct non-dry-run goal lifecycle

A fresh local tenant submitted a normal single-agent architecture-analysis goal through
`POST /goals`. The goal moved `planning -> executing -> failed`.

Persisted evidence:

- Celery wrote `worker_started`.
- LangGraph emitted `goal_failed` with `NotImplementedError (no message)`.
- Iterations remained zero.
- The worker then wrote `worker_complete` with status `failed`.

Root cause is confirmed in code and installed library behavior:

- `app/scaling/tasks.py` initializes `langgraph.checkpoint.redis.RedisSaver`.
- The worker passes it to async `AgentGraph`.
- `RedisSaver.aget_tuple()` is inherited from the base saver and unconditionally raises
  `NotImplementedError`.
- `app/main.py` correctly prefers `AsyncRedisSaver`; the Celery worker does not.

Result: **FAIL**

### Live browser/provider project

Result: **1 passed, 1 failed**

- Real dry-run goal submission returned a goal ID.
- Model registry failed because the browser test calls `/model-registry/models`; the live
  OpenAPI contract exposes `/models`.

Manual browser inspection confirmed that the public landing page and sign-in page render.
The landing page lacks a visible `main` landmark and has multiple WCAG contrast failures.

## Other quality gates

These gates are useful codebase evidence but are not counted as no-mock live-provider
certification.

| Gate | Result |
|---|---:|
| Backend Ruff | FAIL — 9,151 errors; 2,969 autofixable |
| Backend strict mypy | FAIL — 1,669 errors in 387 files |
| Backend non-slow/non-integration/non-real-provider | FAIL — 16,940 passed, 118 failed, 63 skipped, 1 xpassed |
| Container-backed integration marker | FAIL — 114 passed, 1 failed |
| Frontend lint | PASS with 134 warnings |
| Frontend typecheck | PASS |
| Frontend production build | PASS; main chunk about 2.566 MB |
| Frontend component tests | FAIL — 667 passed, 58 failed |
| Python SDK | PASS — 44 passed |
| TypeScript SDK | PASS — build plus 7 tests |
| GitHub Action image | PASS — image builds |
| GitHub Action error UX | FAIL — unreachable API produces a raw traceback |

The broad Playwright `full-live` project was stopped after 95/1,145 scheduled cases because
it is predominantly mock/interception based and therefore violates this run's no-mock
criterion. At the stop point: 63 passed, 30 failed, 2 interrupted, and 1,050 had not run.
Most protected-route failures were a systemic redirect to the public page from stale auth
setup, not independent product coverage.

## Defects and risk ranking

### P0 — Tenant isolation is bypassed by the application DB role

`agentverse` is both `rolsuper=true` and `rolbypassrls=true`. Local Compose connects the
backend and workers with that same role. PostgreSQL superusers and `BYPASSRLS` roles bypass
RLS even when tables use `FORCE ROW LEVEL SECURITY`.

The integration isolation test observed both tenants' rows. Its Testcontainers setup also
uses a superuser and is therefore not a valid RLS proof.

Required remediation:

- Create a migration/bootstrapping path for a separate, non-owner application role with
  `NOSUPERUSER NOBYPASSRLS`.
- Use that role for backend, worker, and beat `DATABASE_URL`.
- Reserve the owner role for migrations only.
- Make startup fail in production if the runtime role is superuser or has `BYPASSRLS`.
- Run isolation tests with the same restricted runtime role.

### P0 — Real queued goals fail with the Redis checkpointer

The Celery worker wires synchronous `RedisSaver` into async LangGraph. Use and lifecycle-manage
`AsyncRedisSaver` in the worker, call its setup method, and add a real worker execution test
that fails if async saver methods are unsupported.

### P1 — Fusion RAG shares one async session across concurrent queries

`retrieve_fusion()` uses `asyncio.gather()` while every task calls `hybrid_search()` with the
same session. Give each variant its own session/connection, or execute variants sequentially.

### P1 — RAG completion requests omit the required model

`retrieve_hyde()`, `retrieve_multi_hop()`, and `rerank_results()` construct
`CompletionRequest` without `model`. Make the request model optional with a safe provider
default, or pass the provider's configured model consistently.

### P1 — Live RAG API/store contract mismatch

`rag_platform/retriever.py` calls `KnowledgeStore.search(query, collection_id, tenant_id,
top_k)`, while `KnowledgeStore.search()` accepts only `query`, `collection_id`, and `top_k`.
The error is swallowed and the endpoint returns an ungrounded response.

### P1 — Real API-design scenario is over-refused

The agent treats a self-contained design request as missing evidence and repeatedly emits
`INSUFFICIENT DATA`. The grounding/refusal policy needs to distinguish knowledge claims from
generative design tasks.

### P1 — Static and regression backlog

The lint, typing, backend, and frontend failure counts are release blockers. The backend
failure clusters include tool-call repair, supervisor IDs, connectors/Jira execution,
knowledge URL ingestion, dynamic RAG, MCP/SSRF, Celery maintenance, checkpointer logging,
tenancy limits, and subprocess execution. Frontend failures span authentication,
artifacts, billing routing, connectors, goals, dashboard, collaboration, and streaming.

### P2 — Test and contract drift

- Provider UI test uses a removed/wrong registry path.
- Several live suites allow failed agent states to pass.
- “Postgres hybrid” and “Redis checkpoint” tests do not exercise what their names claim.
- Browser auth setup prevents much of the desktop suite from reaching protected routes.
- Landing-page contrast and landmark failures violate stated WCAG goals.

### P2 — Operational configuration gaps

- Kong's published admin port 8001 has no listener in the tested DB-less topology.
- MCP health reports many connector URLs without an HTTP/HTTPS scheme.
- The maintenance queue reported a depth above 21,000 during the run.

## Release decision

Do not promote this revision as production-ready or tenant-isolated. The minimum retest
sequence after remediation is:

1. Restricted-role RLS integration test and live two-tenant negative test.
2. A queued, non-dry-run goal that completes through Celery with `AsyncRedisSaver`.
3. Persisted hybrid, Fusion, Adaptive, and HyDE RAG tests using real Postgres and OpenAI.
4. Strict live-test assertions requiring `GoalStatus.COMPLETE`.
5. A real authenticated browser journey without request interception.
6. Ruff, mypy, backend, frontend-component, and browser gates to zero unexplained failures.

## Repository mutation note

No product source fix was applied during certification. Existing user changes were preserved.
Test runners updated their normal report/cache artifacts. Three dedicated local test tenants
and their scoped test data were created; Jira was accessed read-only.

## Canonical persisted RAG checkpoint

Checkpoint date: 2026-07-31

Tasks 7-12 of the canonical persisted RAG runtime plan were implemented after the original
certification run. The runtime now has one typed 18-strategy catalogue, tenant-aware gateway
readiness, bounded reasoning strategies, persisted RAPTOR and proposition indexes,
checkpoint-correct ColBERT reranking, validated Modular RAG, and a provider-neutral RAFT
lifecycle.

Security and durability controls include forced RLS on the RAFT tables, tenant-consistent
composite foreign keys, atomic and idempotent indexed ingestion, exact Decimal cost binding,
single-use RAFT confirmation grants, recoverable provider submission state, and monotonic
fine-tune job transitions. Paid RAFT submission remains disabled until a provider is
configured and the exact quoted cost is explicitly confirmed.

Final automated evidence on the merged implementation:

- RAG, knowledge, and affected API non-integration suites: **1,204 passed, 4 skipped,
  66 deselected**. The skips require unavailable local cross-encoder or ColBERT artifacts.
- PostgreSQL/Testcontainers integration suite: **66 passed, 1,093 deselected**.
- Exact scoped Ruff gate: **all checks passed**.
- Exact scoped mypy gate: **64 source files, no issues**.
- Alembic has one head: `0095_raft_lifecycle`.
- Regenerated OpenAPI contains seven `/rag/raft/*` lifecycle routes.

RAGatouille is pinned under the optional `colbert` extra rather than the default runtime
dependency set. The configured ColBERT checkpoint is used consistently by API and worker
execution paths.

This checkpoint clears the automated Program 1 RAG gate. It does not change the overall
**NOT CERTIFIED** verdict because current real-provider credentials were unavailable for the
17 non-paid live API journeys, and paid RAFT certification still requires explicit
action-time cost confirmation.
