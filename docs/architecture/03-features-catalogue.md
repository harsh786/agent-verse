# AgentVerse OS — Complete Features Catalogue

> **Document 03 of 06** | AgentVerse Technical Architecture Series

---

## Overview

AgentVerse OS contains over **200 distinct features** spanning the agent execution engine, platform services, security infrastructure, compliance tooling, developer experience, and end-user interfaces. This document catalogues every feature from the smallest utility to the largest architectural capability.

---

## Part I: Goal Management

### 1.1 Goal Submission
| Feature | Description | API |
|---------|-------------|-----|
| Single agent goal | Natural language goal submitted to specific or auto-routed agent | `POST /goals` |
| Dry-run mode | Plan preview without executing tools, returns planned steps | `POST /goals` with `dry_run=true` |
| Batch goal submission | Submit multiple goals simultaneously | `POST /goals/batch` |
| Priority levels | normal, high, critical — affects Celery queue routing | `GoalRequest.priority` |
| Supervisor mode | LLM decomposes into parallel sub-goals automatically | `workflow_mode="supervisor"` |
| Multi-agent parallel | Same goal on N agents simultaneously for comparison | `workflow_mode="multi_agent", agent_ids=[...]` |
| Debate mode | N agents debate approach before execution (configurable rounds) | `workflow_mode="debate", debate_rounds=3` |
| Persistence mode | Keep retrying until goal achieved (6-strategy escalation) | `persistence_mode=true` |
| Agent auto-routing | When no agent_id specified, AgentRouter selects best agent | Automatic when `agent_id=null` |

### 1.2 Goal Lifecycle Management
| Feature | Description | API |
|---------|-------------|-----|
| Pause execution | Pause a running goal (HITL-like pause) | `POST /goals/{id}/pause` |
| Resume execution | Resume a paused goal | `POST /goals/{id}/resume` |
| Cancel goal | Immediately cancel and mark as cancelled | `POST /goals/{id}/cancel` |
| Goal replay | Re-run a completed goal and compare outcomes | `GET /goals/{id}/replay` |
| Goal evaluation | Get 6-dimension eval scorecard for any goal | `GET /goals/{id}/eval` |
| Event log | Full event timeline for any goal | `GET /goals/{id}/replay` → `timeline` |
| Execution graph | Force-graph visualization of tool call flow | `GET /insights/graph/{id}` |
| Failure analysis | LLM-powered root cause + suggestions for failed goals | `GET /insights/analysis/{id}` |
| Sub-goal tree | Parent→child lineage for multi-agent goals | `GET /goals/{id}/lineage` |
| Persistence controls | Abort, skip strategy, inject guidance mid-retry | `POST /goals/{id}/persistence/*` |
| Attempt history | All retry attempts with strategy, cost, outcome | `GET /goals/{id}/attempts` |

### 1.3 Real-Time Streaming
| Feature | Description |
|---------|-------------|
| Goal SSE stream | 22 event types streamed as goals execute |
| Persistence events | persistence_attempt_start, backoff_waiting, strategy_changed |
| Agent spawn events | child_agent_spawned with lineage info |
| Cost events | cost_alert when budget thresholds crossed |
| HITL events | waiting_human, human_approved, human_rejected |

---

## Part II: Agent Management

### 2.1 Agent Configuration
| Feature | Description |
|---------|-------------|
| Autonomy modes | manual, supervised, bounded-autonomous, fully-autonomous |
| Domain context | general, legal, healthcare, finance, education, ecommerce, manufacturing |
| Domain identity | Legal: bar_number/jurisdiction; Healthcare: NPI/specialty; Finance: trader_id/desk |
| System prompt | Custom system prompt for the agent's persona and expertise |
| Goal template | Pre-filled goal text template shown in UI |
| Model override | Specific model to use (bypasses model router) |
| Max iterations | Configurable per-agent iteration budget |
| Connector bindings | Which MCP servers this agent can access |
| Knowledge bindings | Which knowledge collections this agent can query |
| Policy bindings | Which governance policies apply to this agent |
| Trigger configuration | Schedule or event-based automatic goal submission |

