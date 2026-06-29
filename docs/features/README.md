# AgentVerse Feature Documentation

> **World-class reference for every feature in the AgentVerse platform.**
> Each doc covers: purpose, how it works internally, backend APIs, algorithms, sequence diagrams, activity diagrams, and step-by-step usage guides.

**56 documents · 15,000+ lines · grounded in actual source code**

---

## Navigation by Sidebar Section

The sidebar has 5 sections matching the platform's capability groups.

---

## 🎯 Core

The three foundational pages every user starts with.

| Doc | Route | What It Covers |
|-----|-------|----------------|
| [Dashboard](core/01-dashboard.md) | `/dashboard` | KPI cards, activity feed, agent orbit, quick goal submit, new-user banner |
| [Goals Overview](core/goals/01-goals-overview.md) | `/goals` | Goal state machine, priority queue algorithm, submission modes, Celery routing |
| [Goal Detail](core/goals/02-goal-detail.md) | `/goals/:id` | Live SSE timeline, HITL controls, eval scorecard, pause/resume, event replay |
| [Ghost Run](core/goals/03-ghost-run.md) | `/goals/ghost-run` | Dry-run preview, plan visualization, cost estimation |
| [Agents Overview](core/agents/01-agents-overview.md) | `/agents` | Agent config schema, autonomy modes, model routing tiers |
| [Create Agent](core/agents/02-agent-create.md) | `/agents/create` | Manual form vs NL AI Builder, MetaAgentPlanner algorithm |
| [Agent Detail](core/agents/03-agent-detail.md) | `/agents/:id` | All 6 tabs: config, versions, credentials, permissions, knowledge, rollout gate |
| [Agent Dashboard](core/agents/04-agent-dashboard.md) | `/agents/:id/dashboard` | Per-agent performance charts, cost breakdown, health indicators |
| [Agent Personality & Radar](core/agents/05-agent-personality-radar.md) | `/agents/:id/radar` | Capability radar, personality sliders, agent identity credentials |

---

## 🔌 Platform

Services that give agents the ability to connect, remember, schedule, and collaborate.

| Doc | Route | What It Covers |
|-----|-------|----------------|
| [Connectors Overview](platform/connectors/01-connectors-overview.md) | `/connectors` | MCP protocol, 10 auth types, circuit breaker, Redis namespace isolation |
| [Connector Catalog](platform/connectors/02-connector-catalog.md) | `/connectors/catalog` | 32 built-in templates, OpenAPI importer, pre-fill registration flow |
| [Connector Detail](platform/connectors/03-connector-detail.md) | `/connectors/:id` | Health history, OAuth PKCE flow, reverse-lookup (which agents use it) |
| [Knowledge](platform/04-knowledge.md) | `/knowledge` | Hybrid pgvector+trgm search, 11 ingest sources, 3-layer RAG, semantic cache |
| [Schedules](platform/05-schedules.md) | `/schedules` | Cron/interval/webhook triggers, NL-to-schedule, celery-redbeat persistence |
| [Collaboration](platform/06-collaboration.md) | `/collaboration` | WebSocket sessions, OT versioning, consensus protocol, LLM synthesis |

---

## 🛡️ Governance

Controls, compliance, access management, and audit infrastructure.

| Doc | Route | What It Covers |
|-----|-------|----------------|
| [Governance Overview](governance/01-governance-overview.md) | `/governance` | PolicyEngine, emergency stop, budget, policy simulation |
| [Policies](governance/02-policies.md) | `/governance` (Policies tab) | Glob patterns, time-windows, version history, Redis pub/sub propagation |
| [Approvals (HITL)](governance/03-approvals.md) | `/approvals` | asyncio.Event blocking, multi-approver, DB persistence, notification dispatch |
| [Notifications](governance/04-notifications.md) | `/notifications` | Channel management, delivery logs, Slack/email/webhook, event triggers |
| [Access Control (RBAC)](governance/05-access-control-rbac.md) | `/rbac` | 4 roles, IP allowlist, JWT extraction, domain role templates |
| [Compliance](governance/06-compliance.md) | `/compliance` | GDPR 27-table cascade, SOC2 package, async export polling, consent management |
| [Audit Log](governance/07-audit-log.md) | `/audit` | Append-only dual-write, SOC2 fields, hash-chain integrity, CSV/JSON export |
| [Guardrails](governance/settings/08-guardrails.md) | `/settings/guardrails` | 6-layer injection defense, PII redaction, dangerous patterns, custom rules |
| [Scope Explorer](governance/settings/09-scope-explorer.md) | `/settings/scopes` | 20 API scopes, plan ceilings, seeder, key rotation with scope selection |
| [Settings](governance/settings/10-settings.md) | `/settings` | LLM config, API key management, BYOK vault, budget manager |

