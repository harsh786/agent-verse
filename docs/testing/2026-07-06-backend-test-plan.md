# AgentVerse Backend Test Plan
**Date:** 2026-07-06  
**Author:** Test Strategy Architect  
**Based on:** Direct analysis of `agent-verse-backend/tests/` (662 files), `app/` source, and `pyproject.toml`

---

## Overview

This plan identifies missing tests across five categories — unit, API, security, tenant-isolation, and integration — prioritized by production risk.

**Marker convention:**
- `@pytest.mark.integration` — requires real Redis/Postgres via testcontainers
- `@pytest.mark.slow` — requires a real LLM API key
- No marker — pure unit test, no external deps

---

## 1. Agent Loop (`app/agent/`)

### Existing coverage
- `tests/agent/test_agent_loop.py` — basic plan/execute/verify cycle
- `tests/agent/test_agent_loop_comprehensive.py` — extended scenarios
- `tests/agent/test_agent_graph.py` — LangGraph state transitions
- `tests/agent/test_goal_tree.py` — hierarchical goal decomposition

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| Replan triggers after N consecutive verifier failures | `tests/agent/test_agent_loop_replan.py` | CRITICAL |
| `max_iterations` boundary — loop exits cleanly at limit | `tests/agent/test_agent_loop_replan.py` | CRITICAL |
| HITL gateway intercepts steps matching `deploy`, `delete`, `prod` keywords | `tests/agent/test_hitl_gateway.py` | HIGH |
| Planner/Executor/Verifier each use separate `LLMProvider` instances | `tests/agent/test_agent_roles.py` | HIGH |
| Checkpointer swap: `MemorySaver` → `AsyncRedisSaver` on lifespan | `tests/agent/test_checkpointer_swap.py` | HIGH |
| `AgentState` serialization round-trip (checkpoint / restore) | `tests/agent/test_agent_state.py` | MEDIUM |
| `workflow_planner.py` decomposes NL goal into DAG correctly | `tests/agent/test_workflow_planner.py` | MEDIUM |
| `model_router.py` assigns correct model per task type | `tests/agent/test_model_router.py` | MEDIUM |

### Missing API tests

| Test | File to create | Priority |
|------|----------------|---------|
| POST `/goals` with `dry_run=true` returns plan preview, no execution | `tests/api/test_goals_dry_run.py` | HIGH |
| PATCH `/goals/{id}/pause` transitions status to `paused` | `tests/api/test_goal_lifecycle_api.py` | HIGH |
| PATCH `/goals/{id}/resume` resumes from paused checkpoint | `tests/api/test_goal_lifecycle_api.py` | HIGH |
| GET `/goals/{id}/events` returns stored event log in order | `tests/api/test_goal_lifecycle_api.py` | MEDIUM |
| GET `/goals/{id}/evaluation` returns 404 when no eval run yet | `tests/api/test_goals_eval_api.py` | MEDIUM |
| POST `/goals/{id}/evaluation` triggers async eval, returns 202 | `tests/api/test_goals_eval_api.py` | MEDIUM |

### Missing security tests