### 2.2 Agent Versioning
| Feature | Description | API |
|---------|-------------|-----|
| Version counter | Monotonic counter incremented on every update | `agents.version` |
| Manual snapshot | Create named snapshot of current config | `POST /agents/{id}/snapshot` |
| Version history | List all snapshots with timestamps | `GET /agents/{id}/versions` |
| Rollback | Restore to any historical snapshot | `POST /agents/{id}/rollback/{snapshot_id}` |
| Clone | Create new agent from existing config | `POST /agents/{id}/clone` |
| Export | Export agent config as OpenAI/Anthropic compatible JSON | `GET /agents/{id}/export` |

### 2.3 Agent Intelligence Features
| Feature | Description | Page |
|---------|-------------|------|
| Health radar | 6-axis spider chart (Speed, Accuracy, Cost, Tool Coverage, Success Rate, Coherence) | `/agents/:id/radar` |
| Personality sliders | Visual sliders mapping to autonomy_mode, max_iterations, model_override | `/agents/:id/personality` |
| A/B experimentation | Bayesian A/B test of different agent configs via SelfOptimizerV2 | Automatic |
| Cost breakdown | Per-agent daily cost tracking | `GET /costs/per-agent` |
| Lineage graph | Visual tree of all goals this agent has executed | UI component |

### 2.4 Agent Identity & Security
| Feature | Description | API |
|---------|-------------|-----|
| Service account credentials | RS256 JWT-based identity for machine-to-machine auth | `POST /agents/{id}/credentials` |
| Credential issuance | Generates RSA-2048 keypair, stores public key in DB | Returns private key once |
| Credential revocation | Immediate revocation, JWKS cache invalidated | `DELETE /agents/{id}/credentials/{kid}` |
| JWT token exchange | 15-minute RS256 JWT for agent service calls | `POST /agents/{id}/token` |
| Scoped credentials | Define exactly which scopes each credential grants | `IssueCredentialRequest.scopes` |
| JWKS public endpoint | Public key set for external JWT verification | `GET /.well-known/jwks.json` |

---

## Part III: Knowledge & Memory

### 3.1 Knowledge Base Ingestion (11 Source Types)
| Source | Description |
|--------|-------------|
| Plain text/Markdown | Direct text ingestion with token-aware chunking |
| URL crawl | httpx web scraping with HTML→text conversion |
| PDF | pypdf page-by-page extraction |
| DOCX | python-docx full document parsing |
| GitHub API | Repository file enumeration + content fetch |
| Git clone | `depth=1` clone, full file tree walk |
| Confluence API | Space page enumeration (SecretStr API token) |
| Jira API | Issue text + comments extraction (SecretStr) |
| Slack API | Channel history ingestion |
| OpenAPI spec | Endpoint/schema/description extraction |
| Code files | Language-aware chunking with syntax preservation |

### 3.2 Knowledge Features
| Feature | Description |
|---------|-------------|
| Token-aware chunking | tiktoken cl100k_base tokenizer, max 512 tokens, 64-token overlap |
| Hybrid search | pgvector cosine + pg_trgm trigram (70/30 blend) |
| Variable dimensions | 768 (Voyage), 1024, 1536 (OpenAI), 3072 (OpenAI large) — separate HNSW tables |
| Federated search | Cross-collection search with min-max score normalization |
| Freshness TTL | Per-document expiry enforcement via daily Celery sweep |
| Semantic cache | Deduplicates LLM calls by embedding similarity (1h TTL) |
| Knowledge graph | D3 force-graph: documents→concepts→relationships |
| RLS isolation | Collection data isolated at DB layer per tenant |

### 3.3 Memory Systems
| System | Description |
|--------|-------------|
| ExecutionMemory | Per-goal short-term memory (context within a goal) |
| LongTermMemoryStore | Cross-session learnings, promoted by LearningPipeline |
| ToolReliabilityMemory | Historical tool success rates for routing decisions |
| SemanticCache | LLM response deduplication by semantic similarity |

---

## Part IV: MCP Connectors (119 Integrations)

### 4.1 Connector Categories

**CRM & Sales (9)**: Salesforce, HubSpot, Pipedrive, Close CRM, Copper, Attio, Affinity, Apollo, Gong