---

## 🏢 Enterprise

Advanced platform capabilities for production deployments at scale.

| Doc | Route | What It Covers |
|-----|-------|----------------|
| [Marketplace](enterprise/01-marketplace.md) | `/marketplace` | Template deploy, publish, bundles, security review, version history |
| [Observability](enterprise/02-observability.md) | `/observability` | Health registry, Prometheus metrics, OTel spans, Grafana |
| [Cost Dashboard](enterprise/03-cost-dashboard.md) | `/observability/cost` | Time-series cost charts, by-model breakdown, anomaly detection, cost predictor |
| [Enterprise Features](enterprise/04-enterprise-features.md) | `/enterprise` | GDPR async export, simulation sandbox, SOC2/PCI reports, SCIM/SAML |
| [Analytics](enterprise/05-analytics.md) | `/analytics` | Goals/tools/eval charts, time period selector, per-agent analytics |
| [Eval Overview](enterprise/eval/01-eval-overview.md) | `/eval` | 6-dimension scoring formulas, EvalRunner algorithm, eval scorecard |
| [Eval Suites](enterprise/eval/02-eval-suites.md) | `/eval` (Suites tab) | Regression test suites, CI integration, pass/fail trend |
| [Red Team](enterprise/eval/03-red-team.md) | `/eval` (Red Team tab) | 4 adversarial categories, pattern-based + behavioral testing |
| [Workflow Builder Overview](enterprise/workflow-builder/01-workflow-builder-overview.md) | `/workflow-builder` | ReactFlow canvas, NL-generate, save/version, import/export |
| [Workflow Node Types](enterprise/workflow-builder/02-node-types.md) | `/workflow-builder` | All 9 node types: Trigger, Tool Call, Agent Step, Decision, Parallel, Loop, HITL, Sub-workflow, End |
| [Workflow Execution](enterprise/workflow-builder/03-workflow-execution.md) | `/workflow-builder` | WorkflowPlanner, execution_waves(), live SSE canvas animation, dry-run |
| [Playground](enterprise/06-playground.md) | `/playground` | Custom mock tools, SimulationRunner, MockMCPClient injection |
| [Civilization](enterprise/07-civilization.md) | `/civilization` | Multi-agent society, Governor, Blackboard, Constitution, Society emergence |
| [Templates](enterprise/08-templates.md) | `/templates` | Variable substitution, instantiation, version history |
| [Ghost Run](enterprise/09-ghost-run.md) | `/goals/ghost-run` | Triple-submission preview, winner selection, plan visualization |
| [Self-Improvement](enterprise/10-self-improvement.md) | `/self-improvement` | SelfOptimizer v2, Bayesian A/B, Mann-Whitney U significance, prompt evolution |
| [Agent Lab](enterprise/11-agent-lab.md) | `/lab` | Pre-flight checks, live sim streaming, comparative eval scoring |

---

## 🔧 Tooling

Native tools, data access, external integrations, and advanced agent capabilities.

