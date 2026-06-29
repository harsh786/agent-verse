# Changelog

All notable changes to AgentVerse are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- World-class visual Workflow Builder with ReactFlow (9 node types, NL-generate, live SSE execution, drag-drop)
- Agent Civilization: multi-agent society with Governor, Blackboard, Constitution, emergent behavior
- Self-Improvement system: SelfOptimizer v2 with Bayesian A/B testing and Mann-Whitney U significance
- Agent Lab: pre-flight checks, live simulation streaming, comparative eval scoring
- Agent Identity: per-agent credentials (JWT/api_key/mTLS), 13 API scopes, domain-specific identity
- Perception module: Playwright-based screenshot, vision LLM analysis, structured text extraction
- Training Export: JSONL fine-tuning export (OpenAI + Anthropic formats) with 0.8 quality gate
- A2A (Agent-to-Agent) protocol with HMAC-signed delegation and cross-tenant task handoff
- Notifications Center: multi-channel (Slack/email/webhook) with delivery logs
- RBAC page: role management, IP allowlist, JWT scope explorer
- Compliance page: GDPR export/deletion, SOC2 package, consent management, legal hold
- Audit Explorer: typed model, date/tool/outcome filters, CSV/JSON export
- Guardrail Center: 6-layer injection defense config, PII redaction rules
- Budget Manager: per-agent and per-tenant LLM spend limits with anomaly detection
- Scope Explorer: 20 API scopes across 6 resource groups, plan ceiling visualization
- Memory Explorer: 3-layer memory (execution/long-term/tool-reliability), pgvector recall
- Artifacts Browser: MinIO-backed file viewer with pre-signed URLs
- Integrations page: Slack/Zapier/AlertManager/Datadog webhook configuration display
- Ghost Run: triple-strategy goal preview without real tool execution
- 32 MCP connector catalog with OpenAPI importer and OAuth PKCE flow
- 88.9% backend unit test coverage (10,290 tests)
- 57-document feature documentation (15,000+ lines, Mermaid diagrams)
- Complete operations RUNBOOK (1,292 lines)
- k6 load test suite (smoke, auth throughput, goal submission, soak)
- SDK CI publish pipelines (PyPI OIDC + npm provenance)

### Fixed
- CircuitBreaker `can_call_async()` missing — silently disabled in no-Redis deployments
- `rollback_all()` fire-and-forget → `rollback_all_async()` with proper await
- HITL `_db_session_factory` not wired in main.py — approvals not persisted across restarts
- `SIEMAdapter` base class `raise NotImplementedError` → `abc.ABC` + `NullSIEMAdapter`
- FakeProvider silent in production → explicit `RuntimeError` guard
- `_FakeRedis` sorted-set race conditions → asyncio.Lock
- GovernancePage approve/reject missing `approver` field → real 422 fixed
- EvalPage eval suites hitting `/eval/suites` (wrong) → `/intelligence/eval-suites`
- ConnectorsCatalogPage `Register` not pre-filling form → `location.state.prefill` wired
- AgentGraph `rollback_all()` sync call on failure → `rollback_all_async()`
- HITL cross-replica gap: `asyncio.Event` → Redis BLPOP path used when Redis available
- `RedisCostController` non-atomic check-and-increment → Lua script atomic CAS
- `discover_and_tick_civilizations` missing `@celery_app.task` decorator
- `GoalDiffPage` positional line comparison → real LCS Myers diff
- `GoalDNAPage` 4-column grid → BFS-depth DAG layout via `layeredLayout`
- Emergency stop state lost on navigation → persisted to Zustand + sessionStorage

### Infrastructure
- Prometheus + Grafana Docker healthchecks added
- Helm `Chart.yaml` and `values.yaml` created (was stub)
- `vite-env.d.ts` VITE_API_URL typed declaration
- CONTRIBUTING.md added
- `(import.meta as any)` pattern replaced with typed `import.meta.env`

---

## [0.1.0] — 2026-06-25 (Initial Release)

### Added
- Core agent execution engine (LangGraph StateGraph, 12-step pipeline, parallel wave execution)
- Multi-agent patterns: Supervisor, Debate/consensus, Goal-tree decomposition
- Multi-tenant architecture with PostgreSQL Row-Level Security
- HITL (Human-in-the-Loop) approval gateway
- MCP (Model Context Protocol) connector registry
- Celery task queue with per-plan routing
- FastAPI backend with 29 routers
- React 19 frontend with 40+ pages
- Python SDK + TypeScript SDK
- Docker Compose + Kubernetes manifests
- OpenTelemetry + Jaeger + Prometheus + Grafana observability stack