**Project Management (10)**: Jira, Asana, Linear, Monday.com, ClickUp, Basecamp, Wrike, Trello, Todoist, Smartsuite

**Communication (6)**: Slack, Discord, Microsoft Teams, Mattermost, Telegram, WhatsApp Business

**Developer Tools (8)**: GitHub, GitLab, Bitbucket, Jenkins, Docker, Kubernetes, Postman, Azure DevOps

**Cloud Platforms (9)**: AWS S3, AWS IAM, AWS Lambda, AWS CloudWatch, Google Cloud Storage, DigitalOcean, Heroku, Vercel, Netlify

**Databases (7)**: PostgreSQL, MongoDB, MySQL, Redis, Snowflake, Elasticsearch, Supabase

**Marketing (8)**: Mailchimp, Klaviyo, Brevo (Sendinblue), ConvertKit, MailerLite, CustomerIO, Mandrill, Zapier

**Analytics (4)**: Amplitude, Mixpanel, Google Analytics, Google Search Console

**Finance & Payments (6)**: Stripe, PayPal, QuickBooks, Xero, Chargebee, Razorpay

**HR & People (5)**: BambooHR, Rippling, Deel, Workday, Bamboo

**E-Commerce (3)**: Shopify, WooCommerce, Square

**Documentation (4)**: Confluence, Notion, WordPress, Webflow

**Search & Discovery (5)**: Brave Search, SerpAPI, Perplexity, Tavily, Firecrawl

**AI Services (4)**: OpenAI, Pinecone, LinkedIn, X/Twitter

**Advertising (4)**: Google Ads, LinkedIn Ads, Facebook Ads, TikTok

**Video & Media (3)**: YouTube, Instagram, Zoom

**Collaboration (3)**: Box, Dropbox, Microsoft OneDrive

**CRM Extended (4)**: Gorgias, Front, Intercom, Freshdesk, Freshservice

**Enterprise Software (3)**: SAP (via Workday), Salesforce (extended), Planhat

### 4.2 Connector Architecture
| Feature | Description |
|---------|-------------|
| Graceful degradation | Optional SDK deps (boto3, motor, asyncpg, snowflake) return structured error on missing |
| Credential vault | vault:// URI references in DB, never plaintext |
| PKCE OAuth flows | Per-tenant OAuth 2.0 with PKCE for user-authorized connectors |
| Tool risk classification | read/write/admin/destructive risk levels for policy routing |
| Tool capability search | Semantic search across all tools via pgvector |

---

## Part V: Governance & Policy

### 5.1 PolicyEngine
| Feature | Description |
|---------|-------------|
| Glob patterns | `jira.*` matches all Jira tools, `*.delete` matches all delete tools |
| Semantic rules | LLM-judged policies: "block actions that could cause data loss" |
| Cost threshold rules | Block tool calls when per-call or per-goal cost exceeds limit |
| Rate limit rules | Max N tool calls per minute per tenant |
| Data classification | Block/allow based on data sensitivity labels |
| Time-window restrictions | IANA timezone-aware: block after 5pm EST |
| Policy versioning | Full history with rollback — no hard deletes |
| Cross-replica sync | Redis pub/sub invalidation across all replicas |
| Policy inheritance | Sub-agents inherit parent goal's policy context |
| Fail-closed | healthcare/legal/finance: default REQUIRE_APPROVAL if no policy matches |

### 5.2 HITL Gateway
| Feature | Description |
|---------|-------------|
| Cross-replica delivery | Redis BLPOP (not asyncio.Event — survives replica failover) |
| Multi-approver threshold | Require N approvals before unblocking |
| Duplicate vote prevention | Same approver can't vote twice |
| SLA enforcement | Celery task every 5 min escalates overdue approvals |
| Email one-click links | HMAC-signed approve/reject links in notification emails |
| Batch approval | Approve up to 100 pending requests in single API call |
| DB persistence | Survives server restarts, `startup_restore()` reloads pending |
| Audit trail | Every approval/rejection recorded in audit log |