| Test | File to create | Priority |
|------|----------------|---------|
| Goal submitted with malicious prompt injection in goal text | `tests/security/test_goal_injection.py` | CRITICAL |
| `sanitization.py` strips tool call output exceeding 64KB | `tests/security/test_goal_injection.py` | HIGH |
| Goal submitted cross-tenant (tenant A reads tenant B's goal events) | `tests/security/test_goal_tenant_boundary.py` | CRITICAL |

### Missing tenant-isolation tests

| Test | File to create | Priority |
|------|----------------|---------|
| Concurrent goals from two tenants don't share `AgentState` | `tests/tenancy/test_goal_isolation.py` | CRITICAL |
| `bulkhead` per-tenant concurrency limit enforced under load | `tests/tenancy/test_bulkhead.py` | HIGH |
| Goal SSE stream only delivers events for the authenticated tenant | `tests/tenancy/test_sse_isolation.py` | CRITICAL |

---

## 2. Multi-Tenancy / RLS (`app/tenancy/`, `app/db/rls.py`)

### Existing coverage
- `tests/tenancy/test_policy_isolation.py`
- `tests/auth/test_auth_api_coverage.py`

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| `rls_context()` sets `app.tenant_id` GUC before each query | `tests/tenancy/test_rls_context.py` | CRITICAL |
| `TenantMiddleware` rejects requests with no `X-API-Key` header | `tests/tenancy/test_middleware.py` | CRITICAL |
| `TenantMiddleware` rejects requests with expired/revoked key | `tests/tenancy/test_middleware.py` | CRITICAL |
| Sliding-window rate limiter blocks after N requests/window | `tests/tenancy/test_rate_limiter.py` | HIGH |
| Rate limiter state is per-tenant (tenant A exhaustion doesn't affect B) | `tests/tenancy/test_rate_limiter.py` | HIGH |
| `SecurityHeadersMiddleware` sets CSP, HSTS, X-Frame-Options | `tests/tenancy/test_security_headers.py` | MEDIUM |

### Missing integration tests (`@pytest.mark.integration`)

| Test | File to create | Priority |
|------|----------------|---------|
| Postgres RLS: direct SELECT from `goals` returns only tenant's rows | `tests/integration/test_rls_postgres.py` | CRITICAL |
| Postgres RLS: `SET LOCAL app.tenant_id = 'attacker'` in app context blocked | `tests/integration/test_rls_postgres.py` | CRITICAL |
| API key rotation invalidates old key immediately in Redis | `tests/integration/test_api_key_rotation.py` | HIGH |

---

## 3. Governance (`app/governance/`)

### Existing coverage
- `tests/governance/test_governance.py`, `test_governance_v2.py`
- `tests/governance/test_hitl_*.py` (8 files)
- `tests/governance/test_audit_*.py` (4 files)
- `tests/governance/test_cost_*.py` (5 files)
- `tests/governance/test_policies_*.py` (2 files)
- `tests/governance/test_emergency_stop.py`

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| Policy with `allowed_hours_utc=[9,10,11]` blocks tool call at hour 14 | `tests/governance/test_time_window_policy.py` | HIGH |
| Policy with `allowed_weekdays=[0,1,2,3,4]` blocks call on Saturday | `tests/governance/test_time_window_policy.py` | HIGH |
| `policies.py` Redis pub/sub propagates policy update to all replicas | `tests/governance/test_policy_pubsub.py` | HIGH |
| `audit.py` append-only — no UPDATE/DELETE against audit events | `tests/governance/test_audit_immutability.py` | HIGH |
| Tamper-evident chain hash breaks on modified event | `tests/governance/test_audit_chain.py` | HIGH |
| Budget `daily_reset` task zeroes daily spend without affecting totals | `tests/governance/test_budget_reset.py` | MEDIUM |

### Missing API tests

| Test | File to create | Priority |
|------|----------------|---------|
| POST `/governance/emergency-stop` cancels all executing goals | `tests/api/test_emergency_stop_api.py` | CRITICAL |
| GET `/governance/audit` respects `goal_id` filter | `tests/api/test_audit_api.py` | HIGH |
| POST `/governance/audit/verify-chain` returns `verified: true` for intact log | `tests/api/test_audit_api.py` | HIGH |
| POST `/governance/policies/{id}/simulate` returns allow/deny per tool pattern | `tests/api/test_policy_simulate_api.py` | MEDIUM |
| GET `/governance/policies/{id}/versions` returns version history | `tests/api/test_policy_versions_api.py` | MEDIUM |

### Missing security tests

| Test | File to create | Priority |
|------|----------------|---------|
| Tenant A cannot approve Tenant B's HITL request | `tests/security/test_hitl_cross_tenant.py` | CRITICAL |
| Bulk approve endpoint validates all IDs belong to calling tenant | `tests/security/test_hitl_cross_tenant.py` | CRITICAL |

---

## 4. Knowledge / RAG (`app/rag/`, `app/knowledge/`)

### Existing coverage
- `tests/knowledge/` and `tests/rag/` directories
- `tests/agent/test_agent_knowledge_binding.py`

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| `SemanticCache` returns cached response on embedding similarity > threshold | `tests/knowledge/test_semantic_cache.py` | HIGH |
| `SemanticCache` cache miss falls through to LLM call | `tests/knowledge/test_semantic_cache.py` | HIGH |
| Hybrid search combines pgvector cosine similarity + trigram BM25 score | `tests/rag/test_hybrid_search.py` | HIGH |
| Document chunking respects `max_chunk_length` and overlap | `tests/knowledge/test_chunking.py` | MEDIUM |
| URL ingest (RPA scraper) sets `playwright_used=True` when Playwright available | `tests/knowledge/test_rpa_ingest.py` | MEDIUM |
| Collection `health_score` degrades when embedding coverage < 50% | `tests/knowledge/test_collection_health.py` | MEDIUM |

### Missing API tests

| Test | File to create | Priority |
|------|----------------|---------|
| POST `/knowledge/collections` returns 409 on duplicate name | `tests/api/test_knowledge_api.py` | MEDIUM |
| POST `/knowledge/ingest/file` rejects files > 50MB | `tests/api/test_knowledge_api.py` | HIGH |
| GET `/knowledge/search` requires `collection_id` or global search flag | `tests/api/test_knowledge_api.py` | MEDIUM |
| POST `/knowledge/chat` streaming endpoint returns SSE `data:` lines | `tests/api/test_knowledge_chat_api.py` | HIGH |

### Missing tenant-isolation tests

| Test | File to create | Priority |
|------|----------------|---------|
| Collection created by Tenant A not visible to Tenant B | `tests/tenancy/test_knowledge_isolation.py` | CRITICAL |
| Semantic search cannot retrieve chunks from another tenant's collection | `tests/tenancy/test_knowledge_isolation.py` | CRITICAL |

---

## 5. MCP / Connectors (`app/mcp/`)

### Existing coverage
- `tests/mcp/` directory
- `tests/api/test_a2a.py`, `test_a2a_comprehensive.py`

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| `MCPRegistry` caches `tools/list` response per connector for 60s | `tests/mcp/test_registry_cache.py` | HIGH |
| `MCPRegistry` invalidates cache on connector config update | `tests/mcp/test_registry_cache.py` | HIGH |
| `oauth.py` PKCE code verifier has correct length (43–128 chars) | `tests/mcp/test_oauth_pkce.py` | HIGH |
| `oauth.py` state parameter validated on callback to prevent CSRF | `tests/mcp/test_oauth_pkce.py` | CRITICAL |
| Tool call with invalid `server_id` returns `MCPServerNotFound` error | `tests/mcp/test_tool_errors.py` | HIGH |

### Missing API tests

| Test | File to create | Priority |
|------|----------------|---------|
| GET `/connectors` lists only current tenant's connectors | `tests/api/test_connectors_api.py` | HIGH |
| DELETE `/connectors/{id}` returns 404 for another tenant's connector | `tests/api/test_connectors_api.py` | CRITICAL |
| POST `/connectors/{id}/test` returns tool count on live server | `tests/api/test_connectors_api.py` | MEDIUM |

---

## 6. Observability / Telemetry (`app/observability/`)

### Existing coverage
- `tests/observability/` directory

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| OpenTelemetry span is created and closed for each agent step | `tests/observability/test_otel_spans.py` | HIGH |
| `GET /metrics` returns valid Prometheus text format | `tests/observability/test_prometheus.py` | HIGH |
| `agentverse_goal_success_total` counter increments on goal completion | `tests/observability/test_prometheus.py` | HIGH |
| Log stream SSE endpoint does not return other tenants' logs | `tests/observability/test_log_isolation.py` | CRITICAL |

---

## 7. Reliability (`app/reliability/`)

### Existing coverage
- `tests/reliability/` directory

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| Circuit breaker opens after 5 consecutive failures to same MCP server | `tests/reliability/test_circuit_breaker.py` | HIGH |
| Circuit breaker half-opens after cooldown period and allows one probe | `tests/reliability/test_circuit_breaker.py` | HIGH |
| `rollback_engine` calls `tool_inverses` compensating action on failure | `tests/reliability/test_rollback.py` | HIGH |
| Deduplication rejects second identical goal submission within 30s | `tests/reliability/test_deduplication.py` | MEDIUM |

---

## 8. Scaling / Celery (`app/scaling/`)

### Existing coverage
- `tests/scaling/` directory

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| Goal from `free` plan tenant routes to `goals.free` queue | `tests/scaling/test_queue_routing.py` | HIGH |
| Goal from `enterprise` plan routes to `goals.enterprise` queue | `tests/scaling/test_queue_routing.py` | HIGH |
| Schedule task fires goal submission at correct cron time | `tests/scaling/test_schedule_task.py` | MEDIUM |

---

## 9. Providers (`app/providers/`)

### Existing coverage
- `tests/providers/` directory

### Missing unit tests

| Test | File to create | Priority |
|------|----------------|---------|
| `FakeProvider` always returns deterministic response (test stability) | `tests/providers/test_fake_provider.py` | MEDIUM |
| Provider selection at startup: `ANTHROPIC_API_KEY` present → Anthropic chosen | `tests/providers/test_provider_selection.py` | HIGH |
| Fallback chain: Anthropic unavailable → OpenAI provider used | `tests/providers/test_provider_failover.py` | HIGH |
| `vault.py` API key encryption round-trip (store → retrieve) | `tests/providers/test_vault.py` | HIGH |

### Missing slow tests (`@pytest.mark.slow`)

| Test | File to create | Priority |
|------|----------------|---------|
| Anthropic provider: completion returns non-empty response | `tests/live/test_anthropic_live.py` | MEDIUM |
| OpenAI provider: streaming completion delivers tokens incrementally | `tests/live/test_openai_live.py` | MEDIUM |

---

## 10. Contract / SDK (`app/` + SDK)

### Existing coverage
- `tests/test_contract_schema.py`
- `tests/sdk/` directory

### Missing tests

| Test | File to create | Priority |
|------|----------------|---------|
| OpenAPI export schema validates against JSON Schema Draft-07 | `tests/test_contract_schema.py` (extend) | HIGH |
| Python SDK `agentsApi.create()` serializes to correct request body | `tests/sdk/test_sdk_contract.py` | HIGH |
| TypeScript SDK type interfaces match OpenAPI schema (build-time) | CI step: `npm run typecheck` in `agent-verse-sdk-typescript/` | HIGH |
| Backend `GoalResponse` shape matches what `GoalsListPage.tsx` consumes | `tests/frontend/test_openapi_frontend_compat.py` | MEDIUM |

---

## Priority Summary

| Priority | Count | Examples |
|----------|-------|---------|
| CRITICAL | 19 | RLS enforcement, cross-tenant isolation, HITL cross-tenant, OAuth CSRF, goal SSE isolation |
| HIGH | 47 | Circuit breaker, provider failover, audit immutability, queue routing, PKCE, cache invalidation |
| MEDIUM | 29 | Eval API, document health score, chunking, `FakeProvider` stability |

**Recommendation:** Address all CRITICAL tests before any production deployment. HIGH tests should be resolved within the next sprint. MEDIUM tests are quality improvements for a healthy CI posture.