| Doc | Route | What It Covers |
|-----|-------|----------------|
| [Tools Overview](tooling/tools/01-tools-overview.md) | `/tools` | Native tools vs MCP tools, when to use each |
| [Code Runner](tooling/tools/02-code-runner.md) | `/tools` (Code tab) | Docker sandbox, Python/JS/Bash, timeout enforcement, security model |
| [File Manager](tooling/tools/03-file-manager.md) | `/tools` (Files tab) | Sandboxed file ops, path traversal protection, tenant workspace |
| [Email Composer](tooling/tools/04-email-composer.md) | `/tools` (Email tab) | SMTP vault config, HTML support, CC/BCC, Mailpit dev routing |
| [Memory](tooling/05-memory.md) | `/memory` | 3-layer memory (execution/long-term/tool-reliability), pgvector recall, EWMA |
| [Artifacts](tooling/06-artifacts.md) | `/artifacts` | MinIO storage, pre-signed URLs, content types, retention policy |
| [Integrations Overview](tooling/integrations/01-integrations-overview.md) | `/integrations` | Inbound webhook config display, copyable endpoint URLs |
| [Slack Integration](tooling/integrations/02-slack.md) | `/integrations` | Slash commands, HMAC verification, HITL Slack approval, events API |
| [Zapier & Webhooks](tooling/integrations/03-zapier-webhooks.md) | `/integrations` | Zapier trigger, AlertManager, Datadog events, generic webhooks |
| [Perception](tooling/07-perception.md) | `/perception` | Screenshot, vision LLM analysis, structured text extraction, PageAnalyzer |
| [Training Export](tooling/08-training-export.md) | `/training-export` | JSONL fine-tuning export, 0.8 quality gate, OpenAI/Anthropic formats |
| [A2A (Agent-to-Agent)](tooling/09-a2a.md) | `/a2a` | HMAC-signed delegation, agent card, cross-tenant tasks, callback flow |
| [RPA Sessions](tooling/rpa/01-rpa-sessions.md) | `/rpa/live` | Playwright browser control, 20+ tools, session persistence, human takeover |
| [Simulation](tooling/10-simulation.md) | `/simulation` | MockMCPClient, custom mock responses, policy simulate, vs Ghost Run |

---

## How to Read These Docs

Each document follows this structure:

1. **Overview** — What the feature is and why it exists
2. **Purpose & When to Use** — Concrete use cases and decision guidance
3. **Architecture** — How it works internally, component relationships
4. **Backend API** — Endpoints, request/response shapes, real curl examples
5. **Algorithms & Workflows** — Step-by-step logic, data flow
6. **Sequence Diagram** — `mermaid` diagrams showing component interactions
7. **Activity Diagram** — State machines and decision flows
8. **Step-by-Step Usage** — Practical guide for operators/developers
9. **Troubleshooting** — Common issues and fixes
10. **Integration** — How this feature connects to others

---

## Quick Reference: Feature → Source Code

| Feature | Frontend | Backend |
|---------|----------|---------|
| Dashboard | `features/dashboard/DashboardPage.tsx` | `api/goals.py` `governance.py` |
| Goals | `features/goals/GoalsListPage.tsx` | `api/goals.py` `services/goal_service.py` |
| Goal execution | `features/goals/GoalDetailPage.tsx` | `agent/graph.py` `agent/state.py` |
| Agents | `features/agents/` | `api/agents.py` `agent/router.py` |
| Connectors | `features/connectors/` | `mcp/registry.py` `mcp/client.py` `mcp/catalog.py` |
| Knowledge | `features/knowledge/KnowledgePage.tsx` | `rag/store.py` `knowledge/ingestors/` |
| Schedules | `features/schedules/SchedulesPage.tsx` | `triggers/store.py` `scaling/tasks.py` |
| Collaboration | `features/collaboration/CollaborationPage.tsx` | `collab/store.py` `api/collab.py` |
| Governance | `features/governance/GovernancePage.tsx` | `governance/policies.py` `api/governance.py` |
| Approvals (HITL) | `features/approvals/ApprovalsPage.tsx` | `governance/hitl.py` |
| Compliance | `features/compliance/CompliancePage.tsx` | `enterprise/compliance.py` |
| Guardrails | `features/settings/GuardrailCenterPage.tsx` | `intelligence/guardrails.py` |
| Workflow Builder | `features/workflow-builder/WorkflowBuilderPage.tsx` | `agent/workflow_planner.py` `api/workflows.py` |
| Civilization | `features/civilization/CivilizationPage.tsx` | `civilization/orchestrator.py` `civilization/governor.py` |
| Self-Improvement | `features/analytics/SelfImprovementPage.tsx` | `intelligence/self_optimizer_v2.py` |
| Memory | `features/memory/MemoryExplorerPage.tsx` | `memory/long_term.py` `memory/execution.py` |
| RPA | `features/rpa/RpaLivePage.tsx` | `rpa/executor.py` `rpa/session.py` |
| Perception | `features/perception/PerceptionPage.tsx` | `perception/browser_agent.py` `perception/page_analyzer.py` |
| A2A | `features/a2a/A2APage.tsx` | `mcp/a2a.py` `api/a2a.py` |
| Training Export | `features/training/TrainingExportPage.tsx` | `api/training_export.py` |
| Tools | `features/tools/ToolsPage.tsx` | `tools/code_interpreter.py` `tools/file_ops.py` |