### 5.3 Budget Controls
| Feature | Description |
|---------|-------------|
| Per-goal budget | Hard stop when goal exceeds limit (atomic Redis check) |
| Per-agent daily budget | Separate limit per agent |
| Per-tenant daily budget | Tenant-wide daily cap |
| Real token tracking | Actual tokens from all 4 providers, not estimates |
| Non-LLM costs | API call fees from MCP tools (Jira, Salesforce, DALL-E) |
| EWMA anomaly detection | Z-score + hourly spike + velocity-to-exhaustion alerts |
| Cost prediction | Pre-run estimate from historical similarity |
| Budget alerts | Redis pub/sub → NotificationService at 50%/75%/90%/100% |
| Cost per agent | Breakdown by agent in dashboard |
| Model pricing table | DB-backed, Redis-cached, admin-updatable without deploy |

---

## Part VI: Security & Compliance

### 6.1 Authentication
| Feature | Description |
|---------|-------------|
| API key authentication | 256-bit entropy (secrets.token_urlsafe(32)), SHA-256 hashed |
| Keycloak OIDC | JWT validation with JWKS caching, JIT tenant provisioning |
| SAML 2.0 | python3-saml, SP-init, IdP-init, SLO, HMAC replay protection |
| SCIM 2.0 | User/group provisioning, bearer token auth, group→role mapping |
| Agent JWT tokens | RS256 service account credentials, 15-min TTL |
| SSO default role | `operator` (not `admin`) — principle of least privilege |

### 6.2 Authorization
| Feature | Description |
|---------|-------------|
| 30+ scopes | Resource:Action format (goals:submit, agents:delete, etc.) |
| Scope enforcement | ENDPOINT_SCOPES mapping on every endpoint |
| RBAC builtin roles | admin, operator, viewer, approver, agent_service |
| Custom tenant roles | Parent inheritance, configurable scope ceiling |
| ABAC conditions | resource.owner_id, request.time, user.department |
| IP allowlist | CIDR-based, Redis-cached, DB-backed |
| Redis role cache | 5-min TTL, pub/sub invalidation on role changes |

### 6.3 Guardrails
| Feature | Description |
|---------|-------------|
| 6-layer architecture | goal/plan/step/tool_args/tool_output/final |
| 100+ injection patterns | 5 categories: direct override, roleplay bypass, encoding, indirect, ChatML |
| Cloud destruction detection | kubectl delete, terraform destroy, aws s3 rm --recursive, gcloud delete |
| Database destruction | DROP TABLE, TRUNCATE, DELETE without WHERE |
| Comprehensive PII detection | SSN, IBAN, HIPAA Safe Harbor 18 identifiers, GDPR Article 9 |
| PII redaction | [REDACTED:SSN], [REDACTED:CREDIT_CARD] in output |
| LLM-as-judge | Semantic policies with fail-closed on error for high severity |
| Per-tenant config | Custom rules per tenant, domain-specific templates |
| Domain templates | HIPAA, GDPR, SOX, legal privilege, educational safety |
| Recursive arg scanning | Nested dicts/lists fully scanned, not just top-level |
| ROT13/base64 detection | Decodes and scans encoding-obfuscated injections |

### 6.4 Compliance
| Framework | Features |
|-----------|---------|
| GDPR | Unlimited async export, cascade deletion, consent records, DPA tracking |
| HIPAA | Minimum necessary enforcement, BAA verification, PHI access log, de-identification |
| SOC2 Type II | Real certification tracking (NOT hardcoded), audit controls |
| Legal holds | Prevent deletion under litigation, Redis-cached check on all DELETE endpoints |
| Data residency | Per-tenant region configuration |
| White-labeling | Custom brand, domain, colors, email domain |

### 6.5 Audit Rails
| Feature | Description |
|---------|-------------|
| Redis WAL | At-least-once delivery guarantee |
| SHA-256 hash chain | Tamper detection on event sequence |
| Monthly partitions | `audit_events PARTITION BY RANGE(created_at)` |
| Admin action audit | @audit_admin_action decorator on all destructive endpoints |
| Full argument capture | Tool arguments stored (PII stripped first) |
| SIEM integration | Splunk HEC, Elasticsearch, Datadog, CEF (ArcSight), LEEF (QRadar), Webhook |
| Legal holds | Freeze specific events from deletion |
| Chain verification | `GET /governance/audit/integrity/verify` |
| Domain formats | HIPAA audit trail, SOX change report, chain of custody |

---

## Part VII: Enterprise Features

### 7.1 Identity Provider Integration
| Feature | Description |
|---------|-------------|
| SAML 2.0 | All 3 flows, HMAC replay protection, SP metadata XML |
| SCIM 2.0 | RFC 7644 compliant, user/group CRUD, automatic role mapping |
| Custom SAML attribute mapping | email/name/groups → AgentVerse roles |
| JIT provisioning | Tenant created automatically on first SSO login |
| Role mapping from IdP groups | SCIM groups → AgentVerse custom roles |

### 7.2 Compliance Features
| Feature | Description |
|---------|-------------|
| Real compliance status | certification_type, status, expires_at from DB (not hardcoded) |
| Contract management | BAA, DPA, SLA agreement storage and tracking |
| GDPR export | Async Celery job, unlimited records, ZIP download |
| Retention sweeps | Daily Celery task respecting legal holds |
| Data deletion | 26-table cascade, hold-aware |
| HIPAA controls | PHI classification, access justification, BAA gate |

### 7.3 White-Labeling
| Feature | Description |
|---------|-------------|
| Brand name | Replace "AgentVerse" throughout UI |
| Custom logo | Base64 stored in whitelabel_configs |
| Primary color | CSS custom property override |
| Custom domain | Nginx routing for `ai.yourbrand.com` |
| Custom email domain | `noreply@yourbrand.com` for notifications |
| Hidden branding | Complete AgentVerse attribution removal |

---

## Part VIII: Marketplace & Templates

### 8.1 Marketplace Features
| Feature | Description |
|---------|-------------|
| 29 domain templates | legal, healthcare, education, finance, ecommerce, devops |
| Security review pipeline | Pattern scan + scope check + LLM safety judge before publishing |
| Atomic install | Agent + install record in ONE transaction (no ghost agents) |
| Parameter validation | JSON Schema validation before deploy |
| Ratings & reviews | 5-star system, verified-install requirement |
| Install tracking | install_count, last_installed_at |
| Full-text search | GIN index on name + description + tags |
| Semantic search | HNSW vector index for "find templates similar to X" |
| Template versioning | Full history, install preserves version snapshot |
| Domain filtering | Browse by legal, healthcare, finance, etc. |

### 8.2 Goal Templates
| Feature | Description |
|---------|-------------|
| Parameterized goals | `{{service}}` placeholder syntax in goal text |
| Auto-extraction | Parameters auto-detected from goal text |
| JSON Schema validation | Type checking on instantiation |
| Preview | Live preview of filled goal before submission |
| Copy to clipboard | Copy instantiated goal without submitting |
| Submit directly | One-click submit from template form |

---

## Part IX: Workflows & Orchestration

### 9.1 Visual Workflow Builder
| Feature | Description |
|---------|-------------|
| @xyflow/react canvas | Drag-drop, snap-to-grid, zoom/pan |
| 9 node types | trigger, tool_call, agent_step, decision, parallel, loop, human_input, condition, output |
| NL-generate | Describe workflow → auto-generate nodes |
| Node Inspector | Edit label, description, config per node |
| Save/load | JSONB definition stored in workflows table |
| Export as goal | Execute workflow by converting to natural language goal |
| Parallel branches | asyncio.gather() for parallel node groups |
| Conditional logic | Decision nodes with safe eval() condition evaluation |

### 9.2 Schedule Engine
| Feature | Description |
|---------|-------------|
| NL scheduling | "Every Monday at 9am" → TriggerSpec |
| Cron expressions | Full cron syntax via croniter |
| RedBeat distributed | No single-node scheduling failure |
| Webhook triggers | External HTTP events trigger goal submission |
| Schedule CRUD | Create, update, pause, resume, delete |

---

## Part X: Observability & Analytics

### 10.1 Dashboards & Charts
| Page | Features |
|------|---------|
| Mission Control | Live agent orbit (D3-force), activity stream, cost ticker, quick submit |
| Cost Dashboard | By model (pie), by agent (bar), by date (line), anomalies panel |
| Analytics | Goal metrics, tool usage, eval scores, time-period selector |
| Observability | Health dependency graph, Prometheus metrics, Grafana links |
| Agent Health Radar | 6-axis spider chart vs platform benchmarks |

### 10.2 Insights APIs
| Endpoint | Description |
|---------|-------------|
| `POST /insights/estimate` | Pre-run cost/time/success prediction from historical similarity |
| `GET /insights/graph/{id}` | Execution graph (nodes + edges) from event log |
| `GET /insights/analysis/{id}` | LLM-powered failure analysis + suggestions |
| `POST /insights/query` | Natural language query ("goals that failed last week") |
| `GET /insights/agent-health/{id}` | 6-axis health radar data |
| `GET /insights/benchmarks` | Anonymized platform-wide comparison percentiles |

---

## Part XI: RPA & Perception

### 11.1 RPA Operations (20 total)
| Operation | Description |
|-----------|-------------|
| rpa_open_url | Navigate to URL, wait for load |
| rpa_click | Click element by selector/text, with vision auto-healing |
| rpa_type | Fill input field |
| rpa_extract_text | Get inner_text from element (up to 5000 chars) |
| rpa_screenshot | Full-page or viewport screenshot, artifact store, vision analysis |
| rpa_wait_for_text | Wait until text appears on page |
| rpa_select_option | Select dropdown by value or label |
| rpa_upload_file | File upload to input[type=file] |
| rpa_scroll | Page or element scroll |
| rpa_hover | Mouse hover over element |
| rpa_keyboard_press | Key: Enter, Escape, Tab, ArrowDown, etc. |
| rpa_keyboard_type | Character-by-character typing |
| rpa_get_attribute | Get href, value, data-* attributes |
| rpa_evaluate_js | Execute arbitrary JavaScript |
| rpa_wait_for_selector | Wait for CSS selector |
| rpa_iframe_switch | Switch context to iframe |
| rpa_screenshot_element | Screenshot specific element only |
| rpa_drag_drop | Drag from source to target |
| rpa_new_tab | Open new browser tab |
| rpa_pdf_export | Export page as PDF → artifact store |

### 11.2 Vision & Auto-Healing
| Feature | Description |
|---------|-------------|
| Vision analysis | BrowserAgent analyzes screenshots for element identification |
| Auto-healing | On selector failure: vision re-identifies element, retries |
| Confidence threshold | Only retry with suggested selector if confidence ≥ 0.65 |
| SSE event | auto_heal_attempted event emitted with original + new selector |
| Goal↔session linking | Running goals linked to RPA sessions in Redis |

---

## Part XII: Frontend Pages (All 43+)

### Navigation Structure
```
Core
├── /dashboard          Mission Control (orbit + stream + quick submit)
├── /goals              Goals list (pagination, search, multi-agent mode)
├── /agents             Agents list (autonomy filter, NL create)

Goals (Detail Views)
├── /goals/:id          Goal pipeline + events + eval + loop control + sub-agents
├── /goals/:id/dna      Execution force-graph
├── /goals/:id/diff     Side-by-side execution comparison
├── /goals/:id/lineage  D3-hierarchy spawn tree
├── /goals/ghost-run    A/B parallel execution
├── /goals/multi-agent  Multi-agent launcher wizard
├── /goals/loop-playground  Persistence strategy configurator

Agents (Detail Views)
├── /agents/:id         Agent detail (6 tabs: overview/versions/perms/knowledge/rollout/creds)
├── /agents/:id/identity  JWT credentials management
├── /agents/:id/radar   6-axis health visualization
├── /agents/:id/personality  Slider-based config

Platform
├── /connectors         Registered connectors list
├── /connectors/catalog  Browse + register
├── /connectors/:id     Connector detail + test
├── /knowledge          Collections + ingest + search + graph
├── /schedules          Schedule CRUD
├── /collaboration      Real-time session collaboration
├── /workflow-builder   @xyflow/react canvas
├── /templates          Parameterized template library
├── /lab                Agent Lab (Pre-Flight + Live Sim + Score)
├── /simulation         Pre-flight governance check (redirects to /lab)
├── /playground         Agent playground (redirects to /lab)

Governance
├── /governance         Policies + approvals + audit + budget
├── /approvals          Pending HITL requests (SSE live updates)
├── /notifications      Notification channel config
├── /rbac               Role + scope + IP allowlist management
├── /compliance         GDPR + legal holds + consent
├── /audit              Audit explorer (virtualized, real-time stream)
├── /settings/guardrails  Guardrail center + violation feed + test playground
├── /settings/scopes    Scope explorer + access request
├── /settings/budgets   Budget manager + utilization gauges

Enterprise
├── /marketplace        Agent template marketplace
├── /observability      System health + Prometheus metrics
├── /observability/cost Cost dashboard (by model/agent/date)
├── /analytics          Analytics (goals/tools/eval/costs)
├── /enterprise         Compliance + SAML + SCIM + contracts + white-label
├── /self-improvement   A/B experiments + suggestions + optimization
├── /eval               Eval suites (redirects to /lab?tab=score)

Intelligence & Special
├── /civilization       Agent civilization map + management
├── /rpa                RPA session list + browser mirror
├── /memory             Execution + long-term memory explorer
├── /artifacts          RPA artifacts browser
├── /tools              Tool execution console
├── /integrations       Webhook + Zapier + Slack integrations
├── /training-export    Training data export for fine-tuning
├── /perception         Browser agent perception tools
├── /a2a                Agent-to-agent task management
├── /onboarding         4-step wizard (LLM → Connector → Agent → Goal)
├── /settings           Profile + API keys + LLM config
```

---

## Part XIII: UI Component Library

### Custom Components
| Component | Purpose |
|-----------|---------|
| LiveCostTicker | requestAnimationFrame real-time cost counter |
| StatusBadge | 20+ statuses with animated pulse for live states |
| ConfirmModal | Accessible destroy confirmation dialog |
| Pagination | Full page navigation with ellipsis |
| EmptyState | Illustrated empty states per context |
| Skeleton | Loading state skeletons |
| LoadingSpinner | Reusable Suspense fallback |
| Toaster | Auto-dismissing toasts (4s success, 6s error, sticky warning) |
| ThemedLineChart | CSS-var-aware Recharts line chart |
| ThemedBarChart | CSS-var-aware Recharts bar chart (horizontal + vertical) |
| ThemedRadarChart | CSS-var-aware Recharts radar chart |
| KnowledgeGraph | D3 force-graph for document relationships |
| AgentOrbitView | D3 force simulation for agent activity visualization |
| VoiceGoalInput | Web Speech API microphone for goal dictation |
| PermissionGate | React component for scope-based rendering |

---

## Part XIV: Developer Experience

### CLI Tools
| Tool | Commands |
|------|---------|
| `agentverse` (Python SDK) | run, agents, goals, approve, replay, logs, simulate, connectors, schedules, dev |
| `npm run dev` | Vite HMR dev server |
| `uv run uvicorn` | FastAPI with hot reload |
| `docker-compose up -d` | Complete 14-service stack in one command |

### Testing Infrastructure
| Suite | Count | Technology |
|-------|-------|-----------|
| Backend unit tests | 2669 | pytest + anyio |
| Frontend component tests | 258 | Vitest + Testing Library |
| E2E Playwright tests | 203+ | Playwright (29 spec files) |
| Integration tests | 18 | testcontainers (real DB + Redis) |

### SDKs
| SDK | Language | Key Features |
|-----|---------|-------------|
| agent-verse-sdk-python | Python 3.11+ | async client, CLI, mock server |
| agent-verse-sdk-typescript | TypeScript | zero runtime deps, full method coverage |
| agent-verse-github-action | Docker/Python | submit + await goals in CI |

---

## Summary Table

| Category | Feature Count |
|----------|-------------|
| Goal management features | 25 |
| Agent management features | 30 |
| Knowledge base features | 15 |
| MCP connectors | 119 |
| Governance features | 35 |
| Security/Auth features | 40 |
| Compliance features | 20 |
| Marketplace features | 15 |
| Workflow features | 12 |
| Analytics/Observability | 20 |
| RPA operations | 22 |
| Frontend pages | 43 |
| UI components | 25 |
| Developer tools | 15 |
| **TOTAL** | **~436 features** |

AgentVerse OS is not a point solution — it is a complete operating system for AI agents, with every feature designed to compose with every other feature to enable capabilities that no single tool can provide alone.
