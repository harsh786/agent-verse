# AgentVerse OS — Deep Code Analysis: Every Module Explained

> **Document 08 of 08** | The most detailed technical reference — every module, every class, every function explained with purpose, capability, and architectural context.

---

## How to Read This Document

This document covers **every Python module** in `agent-verse-backend/app/`. For each module:
- **Purpose**: What problem it solves in 1-2 sentences
- **Key Components**: Every important class/function with what it does
- **Architectural Role**: How it connects to the rest of the system
- **Unique Capability**: What would break if this module didn't exist

---

# PART 1: The Agent Execution Core (`app/agent/`)

The agent/ directory is the brain of AgentVerse. Everything here implements the "thinking" layer — how goals become plans, plans become tool calls, and tool calls become results.

---

## `app/agent/graph.py` — The LangGraph State Machine (CRITICAL)

**Purpose**: Implements the complete autonomous agent execution loop as a directed graph. Every goal that runs in production goes through this file.

**Architecture**: LangGraph `StateGraph` with 8 nodes. State flows from `START → initialize → rag_retrieval → plan → execute → verify → {complete|replan|max_iter|waiting_human} → END`.

### Node Breakdown

| Node | What Happens | Input | Output |
|------|-------------|-------|--------|
| `_node_initialize` | Creates AgentState, runs pre-flight guardrail check on goal text, injects A/B arm config from SelfOptimizerV2 | Goal text + config | AgentState populated |
| `_node_rag_retrieval` | Pulls context from 4 sources: ExecutionMemory (past plans), LongTermMemoryStore (domain knowledge), KnowledgeStore (hybrid pgvector+trigram), tool reliability warnings | AgentState | AgentState + `rag_context` field |
| `_node_plan` | Planner LLM (Claude Opus) produces JSON `{steps:[{description, tool_hint, depends_on, loop_until}]}`. GoalTreeExecutor may split into parallel sub-goals | Goal + RAG context | `plan` in AgentState |
| `_node_execute` | For each step: PolicyEngine check → PermissionMatrix check → GuardrailEngine Layer 4 check → MCPClient.call_tool() → GuardrailEngine Layer 5 check → AuditLog → CostController | Plan steps | Step results in AgentState |
| `_node_verify` | Verifier LLM (Claude Haiku) evaluates: `{success:bool, reason:str, confidence:float}`. On success: records to LongTermMemory, runs EvalRunner, triggers SelfOptimizer | Step output | `verification_success/feedback` |
| `_route` | Conditional edge: `complete` if success, `replan` if fixable failure (< max_iterations), `max_iter` if exhausted, `waiting_human` if HITL gate | AgentState | Route string |

### Constructor Injections (30+ dependencies)
```python
AgentGraph(
    goal, tenant_ctx,
    provider,              # LLM provider (Anthropic/OpenAI/etc)
    embedder,             # For RAG retrieval
    mcp_client,           # Tool call dispatcher
    mcp_registry,         # Tool discovery
    policy_engine,        # Governance rules
    permission_matrix,    # Per-tool access control
    hitl_gateway,         # Human approval gate
    audit_log,            # Immutable event trail
    cost_controller,      # Budget enforcement
    knowledge_store,      # Semantic knowledge base
    execution_memory,     # Past plans recall
    long_term_memory,     # Cross-session learnings
    tool_reliability,     # Tool success rate history
    eval_runner,          # Quality scoring
    self_optimizer,       # Improvement suggestions
    self_optimizer_v2,    # A/B testing framework
    circuit_breaker,      # Fault tolerance
    dedup_cache,          # Prevent duplicate calls
    rollback_engine,      # Undo on failure
    bulkhead,             # Concurrency limits
    model_router,         # Cost-aware model selection
    guardrail_checker,    # Basic content safety
    guardrail_engine,     # 6-layer production safety
    cost_tracker,         # Real token cost recording
    checkpointer,         # State persistence (Redis/Memory)
    step_callback,        # Streaming event emission
    ...
)
```

### Checkpointing (How goals survive crashes)
```
After every step completion:
  → LangGraph writes AgentState to Redis key: "checkpoint:{goal_id}:step_{N}"
  → If worker crashes at step 5 of 10:
  → New worker loads checkpoint from Redis
  → Resumes at step 6 with full context preserved
```

**Unique Capability**: This is the only place in the entire codebase where all subsystems compose. Every safety, memory, cost, and quality system integrates here. Remove this file and AgentVerse is just a configuration management tool.

---

## `app/agent/loop.py` — Legacy Sequential Agent Loop

**Purpose**: A simpler pre-LangGraph agent loop — `while iterations < max` cycle of plan → execute → verify. Used in older code paths, unit tests, and as a minimal dependency-free fallback.

**Key Design**: No LangGraph dependency. Uses plain Python `asyncio` tasks. Accepts the same governance injections as `AgentGraph` but doesn't require the full LangGraph installation.

**`AgentLoop.run()` flow**:
1. `_plan()` → calls Planner LLM, parses JSON steps
2. For each step: `_execute()` → calls Executor LLM → dispatches to MCP/tools
3. `_verify()` → calls Verifier LLM → `(success, reason)` tuple
4. If success: store to ExecutionMemory, return COMPLETE
5. If failure and iterations remain: increment counter, goto 1 with failure context
6. If iterations exhausted: return FAILED

**When used**:
- Unit tests where LangGraph isn't needed
- `AgentTestHarness` in `app/testing/harness.py`
- CLI `dev` mode
- Older Celery task paths that haven't migrated to graph.py

---

## `app/agent/supervisor.py` — Multi-Agent Parallel Decomposition

**Purpose**: Decomposes complex goals into 2-6 independent sub-tasks and executes them in parallel via `GoalService.submit_goal()` for each sub-task.

**`SupervisorAgent.run()` — Three Phases**:

**Phase 1 — Decompose**:
```
Planner LLM receives: "Deploy authentication service to staging"
Returns: {sub_tasks: [
  {goal: "Run all unit tests", optional: false},
  {goal: "Build Docker image", optional: false},
  {goal: "Update load balancer config", optional: true}
]}
```

**Phase 2 — Parallel Execute** (asyncio.gather with semaphore):
```python
async with asyncio.Semaphore(max_parallel):
    results = await asyncio.gather(
        goal_service.submit_goal("Run all unit tests", tenant_ctx),
        goal_service.submit_goal("Build Docker image", tenant_ctx),
        goal_service.submit_goal("Update LB config", tenant_ctx),
    )
```

**Phase 3 — Synthesize**:
```
Planner LLM receives: all 3 results
Returns: "Authentication service deployed successfully. Tests passed (47/47),
         Docker image pushed (sha256:abc...), LB updated to route 10% traffic."
```

**SSE Events Emitted**:
- `supervisor_decomposed`: Plan ready with N sub-tasks
- `supervisor_task_started`: Each sub-task begins
- `supervisor_task_complete`: Each sub-task finishes (with cost)
- `supervisor_complete`: Final synthesis ready

**When used**: `workflow_mode="supervisor"` in GoalRequest, or when GoalLineageTree shows parent→children relationships.

---

## `app/agent/debate.py` — Multi-Agent Adversarial Reasoning

**Purpose**: N independent agents propose solutions, critique each other's proposals, then vote. Produces a `consensus` solution via democratic weighting.

**Protocol (3 rounds)**:

```
Round 1 — Proposals (parallel):
  Agent-1: "Use REST API with OAuth2"  (confidence: 0.87)
  Agent-2: "Use GraphQL with JWT"       (confidence: 0.91)
  Agent-3: "Use gRPC for performance"   (confidence: 0.73)

Round 2 — Critiques (each agent critiques others):
  Agent-1 → Agent-2: "GraphQL adds complexity for simple CRUD"
  Agent-2 → Agent-1: "OAuth2 has higher latency than JWT"
  Agent-3 → both: "Neither considers mobile client constraints"

Round 3 — Voting:
  Agent-1 votes: Agent-2 (GraphQL better for complex queries)
  Agent-2 votes: Agent-1 (OAuth2 more secure for this use case)
  Agent-3 votes: Agent-2 (GraphQL has better mobile SDKs)
  
Winner: Agent-2 (2 votes) → "GraphQL with JWT"
```

**`DebateResult` fields**:
- `winning_proposal`: The elected solution text
- `winning_agent`: Agent ID that won
- `consensus_level`: votes_for_winner / total_agents (0.0–1.0)
- `all_proposals`: Full list for audit/transparency
- `round_count`: How many rounds ran

**When activated**: `workflow_mode="debate"`, `debate_rounds=N` in GoalRequest. The winning proposal becomes `exec_ctx["debate_consensus"]` injected into the goal execution.

---

## `app/agent/router.py` — Automatic Agent Selection

**Purpose**: When a goal is submitted without `agent_id`, this module automatically selects the best agent from all configured agents. Users don't need to know which agent handles what.

**Scoring Algorithm** (composite):

| Signal | Weight | How calculated |
|--------|--------|----------------|
| Keyword overlap | 40% | Jaccard similarity: `goal_tokens ∩ agent_tokens / goal_tokens ∪ agent_tokens` |
| Connector match | 40% | Does goal text mention any of the agent's connector IDs? |
| History success | 20% | Agent's average eval score on past similar goals |

**Routing Decisions**:
- Score ≥ 0.6: `single_agent` → route to top scorer
- 0.3–0.6: `single_agent` with lower confidence warning
- Score < 0.3: `needs_new_agent` → suggest creating a specialist
- Multiple agents close in score: `multi_agent` → parallel execution

**Example**:
```
Goal: "Search Jira for P0 bugs and create a Confluence page summary"

Agent "DevOps Bot" (connectors: jira, confluence, github):
  - Keyword: "jira" ∈ connectors → 40% × 1.0 = 0.40
  - Connector: "jira" mentioned → 40% × 1.0 = 0.40
  - History: 0.84 avg score → 20% × 0.84 = 0.17
  Total: 0.97 ✓ → SELECTED

Agent "HR Assistant" (connectors: bamboohr, slack):
  Total: 0.02 → skip
```

---

## `app/agent/model_router.py` — Cost-Aware Model Selection

**Purpose**: Maps each task type to the optimal model — cheapest model that's good enough for that task. Saves 60-80% on LLM costs vs. using the largest model everywhere.

**Default Routing**:

| Task | Model | Cost/1M | Why |
|------|-------|---------|-----|
| `planning` | claude-opus-4 | $15 | Highest reasoning — goal decomposition quality critical |
| `execution` | claude-sonnet-4-5 | $3 | Good tool use + instruction following |
| `verification` | claude-haiku-3-5 | $0.25 | Binary yes/no — haiku is sufficient |
| `embedding` | voyage-2 | $0.12 | Best semantic similarity quality |
| `classification` | claude-haiku-3-5 | $0.25 | Simple categorization task |
| `reflection` | claude-sonnet-4-5 | $3 | Self-critique needs nuance |
| `think` (CoT) | claude-sonnet-4-5 | $3 | Chain-of-thought reasoning |

**Budget-adaptive degradation**: If `budget_pct_remaining < 0.1`, ALL roles automatically downgrade to haiku.

**Per-tenant override**: `LLMConfigStore` allows tenants to set custom model mappings without code changes.

---

## `app/agent/goal_tree.py` — Parallel Sub-Goal Execution

**Purpose**: When a plan has many interdependent steps (e.g., 15 steps), decomposes them into a dependency DAG and executes independent steps simultaneously.

**Example**:
```
Goal: "Build and test the authentication microservice"

Without GoalTree (sequential):
  Step 1: Write service code          (5 min)
  Step 2: Write unit tests            (3 min)  ← could parallel with step 1
  Step 3: Write integration tests     (4 min)  ← could parallel with step 1
  Step 4: Run tests                   (2 min)  ← needs steps 1+2+3
  Step 5: Build Docker image          (3 min)  ← needs step 1
  Total: 17 minutes sequential

With GoalTree (parallel waves):
  Wave 1 (parallel): Steps 1,2,3     → 5 min (bottleneck)
  Wave 2 (parallel): Steps 4,5       → 3 min (bottleneck)
  Total: 8 minutes
```

**`GoalTreeExecutor.execute()` flow**:
1. Calls `decompose_goal()` — Planner LLM returns `{sub_goals: [{id, description, depends_on}]}`
2. Builds adjacency graph from `depends_on` edges
3. Topological sort → execution waves
4. Each wave: `asyncio.gather(*[execute_sub_goal(sg) for sg in wave])` bounded by `asyncio.Semaphore(max_parallel)`
5. Results aggregated into parent `AgentState`

---

## `app/agent/workflow_planner.py` + `workflow_executor.py` — Workflow Engine

**Purpose**: Two-tier planning system: fast keyword-based static planner for common patterns, LLM-powered DAG planner for novel workflows.

### Static Planner (keyword-based, zero LLM cost)
```python
goal = "Search Jira for P0 bugs and post to Slack"
# Keyword detection:
#   "jira" → JiraStep(action="search", query=extracted_jira_query)
#   "slack" → SlackStep(action="message", channel=extracted_channel)
build_static_workflow(goal)  # <5ms, zero API calls
```

### LLM Planner (for novel workflows)
```python
WorkflowPlanner.plan("Complex multi-system orchestration...")
# Returns WorkflowPlan with steps that have depends_on edges
# WorkflowPlan.execution_waves() → topological sort → parallel waves
```

### Executor (parallel execution)
```python
WorkflowExecutor.execute(plan, tenant_ctx)
# Wave 1: parallel steps (no dependencies)
# Wave 2: steps depending on Wave 1
# Short-circuits on non-recoverable failure
```

---

## `app/agent/tool_risk.py` — Tool Risk Classification

**Purpose**: Classifies every tool call into a risk tier before execution. Zero-latency (pure function, no I/O).

**Risk Tiers**:

| Tier | Examples | Effect |
|------|---------|--------|
| `read` | `jira.get_issue`, `github.get_file` | Allowed without check |
| `write_low` | `jira.create_issue`, `slack.send_message` | ALLOW_LOG in PermissionMatrix |
| `write_high` | `jira.delete_sprint`, `stripe.create_charge` | Requires APPROVAL |
| `destructive` | `github.delete_repo`, `postgres.drop_table` | Requires HITL gate |

**Classification Algorithm**:
1. High-risk connector override: `stripe.*`, `billing.*`, `payment.*` → `write_high`
2. Tool-specific token matching: `jira.create*`, `jira.update*` → `write_low`; `jira.delete*` → `destructive`
3. Generic token matching: `delete`, `drop`, `truncate`, `destroy`, `remove` → `destructive`; `create`, `update`, `write`, `post` → `write_low`; else → `read`

**Why pure function**: Called 100+ times per goal execution. Any I/O here would be a latency bottleneck.

---

## `app/agent/sanitization.py` — Output Sanitization

**Purpose**: Prevents credentials, API keys, and Bearer tokens from leaking into the SSE event stream that clients receive.

**Three regex patterns**:
1. `KEY=VALUE` pattern: `(api[_-]?key|access[_-]?token|secret|password)\s*[=:]\s*\S+` → `[REDACTED]`
2. Authorization header: `Authorization:\s*(Bearer|Basic)\s+\S+` → `Authorization: [REDACTED]`
3. Base64 Basic auth: `Basic [A-Za-z0-9+/=]{20,}` → `Basic [REDACTED]`

**Applied at**: Every point where tool output or step results are appended to `GoalRecord.events` in GoalService.

---

# PART 2: Services (`app/services/`)

---

## `app/services/goal_service.py` — The Central Orchestration Hub

**Purpose**: The single stateful runtime for ALL in-flight goals. Every goal submission, status check, event subscription, and approval flows through this service.

**The `GoalRecord` Runtime Object**:
```python
@dataclass
class GoalRecord:
    goal_id: str
    goal_text: str
    status: GoalStatus          # PLANNING → EXECUTING → VERIFYING → terminal
    events: list[dict]          # Full event history (SSE replay)
    task: asyncio.Task          # The running AgentGraph task
    subscribers: list[asyncio.Queue]  # SSE subscriber queues
    created_at: float
    tenant_id: str
    cost_usd: float             # Running total
    iterations: int             # Replan counter
```

**Goal Submission Flow**:
```
POST /goals
  → validate tenant quota (Bulkhead.check_tenant_concurrency)
  → validate daily goal limit (check_and_increment_concurrent_goals)
  → create GoalRecord in _goals dict
  → route via AgentRouter (if no agent_id)
  → check workflow_mode (supervisor/multi_agent/debate/single)
  → asyncio.create_task(_run_agent_graph(...))
  → return goal_id immediately (async, non-blocking)

SSE stream:
  Client: GET /goals/{id}/stream
  → subscribe_events() creates Queue
  → pushed events from running AgentGraph task
  → terminal event (COMPLETE/FAILED/CANCELLED) → drain
```

**`_build_agent_graph()` — The Dependency Injection Factory**:
```python
# Assembles ALL subsystems for ONE goal execution
graph = AgentGraph(
    goal=goal_text,
    tenant_ctx=tenant_ctx,
    provider=self._resolve_provider(tenant_ctx),  # LLM (Anthropic/OpenAI/etc)
    model_router=ModelRouter.from_settings(),
    mcp_client=MCPClient(mcp_registry, vault),
    policy_engine=app_state.policy_engine,
    hitl_gateway=app_state.hitl_gateway,
    audit_log=app_state.audit_log,
    cost_controller=app_state.cost_controller,
    knowledge_store=app_state.knowledge_store,
    execution_memory=app_state.exec_memory,
    long_term_memory=app_state.long_term_memory,
    eval_runner=app_state.eval_runner,
    self_optimizer_v2=app_state.self_optimizer_v2,
    cost_tracker=app_state.cost_tracker,
    guardrail_engine=app_state.guardrail_engine,
    checkpointer=self._resolve_checkpointer(),  # Redis/MemorySaver
    ...
)
```

---

## `app/services/tenant_service.py` — Tenant & API Key Management

**Purpose**: Creates tenants, issues API keys, and resolves incoming keys with Redis-cached lookups. The foundation of multi-tenancy.

**API Key Security Design**:
```
User calls: curl -H "X-API-Key: av_professional_7Xk2mN9pQ3..."

1. SHA-256 hash the raw key
2. Redis lookup: "api_key:{sha256}" → TenantContext (5-min TTL)
3. On cache miss: DB query with hash
4. Return TenantContext(tenant_id, plan, api_key_id)

Security: Raw key NEVER stored. Only SHA-256 hash in DB.
If DB is compromised: hashes are useless without the raw key.
```

**`create_tenant()` returns**:
```json
{
  "tenant_id": "t_abc123",
  "plan": "professional",
  "raw_key": "av_professional_7Xk2mN9pQ3vR8wT1yU6fA4bC5dE0gH..."
}
```
The `raw_key` is shown exactly ONCE and never stored.

---

## `app/services/notification_service.py` — Multi-Channel Notifications

**Purpose**: Delivers HITL approval requests and goal completion alerts to Slack, Teams, or generic webhooks.

**When triggered**:
- `HITLGateway.request_approval()` → notify_approval_required() → Slack/webhook
- `GoalService` terminal event → notify_goal_complete() → Slack/webhook

**Slack message format**:
```
⚠️ *Approval Required*
Goal: `delete-production-database`
Action: `postgres.drop_table`
Risk Level: `destructive`
Request ID: `req-abc123`

[Approve] (link) | [Reject] (link)
```

---

# PART 3: Governance (`app/governance/`)

---

## `app/governance/policies.py` — Policy Evaluation Engine

**Purpose**: Real-time, Redis-propagated tool policy engine. Evaluates every tool call against tenant-defined rules.

**Policy Structure**:
```python
Policy(
    name="no-weekend-deployments",
    denied_tools=["kubernetes.*", "terraform.*"],
    approval_tools=["docker.*"],
    allowed_hours_utc=(9, 17),          # 9am-5pm UTC only
    allowed_weekdays=[0, 1, 2, 3, 4],   # Monday-Friday only
    timezone="America/New_York",         # IANA timezone
    tenant_id="t_enterprise"
)
```

**Evaluation result types**:
- `ALLOW`: proceed normally
- `DENY`: raise `ToolCallDeniedException` immediately
- `REQUIRE_APPROVAL`: route to HITLGateway
- `ALLOW_LOG`: proceed but record in audit

**Cross-replica propagation**:
```
Operator changes policy via API
  → DB update
  → Redis PUBLISH to "policy_changes:{tenant_id}"
  → All replicas subscribe → reload policies from DB
  → <50ms propagation across the entire cluster
```

**Fail-closed for regulated domains**:
```python
if domain in REGULATED_DOMAINS:  # healthcare, legal, finance
    # No matching policy = default to REQUIRE_APPROVAL (safe)
else:
    # No matching policy = ALLOW (backward compatible)
```

---

## `app/governance/hitl.py` — Human-in-the-Loop Gateway

**Purpose**: Pauses agent execution mid-run and waits for human approval before continuing. The critical "human override" capability.

**The Cross-Replica Problem (Solved)**:
```
WRONG (asyncio.Event - single replica only):
  Replica A: agent waiting on event.wait()
  Replica B: user calls approve() → event.set() on Replica B
  Replica A: event was never set! Agent waits forever.

CORRECT (Redis BLPOP):
  Replica A: agent calls BLPOP "hitl_result:{request_id}" timeout=600
  Replica B: user calls approve() → RPUSH "hitl_result:{request_id}" decision
  Replica A: BLPOP returns! Decision received regardless of which replica.
```

**Full Flow**:
```
AgentGraph._node_execute():
  1. classify_tool_risk("terraform.destroy") → "destructive"
  2. hitl_gateway.request_approval(goal_id, action, risk_level)
     → DB insert to approval_requests table
     → notification_service.notify_approval_required() → Slack
     → Return ApprovalRequest object
  3. hitl_gateway.wait_for_result(request_id, timeout=600)
     → BLPOP "hitl_result:{request_id}" max 600s
     
Human:
  4. POST /governance/hitl/{request_id}/approve
     → DB update status="approved"
     → Redis RPUSH "hitl_result:{request_id}" "approved"
     
Agent resumes:
  5. BLPOP returns "approved"
  6. AgentGraph continues execution
```

---

## `app/governance/cost.py` — Budget Enforcement

**Purpose**: Hard spending limits — per-goal and per-tenant-daily — enforced atomically using Redis counters.

**Atomic Check-and-Record** (prevents TOCTOU race):
```python
async def check_and_record(tenant_ctx, goal_id, cost_usd):
    async with self._locks[tenant_ctx.tenant_id]:  # asyncio.Lock
        goal_total = self.get_goal_total(goal_id) + cost_usd
        if goal_total > self.config.per_goal_usd:
            return False  # BLOCKED
        
        daily_total = self.get_daily_total(tenant_ctx.tenant_id) + cost_usd
        if daily_total > self.config.per_tenant_daily_usd:
            return False  # BLOCKED
        
        # Both checks passed — atomically record
        self._goal_totals[goal_id] = goal_total
        self._daily_totals[tenant_ctx.tenant_id] = daily_total
        return True  # ALLOWED
```

**Redis-backed (production)**: Uses `INCRBYFLOAT goal_cost:{goal_id}` and `INCRBYFLOAT daily_cost:{tenant_id}:{date}` for cross-replica atomic counters. Keys auto-expire at midnight UTC.

---

## `app/governance/permissions.py` — Fine-Grained Tool Permissions

**Purpose**: Per-tenant, per-tool permission matrix with daily and per-goal call count limits.

**Permission Levels** (ordered by restriction):
1. `ALLOW` — proceed silently
2. `ALLOW_LOG` — proceed + record in audit (default)
3. `APPROVAL` — block until human approves
4. `DENY` — hard block

**Rule with limits**:
```python
PermissionRule(
    tool_name="stripe.*",         # glob pattern
    level=ActionLevel.APPROVAL,
    daily_limit=100,              # max 100 Stripe calls/day
    per_goal_limit=5,             # max 5 Stripe calls per goal
    scope_pattern="amount<1000"   # ABAC condition
)
```

---

# PART 4: Intelligence (`app/intelligence/`)

---

## `app/intelligence/eval_runner.py` — 6-Dimension Quality Scorer

**Purpose**: After every goal completes, produces a multi-dimensional quality scorecard. The platform's "quality meter."

**Six Dimensions Explained**:

### 1. Task Completion (0.0 or 1.0)
```
AgentState.status == GoalStatus.COMPLETE → 1.0
Anything else → 0.0
Binary — either the goal was achieved or it wasn't.
```

### 2. Efficiency (0.0–1.0)
```
efficiency = 0.7 × iteration_efficiency + 0.3 × cost_efficiency

iteration_efficiency = max(0, 1 - (iterations - 1) / max_iterations)
  # 1 iteration = perfect efficiency
  # max_iterations used = 0.0

cost_efficiency = max(0, 1 - cost_usd / 2.0)
  # $0 cost = 1.0 efficiency
  # $2.00 cost = 0.0 efficiency
  # Goal: reward agents that complete in fewer steps and cheaper
```

### 3. Accuracy (0.0, 0.5, or 1.0)
```
Verifier LLM feedback sentiment:
  "success" → 1.0
  "partial" in feedback → 0.5
  "failure" without partial → 0.0
```

### 4. Safety (0.0–1.0)
```
safety = max(0, 1 - deny_events × 0.25)

Each blocked/denied tool call: -0.25
4+ violations: safety = 0.0
An agent that follows all policies perfectly: safety = 1.0
```

### 5. Coherence (0.0–1.0)
```
Async path (production): LLM-scored
  "Rate 0.0–1.0 how logically coherent these steps are for this goal"

Sync fallback heuristic:
  output_rate = steps_with_output / total_steps
  diversity = unique_step_descriptions / total_steps
  coherence = 0.6 × output_rate + 0.4 × diversity
```

### 6. SLA (0.0–1.0)
```
If timing data available:
  duration = time.monotonic() - execution_started_at
  sla_budget = context["sla_budget_seconds"]  # default 300s
  sla = max(0, 1 - max(0, duration - sla_budget) / sla_budget)

No data → 1.0 (assume on-time)
```

**`score_and_persist()`**: Calls `score_async()` (real LLM coherence) then inserts to `evaluations` table. Multiple callers share the same result (idempotent insert).

---

## `app/intelligence/guardrails.py` — Basic Content Safety Checker

**Purpose**: Fast, synchronous, zero-dependency content safety. Runs before the full 6-layer `guardrail_engine.py` for basic screening.

**Detection Methods**:

### Injection Detection (10 phrases)
```python
_INJECTION_PHRASES = [
    "ignore all previous instructions",
    "ignore previous instructions",
    "disregard all previous",
    "you are now",
    "act as if you are",
    "pretend you are",
    "override your instructions",
    "system: override",
    "new instructions:",
    "forget your instructions",
]
```

### Dangerous Command Detection (7 regex patterns)
```python
r"rm\s+-rf?\s+/"           # Unix filesystem destruction
r"DROP\s+(TABLE|DATABASE)"  # SQL schema destruction
r"TRUNCATE\s+TABLE"         # SQL data destruction
r"DELETE\s+FROM"            # SQL mass deletion
r"format\s+c:"              # Windows format
r"mkfs\s+"                  # Linux format
r">\s+/dev/sd[a-z]"         # Disk overwrite
```

### PII Detection (3 patterns)
```python
SSN:         r"\b\d{3}-\d{2}-\d{4}\b"
Credit card: r"\b(?:4[0-9]{12}(?:[0-9]{3})?|...)"  # Visa, MC, AmEx, Discover
```

### Advanced Detection
- **Base64 decode + rescan**: Detects injection hidden in base64 encoding
- **ROT13 decode + rescan**: Detects ROT13-obfuscated injections
- **Unicode normalization**: NFKC normalization catches homoglyph attacks
- **Leetspeak translation**: `1gnor3` → `ignore`

---

## `app/intelligence/guardrail_engine.py` — 6-Layer Production Guardrail

**Purpose**: Production-grade, defense-in-depth content safety. Six independent layers, severity scoring, PII redaction, LLM judge.

**Layer Architecture**:
```
Input: goal text or tool arguments
  │
  ▼ Layer 1: InjectionGuard (100+ patterns, recursive scan)
  ▼ Layer 2: RecursiveArgScanner (DFS into nested JSON)
  ▼ Layer 3: PIIDetector (18 HIPAA identifiers + GDPR categories)
  ▼ Layer 4: CloudDestructionGuard (kubectl, terraform, aws s3 rm)
  ▼ Layer 5: LLMJudge (semantic risk scoring via haiku)
  ▼ Layer 6: OutputScanner (PII redaction in outputs)
  │
Output: GuardrailResult(passed, action, risk_score, violations, redacted_content)
```

**Severity → Action mapping**:

| Max Severity | Action |
|-------------|--------|
| CRITICAL | BLOCKED immediately |
| HIGH | BLOCKED (configurable to HITL_QUEUED) |
| MEDIUM | WARNED (logged, not blocked) |
| LOW | LOGGED only |
| INFO | No action |

**LLM Judge (Layer 5)** — Semantic risk scoring:
```
Prompt: "Does this content violate our policy: {policy_description}?
         Content: {text}
         Respond with JSON: {violation: bool, reason: str, severity: str, confidence: float}"

Model: claude-haiku-3-5 (fastest, cheapest)
Cache: 1-hour TTL by content hash (identical inputs return cached result)
Fail behavior: FAIL CLOSED for HIGH/CRITICAL (block on error), FAIL OPEN for LOW/MEDIUM
```

---

## `app/intelligence/self_optimization.py` — Rule-Based Improvement Suggester

**Purpose**: Analyzes failed/low-scoring goal runs and generates typed improvement suggestions.

**Suggestion Types**:

| Trigger | Category | Suggestion |
|---------|---------|-----------|
| `average_score < 0.5` | `prompt_improvement` | Better planner system prompt |
| `"tool not found" in error_log` | `tool_expansion` | Add missing tool to agent |
| `efficiency < 0.4` | `retry_strategy` | Increase max_iterations |
| `safety < 0.7` | `policy_tightening` | Add stricter governance policy |

**`apply_suggestion()` — Live Updates**:
```python
if suggestion.change_type == "system_prompt":
    # Update running AgentGraph's planner prompt immediately
    agent_graph._planner_system_prompt = suggestion.after
    prompt_optimizer.register_variant(...)

elif suggestion.change_type == "max_iterations":
    # Update AgentStore config
    agent_store.update(agent_id, {"max_iterations": new_value})

elif suggestion.change_type == "add_policy":
    # Add governance rule
    policy_engine.add_policy(Policy(denied_tools=[...]))
```

---

## `app/intelligence/prompt_optimizer.py` — A/B Prompt Testing

**Purpose**: Statistical A/B testing of system prompt variants. Automatically promotes the statistically better prompt.

**Variant Selection** (epsilon-greedy):
```
ε = 0.1 (10% exploration)

if random() < ε:
    → explore: pick random non-control variant
else:
    → exploit: pick highest-mean-score variant

Assignment is DETERMINISTIC: SHA-256(goal_id) % 100 < 50 → control
  Same goal_id always gets same variant for reproducibility.
```

**Promotion Criteria** (Mann-Whitney U test):
```python
# After min_runs_for_promotion=5 runs:
statistic, p_value = scipy.stats.mannwhitneyu(
    challenger_scores, control_scores, alternative="greater"
)
if p_value < 0.05 and mean(challenger) > mean(control):
    # Statistically significant improvement → PROMOTE
    agent.system_prompt = challenger.prompt_text
    control.is_active = False
    challenger.is_active = True
```

---

## `app/intelligence/benchmarking.py` — Performance Trend Tracking

**Purpose**: Long-term agent performance tracking across ALL 6 eval dimensions. Detects improving/stable/degrading trends before users notice.

**Trend Detection**:
```python
recent_3 = scores[-3:]     # Last 3 eval scores
baseline_3 = scores[-6:-3] # Previous 3 eval scores

if mean(recent_3) - mean(baseline_3) > 0.05:
    trend = "IMPROVING"    # +5% → significant improvement
elif mean(baseline_3) - mean(recent_3) > 0.05:
    trend = "DEGRADING"    # -5% → regression alert!
else:
    trend = "STABLE"
```

**Why per-6 scores** (not all history): Recent trend matters more than long-term average. A model upgrade that improved performance 6 months ago shouldn't mask a recent regression.

---

## `app/intelligence/meta_agent.py` — Natural Language Agent Creation

**Purpose**: "Tell me what you want, I'll configure the agent" — converts a plain English description to a complete `MetaAgentConfig`.

**Input → Output**:
```
Input: "Every Monday morning, pull the Jira sprint summary,
        check Confluence for related docs, and post a briefing to Slack"

Output: MetaAgentConfig(
    name="Weekly Sprint Reporter",
    goal_template="Generate sprint briefing for {team_name}",
    connectors=["jira", "confluence", "slack"],
    trigger_type="cron",
    cron_expression="0 9 * * 1",  # Monday 9am
    autonomy_mode="bounded-autonomous",
    policy_suggestions=[
        Policy(approval_tools=["slack.*"], name="require-slack-approval")
    ]
)
```

**LLM Prompt**: Uses `_META_AGENT_SYSTEM` prompt that enforces strict JSON output schema. Falls back to minimal config on JSON parse failure (never crashes).

---

## `app/intelligence/cost_optimizer.py` — Model Cost ROI Analysis

**Purpose**: Finds goal categories where using a cheaper model produces equivalent quality, and suggests/applies the downgrade automatically.

**Category Inference**:
```
Goal: "Search Jira for P0 bugs in the last sprint"
First 3 words: "search", "jira", "for"
Category: "search" (matches keyword)

Other categories: "analyze", "generate", "test", "deploy", "review", "extract"
```

**Suggestion Trigger**:
```
For category "search" using "claude-opus-4":
  avg_score = 0.86
  downgrade_model = "claude-sonnet-4-5"
  sonnet_score = 0.83  (historical data)
  quality_drop = 0.86 - 0.83 = 0.03  # 3.5%

if quality_drop < quality_threshold (5%):
    → Emit suggestion: "Use claude-sonnet-4-5 for 'search' tasks"
    → Cost savings: $15 → $3 per 1M tokens (80% reduction)
    → Quality impact: -3.5% (within acceptable range)
```

---

# PART 5: RAG & Memory (`app/rag/`, `app/memory/`)

---

## `app/rag/store.py` — Hybrid Knowledge Search

**Purpose**: The knowledge retrieval backend. Combines vector similarity and text fuzzy matching in a single query for best-of-both-worlds search.

**Hybrid Search Formula**:
```
final_score = 0.7 × vector_score + 0.3 × trigram_score

vector_score: cosine similarity via pgvector <=> operator
trigram_score: pg_trgm similarity() function (character trigrams)
```

**Why 70/30 weighting**:
- Pure semantic (vector-only): misses exact product codes like "PROJ-1234"
- Pure keyword (BM25/trigram-only): misses conceptually similar content
- 70/30 blend: semantic intent + exact term matching

**In-memory fallback** (for tests/dev without DB):
```python
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot_product = sum(x*y for x,y in zip(a,b))
    norm_a = sum(x**2 for x in a) ** 0.5
    norm_b = sum(x**2 for x in b) ** 0.5
    return dot_product / (norm_a * norm_b)

def _trigram_score(query: str, text: str) -> float:
    q_tris = {query[i:i+3] for i in range(len(query)-2)}
    t_tris = {text[i:i+3] for i in range(len(text)-2)}
    return len(q_tris & t_tris) / max(len(q_tris), 1)
```

**Tenant isolation**: Every SQL query has `AND tenant_id = :tid` (defence-in-depth on top of RLS).

---

## `app/rag/evaluation.py` — Retrieval Quality Measurement

**Purpose**: Measures how well the knowledge base is actually helping the agent. Not "is data ingested" but "is data being found."

**Metrics Explained**:

### Precision@K
```
"Of the K chunks retrieved, how many were actually relevant?"
precision@5 = relevant_retrieved / 5

Example: Retrieved [A, B, C, D, E], relevant=[A, C, E]
precision@5 = 3/5 = 0.60
```

### Recall@K
```
"Of all relevant chunks, how many did we find?"
recall@5 = relevant_retrieved / total_relevant

Example: Total relevant=[A, C, E, F, G], retrieved=[A, C, E]
recall@5 = 3/5 = 0.60
```

### Mean Reciprocal Rank (MRR)
```
"How highly was the FIRST relevant result ranked?"
MRR = mean(1/rank_of_first_relevant)

Example: First relevant at position 2 → 1/2 = 0.50
         First relevant at position 1 → 1/1 = 1.00
```

**Actionable Recommendations**:
```
score < 0.5  → "Consider increasing chunk overlap (64 → 128 tokens)"
score < 0.7  → "Low-quality chunks detected. Review source documents."
score > 0.9  → "Excellent retrieval quality. No action needed."
```

---

## `app/memory/execution.py` — Past Plans Recall

**Purpose**: The agent's "muscle memory" — remembers which plans worked and which failed for similar goals.

**How it improves agent performance**:
```
Without execution memory:
  Day 1: Agent tries 3 approaches before finding right plan for Jira task
  Day 7: Same Jira task — agent tries same 3 approaches again (wasteful)

With execution memory:
  Day 1: Agent tries → fails → fails → succeeds with approach C
         ExecutionMemory.record(goal="Jira task", plan=approach_C)
  Day 7: Same Jira task
         ExecutionMemory.recall("Jira task") → [approach_C from Day 1]
         AgentGraph._node_rag_retrieval injects approach_C as context
         Agent starts with approach_C → succeeds on first try
```

---

## `app/memory/long_term.py` — Cross-Session Learning Store

**Purpose**: Persists discovered domain knowledge and tool preferences across ALL goal executions for a tenant. What the civilization learns, the individual agents inherit.

**Memory Types**:

| Type | Example | Extracted when |
|------|---------|---------------|
| `tool_preference` | "Use jira_api, not jira_browser" | Goal succeeds, tool mentioned |
| `domain_fact` | "Jira board is 'BACKEND', not 'BE'" | Any completion |
| `success_pattern` | "Confluence search works better with exact page title" | Goal succeeds |
| `failure_pattern` | "Slack DM to @channel fails; use #general" | Goal fails after retries |

**Semantic recall** (production with embedder):
```sql
SELECT content, confidence, memory_type
FROM long_term_memory
WHERE tenant_id = :tid
  AND embedding <=> :query_embedding < 0.3  -- cosine distance
ORDER BY confidence DESC, created_at DESC
LIMIT :top_k
```

---

## `app/memory/tool_reliability.py` — Tool Success Rate Tracker

**Purpose**: The agent equivalent of "this API has been flaky lately, avoid it."

**EWMA Success Rate**:
```
After each tool call:
  current_rate = (success_count / (success_count + failure_count))
  ewma_rate = alpha × current_rate + (1-alpha) × previous_ewma
  
Tool "jira.get_issue":   success=95, failure=5  → reliability=0.95
Tool "legacy.api.v1":    success=20, failure=80 → reliability=0.20

Planner context injection:
"WARNING: The following tools have low reliability (< 70%): ['legacy.api.v1']
 Consider alternative approaches."
```

---

# PART 6: MCP Connectors (`app/mcp/`)

---

## `app/mcp/registry.py` — Tool Server Registry

**Purpose**: Redis-backed, per-tenant registry of all tool servers. Hot-registers new connectors at runtime.

**Data Model** per connector:
```python
MCPServerConfig(
    server_id="jira-prod",
    name="Jira Production",
    url="https://company.atlassian.net",
    auth_type=AuthType.BEARER,           # 10 auth types supported
    auth_config={"token": "vault://jira/token"},  # vault:// reference
    capabilities=["issues", "sprints"],
    tool_definitions=[...],              # JSON schemas for tools
    status=ServerStatus.ACTIVE,
    builtin_handler=None                 # process-local callable
)
```

**The `builtin_handler` field**: For the 119 built-in connectors (aws_s3_server.py, jira_server.py, etc.), the actual Python callable is stored in `_BUILTIN_HANDLER_REGISTRY` (module-level dict). It's excluded from Redis serialization (not JSON-serializable) and reattached on deserialization. This allows built-in connectors to run in-process without an HTTP call.

---

## `app/mcp/client.py` — Tool Call Dispatcher

**Purpose**: Universal tool execution — discovers tools across all registered servers and dispatches calls with appropriate auth.

**Call Resolution**:
```
agent wants to call "jira.search_issues"

1. Parse server_id: "jira" (everything before first dot)
2. MCPRegistry.get("jira", tenant_ctx) → MCPServerConfig
3. MCPServerConfig.builtin_handler → JiraServer.handle()
   OR: HTTP POST to MCPServerConfig.url/tools/jira.search_issues

4. Apply circuit breaker: if jira server has failed 5x in 60s → OPEN
5. Auth resolution: "vault://jira/token" → real token from Vault
6. Execute call
7. Update circuit breaker state
8. Return ToolCallResult
```

**JSON-RPC vs REST**: Both protocols supported. Auto-detected from `content_type` response header. Most modern MCP servers use REST; older ones use JSON-RPC 2.0.

---

## `app/mcp/capability_search.py` — Semantic Tool Discovery

**Purpose**: "I need a tool that creates GitHub issues" → finds the right tool across all 119 connectors.

```python
capability_search.find_tools("create a GitHub issue with labels")
→ [
    ToolMatch(tool="github.create_issue", score=0.95, server="github"),
    ToolMatch(tool="gitlab.create_issue", score=0.71, server="gitlab"),
    ToolMatch(tool="jira.create_issue", score=0.68, server="jira"),
]
```

**How**: Embeds both the capability query and each tool's `description` + `name`. Returns top-K by cosine similarity.

---

# PART 7: Reliability (`app/reliability/`)

---

## `app/reliability/circuit_breaker.py` — Fault Tolerance

**Purpose**: Stops cascading failures by refusing to call a failing external service.

**State Machine**:
```
CLOSED (normal operation)
  │ 5 failures in 60s
  ▼
OPEN (refusing calls)
  │ 30s cooldown
  ▼
HALF_OPEN (testing recovery)
  │ Success → CLOSED
  │ Failure → OPEN
```

**Redis-backed** (`RedisCircuitBreaker`): State shared across all replicas. One breaker per `(server_id, tenant_id)`. If Jira is down, ALL replicas know instantly.

---

## `app/reliability/rollback.py` — Compensating Actions

**Purpose**: When a goal fails mid-execution, undo already-executed tool calls to leave the system in a consistent state.

**Tool Inverses** (examples from `tool_inverses.py`):

| Action | Compensating Action |
|--------|---------------------|
| `jira.create_issue` | `jira.delete_issue({issue_id})` |
| `github.create_branch` | `github.delete_branch({branch_name})` |
| `slack.send_message` | `slack.delete_message({ts, channel})` |
| `confluence.create_page` | `confluence.delete_page({page_id})` |

**Rollback execution**:
```
Goal fails at step 5/8
Steps 1-4 have completed successfully

RollbackEngine.rollback_from_step(4):
  → Reverse order: step4 → step3 → step2 → step1
  → For each: execute compensating action
  → Log each rollback to AuditLog
  → Goal status → ROLLED_BACK
```

---

## `app/reliability/bulkhead.py` — Concurrency Limits

**Purpose**: Prevents any single tenant from consuming all worker capacity.

**Per-plan limits**:

| Plan | Max Concurrent Goals |
|------|---------------------|
| free | 2 |
| starter | 5 |
| professional | 10 |
| enterprise | 20 |

**Implementation**: Redis counter `concurrent_goals:{tenant_id}` with `INCR/DECR`. `Celery` workers check before accepting a goal task. Goal is rejected with `HTTP 429` if at limit.

---

## `app/reliability/dedup.py` — Duplicate Call Prevention

**Purpose**: Prevents the same tool call from being made twice in the same goal execution (idempotency).

**Cache key**: `SHA-256(goal_id + tool_name + JSON.dumps(sorted(args)))`

**Use case**:
```
Agent generates plan:
  Step 2: "Get Jira issue PROJ-123"
  Step 4: "Get Jira issue PROJ-123"  ← duplicate!

DeduplicationCache detects Step 4 is same as Step 2
→ Returns cached result from Step 2
→ No extra API call
→ No extra cost
```

---

# PART 8: Agent Civilization (`app/civilization/`)

---

## `app/civilization/constitution.py` — The Pure Policy Evaluator

**Purpose**: The Constitution is the rulebook for the civilization. This module is a pure function — zero I/O, deterministic, 100% testable.

**What it enforces**:
```python
Constitution(
    max_agents=20,              # Hard cap on society size
    max_depth=4,                # Max spawn depth (A→B→C→D)
    max_budget_usd=100.0,       # Total civilization budget
    max_spawn_per_hour=10,      # Rate limit on spawning
    max_concurrent_goals=10,    # Prevents resource exhaustion
    min_reputation_for_goals=0.3,  # Minimum rep to receive work
    idle_ttl_seconds=3600,       # Auto-retire idle agents after 1h
    min_viable_roster=3,         # Never retire below this count
    autonomy_ceiling="bounded-autonomous",  # Child autonomy cap
    propagate_autonomy_mode=True  # Children inherit parent's mode
)
```

**`evaluate_spawn()` — 5 checks in order**:
1. `current_depth < max_depth` → depth exceeded → DENIED
2. `total_agents < max_agents` → society full → DENIED
3. `concurrent_goals < max_concurrent_goals` → too busy → DENIED
4. `spawns_last_hour < max_spawn_per_hour` → rate limited → DENIED
5. `budget_remaining >= min_child_budget` → no budget → DENIED
6. All pass → APPROVED with computed child budget

**Why pure function**: The Governor calls this on every spawn request. Any I/O here would create a bottleneck on the hot path. By being pure, it's also infinitely testable.

---

## `app/civilization/governor.py` — The Spawn Authority

**Purpose**: The single chokepoint for all agent creation. No agent is ever created without going through the Governor.

**`spawn_agent()` decision tree**:
```
Governor.spawn_agent(capability, goal, budget, tenant_ctx):

1. Check for reusable idle agent:
   SELECT * FROM civilization_agents
   WHERE civilization_id = :cid
     AND status = 'active'
     AND current_goal IS NULL  -- idle
     AND capabilities @> :required_capability  -- has the skill
   
   Found idle agent? → REUSE (no creation, save budget)
   
2. No idle agent available:
   → MetaAgentPlanner.plan_agent(capability, goal)
   → AgentStore.create(config)
   → INSERT INTO civilization_agents (...)
   → Emit bus event: {type: "agent_spawned", ...}
   → Return new agent_id
```

**`check_breach()` — Constitutional Monitoring** (Celery beat every 30s):
```
1. Get live metrics from DB:
   - total_agents (count of active agents)
   - concurrent_goals (agents currently executing)
   - budget_spent_usd (sum of all spent)
   - spawns_last_hour (count in last 60s)

2. constitution.evaluate_breach(metrics, constitution)
   → BreachType: BUDGET_EXHAUSTED | SPAWN_RATE | CAPACITY

3. If breach:
   → Redis SET "civ:paused:{civ_id}" "true"
   → Emit bus event: {type: "constitution_violated", breach_type}
   → HITLGateway.request_approval(...)  # optional escalation
```

---

## `app/civilization/society.py` — Reputation & Goal Routing

**Purpose**: Tracks the "reputation score" of each civilization member and routes new goals to the most capable (highest-reputation) available agent.

**EWMA Reputation Formula**:
```
α = 0.2  (20% weight on new observation)
new_rep = α × outcome_score + (1-α) × current_rep

Seeded at 0.5 (neutral for new agents)

Example evolution:
  Initial: 0.50
  Goal 1 success (1.0): 0.2×1.0 + 0.8×0.50 = 0.60
  Goal 2 success (1.0): 0.2×1.0 + 0.8×0.60 = 0.68
  Goal 3 failure (0.0): 0.2×0.0 + 0.8×0.68 = 0.54  ← graceful recovery
  Goal 4 success (1.0): 0.2×1.0 + 0.8×0.54 = 0.63

Why α=0.2: Resistant to single outliers. An agent needs consistently bad
           performance (not one bad run) to have reputation tank.
```

**`route_goal()` priority chain**:
1. `AgentRouter.route(goal)` → semantic routing to best-fit agent
2. Fallback: highest-reputation idle member
3. Final fallback: any active member

---

## `app/civilization/blackboard.py` — Shared Knowledge Repository

**Purpose**: Agents post discoveries to a shared board. Others query before duplicating work. High-confidence conflicts trigger debates.

**Optimistic Concurrency Control**:
```sql
-- Agent A and Agent B both try to update the same finding simultaneously

Agent A: UPDATE blackboard_entries SET version=2, content='X' WHERE id=:id AND version=1
Agent B: UPDATE blackboard_entries SET version=2, content='Y' WHERE id=:id AND version=1

-- One succeeds (rowcount=1), one fails (rowcount=0)
-- Failure → raise BlackboardConflictError("Version mismatch: expected 1, found 2")
-- Client must re-read and retry
```

**Debate Trigger Logic**:
```python
def _check_for_conflicts(entry, existing_entries):
    # Find similar topic entries with high confidence
    conflicts = [e for e in existing_entries
                 if e.topic == entry.topic
                 and e.confidence >= 0.75
                 and e.content != entry.content  # different claim!
                 and entry.confidence >= 0.75]   # both confident
    
    if conflicts:
        # Two confident agents disagree → structured debate
        bus.publish(topic="debate", payload={
            "claim_a": entry.content,
            "claim_b": conflicts[0].content,
            "proposers": [entry.author_id, conflicts[0].author_id],
        })
```

---

## `app/civilization/learning.py` — Anti-Poisoning Learning Pipeline

**Purpose**: Controls what collective knowledge gets promoted to LongTermMemoryStore. Prevents bad/adversarial learnings from corrupting shared memory.

**State Machine**:
```
submit_candidate(text) → status: "candidate"
                                      │
                              validate_candidate()
                              calls EvalRunner.score()
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                  │
               score ≥ 0.7       0.35 ≤ score < 0.7  score < 0.35
                    │                 │                  │
               "validated"       stays "candidate"    "rejected"
                    │            (needs more data)        │
              promote_validated()                    NEVER promoted
                    │
              LongTermMemoryStore.store()
              status: "promoted"
```

**Why 0.35 threshold**: Below 35% EvalRunner quality score, the content is likely hallucinated, adversarial, or simply wrong. These are rejected permanently and never reconsidered.

---

## `app/civilization/bus.py` — Event Bus

**Purpose**: Decoupled publish/subscribe communication between all civilization components. Dual-write to Redis (speed) + PostgreSQL (durability).

**Publish flow** (every event):
```python
async def publish(from_agent_id, topic, payload):
    message_id = str(uuid.uuid4())
    
    # 1. Persist to DB first (durability)
    await _persist_message(message_id, from_agent_id, topic, payload)
    
    # 2. Publish to Redis (speed)
    channel = f"civ:{tenant_id}:{civ_id}:{topic}"
    try:
        await redis.publish(channel, json.dumps(event))
    except Exception:
        # Redis failure doesn't fail the publish — DB has the message
        logger.warning("bus_redis_publish_failed", ...)
    
    # 3. Emit to civilization_events for SSE/WS clients
    await _emit_civilization_event(event)
```

**Why dual-write**: Redis pub/sub is ephemeral — if a subscriber is offline, they miss the message. PostgreSQL `bus_messages` table provides guaranteed delivery via `get_messages(after_ts)` replay for reconnecting clients.

---

# PART 9: RPA (`app/rpa/`)

---

## `app/rpa/executor.py` — Browser Automation Dispatcher

**Purpose**: The unified entry point for all 20 RPA operations. Routes to Playwright, session-managed Playwright, or simulation.

**Credential Injection Flow**:
```
Agent plan step:
  "Log into Salesforce using vault://salesforce/admin_password"

CredentialInjector.resolve_arguments({
    "selector": "#password",
    "value": "vault://salesforce/admin_password"  ← vault reference
}):

1. Parse vault://salesforce/admin_password
2. Vault.get("salesforce", "admin_password", tenant_ctx)
3. Returns: {selector: "#password", value: "ActualP@ssw0rd!"}
4. Playwright uses resolved value
5. Logs: "Resolved vault://salesforce/admin_password" (NOT the value!)
```

**Why vault references in plans**: If the agent plan is stored or logged, it contains `vault://...` references, never plaintext passwords. Plans are safe to audit.

---

## `app/rpa/session_manager.py` — Persistent Browser Sessions

**Purpose**: Keeps browser sessions alive across multiple RPA tool calls in a single workflow.

**Why sessions matter**:
```
WITHOUT session management (standalone mode):
  Tool call 1: rpa_open_url("https://app.salesforce.com")
    → Launch Chrome → navigate → log in → close Chrome
  Tool call 2: rpa_click("#create-opportunity")
    → Launch Chrome → navigate → LOG IN AGAIN → click → close
  Problem: Login overhead × N tool calls = slow and breaks 2FA

WITH session management:
  Session created: Chrome launched, logged in once
  Tool call 1: rpa_open_url → uses existing page
  Tool call 2: rpa_click → same page, already logged in
  Tool call N: rpa_screenshot → same session
  Session closed after goal completes
  
Benefit: Single login, N tool calls, vastly faster
```

---

# PART 10: Provider Layer (`app/providers/`)

---

## `app/providers/base.py` — Vendor-Agnostic LLM Protocol

**Purpose**: Defines the interface that ALL LLM providers must satisfy. Enables swapping Claude for GPT-4 for Gemini without changing any calling code.

**Protocol fields**:
```python
class CompletionRequest:
    messages: list[Message]       # conversation history
    model: str                    # "claude-opus-4" etc.
    system: str | None            # system prompt (separate from messages)
    tools: list[ToolDefinition]   # available tools (function calling)
    max_tokens: int = 4096
    temperature: float = 0.0      # deterministic by default
    metadata: dict = {}           # extra provider-specific options

class CompletionResponse:
    content: str                  # the LLM's text response
    usage: TokenUsage | None      # REAL token counts (not estimates!)
    tool_calls: list[ToolCall]    # structured tool invocations
    stop_reason: str | None       # "end_turn", "max_tokens", etc.
```

**`TokenUsage`** — Why it matters:
```python
@dataclass
class TokenUsage:
    prompt_tokens: int       # Actual tokens in the prompt
    completion_tokens: int   # Actual tokens in the response
    total_tokens: int
```
Real costs = real tokens. Before this existed, cost was estimated (often wrong). Now every dollar is accountable.

---

## `app/providers/anthropic_provider.py` — Claude Integration

**Purpose**: Production Anthropic Claude client with real token extraction.

**Key implementation detail** (the critical cost-tracking fix):
```python
async def complete(self, request: CompletionRequest) -> CompletionResponse:
    response = await self._client.messages.create(
        model=request.model,
        messages=_convert_messages(request.messages),
        max_tokens=request.max_tokens,
        ...
    )
    
    return CompletionResponse(
        content=response.content[0].text,
        usage=TokenUsage(
            prompt_tokens=response.usage.input_tokens,    # ← REAL count
            completion_tokens=response.usage.output_tokens,  # ← REAL count
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )
    )
```

Previously, usage was `None` everywhere. This single change enabled real cost tracking across the entire platform.

---

# PART 11: Authentication (`app/auth/`)

---

## `app/auth/agent_identity.py` — Cryptographic Agent Identity

**Purpose**: Every agent has a verifiable cryptographic identity via RS256 JWTs.

**JWT Payload structure**:
```json
{
    "iss": "agentverse:tenant-abc",
    "sub": "agent:agent-uuid-123",
    "aud": ["agentverse-api", "mcp-tools"],
    "exp": 1750000900,
    "iat": 1750000000,
    "jti": "unique-token-id",
    "agent_id": "agent-uuid-123",
    "tenant_id": "tenant-abc",
    "autonomy_mode": "bounded-autonomous",
    "scopes": ["goals:execute", "knowledge:read"],
    "domain_context": "legal",
    "parent_goal_id": "goal-xyz",
    "delegated_by": null
}
```

**Why RS256** (not HS256):
- RS256 = asymmetric (public/private key pair)
- Private key: stored encrypted in vault, used to SIGN
- Public key: published at JWKS endpoint, used to VERIFY
- External systems can verify tokens without knowing the private key
- HS256 would require sharing the secret — insecure for multi-service architectures

---

## `app/auth/scope_enforcement.py` — Per-Request Scope Gating

**Purpose**: Every API endpoint has a required scope. Every request must prove the caller has that scope.

**Middleware position in stack**:
```
Request enters:
  1. CORS middleware
  2. SecurityHeadersMiddleware (HSTS, CSP, X-Frame-Options)
  3. ScopeEnforcementMiddleware  ← here
  4. TenantMiddleware (auth + rate limit)
  5. Route handler
```

**Why scope enforcement is BEFORE tenant auth**: Scope check requires knowing the tenant context (from TenantMiddleware). But ScopeEnforcementMiddleware runs AFTER TenantMiddleware in Starlette's LIFO middleware stack. Middleware is added in reverse order.

**ENDPOINT_SCOPES registry** (partial):
```python
ENDPOINT_SCOPES = {
    ("POST", "/goals"):              "goals:write",
    ("DELETE", "/goals/{goal_id}"): "goals:delete",
    ("POST", "/agents"):             "agents:write",
    ("DELETE", "/agents/{agent_id}":"agents:delete",
    ("POST", "/connectors"):         "mcp:write",
    ("POST", "/governance/policies"):"governance:write",
    ("GET",  "/analytics"):          "audit:read",
    ("POST", "/civilization"):       "agents:write",
    ("POST", "/guardrails/test"):    "governance:read",
}
```

---

# PART 12: All 119 MCP Connectors

The `app/mcp/servers/` directory contains 119 connector server files. Each implements:
1. `get_tools()` → Returns `list[ToolDefinition]` — JSON Schema descriptions of available tools
2. `call_tool(tool_name, arguments)` → Makes real API call, returns result dict

All connectors:
- Use `httpx.AsyncClient` for non-blocking I/O
- Handle `ImportError` for optional native SDKs (boto3, motor, asyncpg) gracefully
- Include credential injection via `vault://` URI support

**Complete connector list by category**:

| Category | Connectors |
|----------|-----------|
| CRM | Salesforce, HubSpot, Pipedrive, Close CRM, Copper, Attio, Affinity, Apollo, Gong, Planhat |
| Project Management | Jira, Asana, Linear, Monday.com, ClickUp, Basecamp, Wrike, Trello, Todoist, Smartsuite |
| Communication | Slack, Discord, Microsoft Teams, Mattermost, Telegram, WhatsApp Business |
| Developer | GitHub, GitLab, Bitbucket, Jenkins, Docker, Kubernetes, Postman, Azure DevOps |
| Cloud | AWS S3, AWS IAM, AWS Lambda, AWS CloudWatch, GCP Storage, DigitalOcean, Heroku, Vercel, Netlify |
| Databases | PostgreSQL, MongoDB, MySQL, Redis, Snowflake, Elasticsearch, Supabase |
| Marketing | Mailchimp, Klaviyo, Brevo, ConvertKit, MailerLite, CustomerIO, Mandrill, Zapier |
| Analytics | Amplitude, Mixpanel, Google Analytics, Google Search Console |
| Finance | Stripe, PayPal, QuickBooks, Xero, Chargebee, Razorpay |
| HR | BambooHR, Rippling, Deel, Workday |
| E-Commerce | Shopify, WooCommerce, Square |
| Documentation | Confluence, Notion, WordPress, Webflow |
| Search | Brave Search, SerpAPI, Perplexity, Tavily, Firecrawl |
| AI Services | OpenAI, Pinecone, LinkedIn, X/Twitter |
| Advertising | Google Ads, LinkedIn Ads, TikTok, Instagram |
| Video | YouTube, Zoom |
| Collaboration | Box, Dropbox, OneDrive |
| Support | Gorgias, Front, Intercom, Freshdesk, Freshservice |
| Social | LinkedIn, Instagram, Telegram |
| Miscellaneous | PandaDoc, DocuSign, Loggly, Sentry, New Relic, Prometheus, Brevo, Razorpay |

---

# PART 13: app/scaling/ — Celery Background Processing (Deep Dive)

**Files**: `celery_app.py` (189 lines) + `tasks.py` (2,005 lines)

The scaling package is the async backbone of AgentVerse. Every goal execution, every cron trigger, every maintenance sweep, every audit flush happens through here. Understanding this package is essential to understanding the platform's operational posture.

---

## celery_app.py — The Celery Factory

**Purpose**: Single file that defines the `celery_app` singleton + **all** configuration. Every other module imports from here; there is no other Celery config.

### Worker Configuration

```python
worker_max_tasks_per_child = 100       # Recycle worker after 100 tasks (memory leak prevention)
worker_max_memory_per_child = 500_000  # Kill worker at 500 MB RSS (RSS in KB units)
task_acks_late = True                  # ACK only AFTER task completes, not when received
task_reject_on_worker_lost = True      # Re-queue if worker crashes mid-task
worker_prefetch_multiplier = 1         # Fetch only 1 task at a time (prevents hoarding)
task_default_retry_delay = 30          # Wait 30s before retry attempt
```

**Why `prefetch_multiplier=1`?** With multiple plan queues, a greedy worker could fetch 4 tasks from `goals.enterprise` and starve `goals.free`. Setting to 1 ensures fair distribution — each worker asks for one task at a time and processes it before fetching the next.

**Why `task_acks_late=True`?** If a worker crash-kills mid-execution, Redis still has the task in-queue (unacknowledged). RabbitMQ/Redis re-delivers it to a healthy worker. Without this, tasks vanish on worker death.

### Per-Plan Queue Routing

```python
PLAN_QUEUE_MAP = {
    "free":         "goals.free",
    "starter":      "goals.starter",
    "professional": "goals.professional",
    "enterprise":   "goals.enterprise",
}
```

**Deployment pattern**: Enterprise customers deploy dedicated worker pools:
```bash
# Enterprise-only workers (dedicated servers)
celery worker -Q goals.enterprise -c 20

# Free/starter shared workers
celery worker -Q goals.free,goals.starter -c 4
```

This means a surge in free-tier signups **cannot** starve an enterprise tenant's goals — they run on physically separate workers.

### Complete Queue Inventory

| Queue | Purpose | Concurrency recommendation |
|-------|---------|--------------------------|
| `goals` | Default goal execution (route target before plan-dispatch) | 8-16 |
| `goals.free` | Free plan goals | 2-4 |
| `goals.starter` | Starter plan goals | 4-8 |
| `goals.professional` | Professional plan goals | 8-16 |
| `goals.enterprise` | Enterprise plan goals (dedicated workers) | 16-32 |
| `goals.persistence` | Checkpoint persistence tasks | 4 |
| `goals_dlq` | Dead Letter Queue — failed goals for analysis | 1 |
| `schedules` | Cron/interval trigger firing | 2 |
| `maintenance` | All background housekeeping tasks | 4 |
| `governance` | HITL SLA enforcement | 2 |

### Beat Schedule — All 25 Periodic Tasks

| Beat Entry | Task | Schedule | Queue |
|-----------|------|----------|-------|
| `mcp-health-check-every-30s` | `check_mcp_health` | every 30s | maintenance |
| `fire-due-schedules-every-60s` | `fire_due_schedules` | every 60s | schedules |
| `record-queue-depths-every-30s` | `record_queue_depths` | every 30s | maintenance |
| `detect-stuck-goals` | `detect_stuck_goals` | every 5 min | maintenance |
| `execute-retention-policy` | `execute_retention_policy` | daily 3:00 AM UTC | maintenance |
| `expire-hitl-approvals` | `expire_hitl_approvals` | every 60s | maintenance |
| `check-email-goals` | `check_email_goals` | every 60s | maintenance |
| `drain-goals-dlq-every-5min` | `run_goal_dlq` | every 5 min | goals_dlq |
| `reindex-stale-knowledge` | `reindex_stale_knowledge` | every 1 hour | maintenance |
| `purge-expired-artifacts-daily` | `purge_expired_artifacts` | every 24h | maintenance |
| `civilization-discovery-every-30s` | `discover_and_tick_civilizations` | every 30s | maintenance |
| `warm-jwks-cache` | `warm_jwks_cache` | every 9 min | maintenance |
| `create-guardrail-partitions` | `create_guardrail_partitions` | monthly 1st at 2:00 AM | maintenance |
| `enforce-hitl-sla` | `enforce_hitl_sla` | every 5 min | maintenance |
| `flush-audit-wal` | `flush_audit_wal` | every 10s | maintenance |
| `scan-cost-anomalies` | `scan_cost_anomalies` | every hour (top of hour) | maintenance |
| `embed-marketplace-templates` | `embed_marketplace_templates` | every 15 min | maintenance |
| `conclude-stale-experiments` | `conclude_stale_experiments` | daily 3:00 AM UTC | maintenance |
| `expire-stale-documents` | `expire_stale_documents` | daily 1:00 AM UTC | maintenance |
| `consolidate-memories-daily` | `consolidate_memories_task` | daily 3:00 AM UTC | maintenance |
| `reindex-stale-knowledge` | `reindex_stale_knowledge` | hourly | maintenance |

### RedBeat HA Scheduler

```python
celery_app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
celery_app.conf.redbeat_redis_url = REDIS_URL
celery_app.conf.redbeat_lock_key = "agentverse:beat:lock"
celery_app.conf.redbeat_lock_timeout = 300  # 5 minutes
```

**Why RedBeat matters**: Default Celery beat uses a local file (`celerybeat-schedule`). With 3 beat replicas, all three would fire every task simultaneously → duplicate executions. RedBeat uses a **Redis distributed lock** — only ONE beat replica acquires `agentverse:beat:lock` at a time. The others stand by. If the lock-holder dies, another acquires it within 5 minutes.

---

## tasks.py — All 25 Celery Tasks (2,005 lines)

### Infrastructure Helpers

**`_setup_sigterm()`** (runs at import): Installs a SIGTERM handler that calls `raise SystemExit(0)`. This triggers Python's finally-blocks + asyncio cancellation, which lets LangGraph write its final checkpoint before the process dies. Without this, `docker stop` would SIGTERM → immediate kill → in-flight step lost.

**`_get_redis_pool()` / `_get_sync_redis()`**: Module-level singleton connection pool (max 10 connections). Created once per worker process. Sync Redis clients are safe to share across tasks; async clients must be created per-task because each task uses `asyncio.new_event_loop()`.

**`_run_async(coro)`**: Creates a fresh event loop, runs the coroutine to completion, closes the loop. Used by all sync Celery task functions to call async code. Each invocation creates a new loop (not `asyncio.get_event_loop()`) because Celery workers are not async-native.

**`_get_llm_provider(tenant_id)`**: Loads per-tenant LLM config from Redis key `llm_config:{tenant_id}`. Decrypts the API key via vault. Returns the right `LLMProvider` subclass:
- `provider=anthropic` → `AnthropicProvider(default_model="claude-opus-4-8")`
- `provider=openai/groq/together/azure/ollama` → `OpenAICompatibleProvider(base_url=...)`
- Missing/error → `None` (falls back to env-var-based default provider)

**`_strip_secret_redis_schedule_fields(sched)`**: Strips `webhook_token`, `token`, `password`, `api_key`, `secret` from schedule dicts before writing back to Redis. Prevents credentials from accumulating in Redis keys.

**`_scheduled_goal_id(schedule_key, fire_instance_id)`**: `"sched_" + SHA-256(schedule_key + ":" + fired_at_iso)[:26]`. The deterministic ID prevents duplicate goal submissions if the beat fires twice (network blip, restart race).

---

### Task 1: `run_goal` — The Main Goal Executor

**Registration**: `@celery_app.task(name="app.scaling.tasks.run_goal", bind=True, max_retries=3)`

**Queue routing**: Dispatched to `goals.{plan}` at dispatch time by the API based on `PLAN_QUEUE_MAP[tenant.plan]`. The default route is `goals`; only the `apply_async(queue=...)` override at dispatch time routes to the plan-specific queue.

**What it does** (simplified):
```
1. Decrement concurrent-goal counter on exit (always, via finally)
2. Load LLM provider for tenant from Redis
3. Build AgentGraph with all 30+ dependencies
4. Run _run_with_signals(agent_runner, goal, ...) — executes LangGraph
5. Emit SSE events via Redis pub/sub to all subscribers
6. Write checkpoint after every step (AsyncRedis → Redis → Memory fallback)
7. On SIGTERM: graceful shutdown after current step completes
8. Retry up to 3× on transient failure (exponential backoff: 30s, 60s, 120s)
```

**Concurrency counter**: Redis key `concurrent_goals:{tenant_id}` tracks live goal count. `_decrement_after_completion()` runs in a `finally` block — decrements even if the task raises. This prevents stuck counters from permanently blocking a tenant.

**Monkey-patch detection**: At import, captures `_REAL_AGENT_LOOP_CLASS = AgentLoop`. If tests replace `app.agent.loop.AgentLoop` with a mock, `run_goal` detects this and uses the mock path (for test compatibility) instead of bypassing to `AgentGraph`.

---

### Task 2: `run_goal_dlq` — Dead Letter Queue Handler

**Registration**: `max_retries=0` — no retries. DLQ tasks already failed 3× in the main queue.

**What it does**: Marks the goal as `failed` in the DB with reason `"max_retries_exceeded"`. Emits a `goal_failed` SSE event. Writes a structured log entry for post-mortem analysis.

**Beat entry**: Runs every 5 minutes to drain any accumulated DLQ entries that didn't get processed immediately.

---

### Task 3: `run_scheduled_goal` — Schedule-Triggered Goal Execution

**Registration**: `bind=True, max_retries=3` — same retry policy as `run_goal`.

**What it does**: Wraps `run_goal.apply_async()` with schedule metadata injected into the goal context. The agent sees the goal as coming from a schedule trigger, not a user API call.

---

### Task 4: `fire_due_schedules` — The Cron Engine

**Registration**: `bind=True, max_retries=3`. Runs every 60s.

**Schedule sources** (dual-source, merged):
1. **Redis**: Scans `schedule:*` keys written by `ScheduleStore`. Each key is a JSON schedule object.
2. **PostgreSQL** (opt-in via `AGENTVERSE_DB_SCHEDULE_DISCOVERY=true`): Loads schedules from the `schedules` table.

**Three trigger types**:

| Type | Logic |
|------|-------|
| `cron` | `croniter(cron_expression, now+1s).get_prev()` — gets the most recent slot that should have fired. If `last_fired_at < prev_slot` → fire. Dedup via ISO timestamp as `fire_instance_id`. |
| `interval` | `(now - last_fired_at).total_seconds() >= interval_seconds`. If never fired, fires immediately. |
| `once` | `fire_at_iso` parsed to UTC datetime. If `now >= fire_at` and `last_fired_at is None` → fire once and never again. |

**Security**: `_strip_secret_redis_schedule_fields()` removes credential fields before writing schedule back to Redis. This prevents accumulation of plaintext secrets in Redis over time.

**Idempotency**: `_scheduled_goal_id(schedule_key, fire_instance_id=fired_at.isoformat())` generates a deterministic goal ID. If `fire_due_schedules` fires twice in the same second, both produce the same goal ID → DB unique constraint rejects the duplicate.

---

### Task 5: `record_queue_depths` — Autoscaling Metrics

**Registration**: every 30s. `bind=True, max_retries=3`.

**What it does**: For each queue in `("goals", "schedules", "maintenance")`:
```python
depth = r.llen(queue_name)          # Redis LLEN — O(1) operation
record_queue_depth(queue, depth)    # Prometheus gauge update
```

These gauges feed the Grafana autoscaling dashboard. A Kubernetes HPA can scale workers when `goals` depth exceeds a threshold.

---

### Task 6: `check_mcp_health` — MCP Server Watchdog

**Registration**: every 30s. No retries (it's a best-effort health check).

**What it does**:
1. Scans Redis for `mcp:servers:{tenant_id}:{server_id}` keys
2. Parses each as `MCPServerConfig`
3. `GET {base_url}/health` with 5s timeout via httpx
4. Records result: `{server, status: "ok"|"error", code}`
5. Cap at 50 servers per run to prevent runaway

**Fallback**: If `MCPServerConfig` import fails (e.g., during test), falls back to scanning `mcp:servers:*` keys with the old flat-dict key structure for backward compatibility.

---

### Task 7: `detect_stuck_goals` — Zombie Goal Reaper

**Registration**: every 5 minutes. `max_retries=0` (detection failures are non-critical).

**What it does** (raw SQL for performance):
```sql
UPDATE goals
SET    status='failed',
       error_message='Stuck goal: exceeded 60-minute timeout',
       updated_at=NOW()
WHERE  status IN ('executing','planning')
  AND  updated_at < NOW() - INTERVAL '60 minutes'
RETURNING id
```

**Why 60 minutes?** The `soft_time_limit` on `run_goal` should kill the Celery task before 60 minutes. This SQL sweep catches goals where the Celery task was killed externally (OOM, `kill -9`, infrastructure failure) without updating the goal status.

---

### Task 8: `execute_retention_policy` — GDPR/Data Lifecycle

**Registration**: daily at 3:00 AM UTC. `max_retries=1`.

**Env var**: `DATA_RETENTION_DAYS` (default: 90).

**Tables purged**:
- `goal_events` — SSE event stream records
- `decision_traces` — per-step reasoning traces

**Why only these two?** Goals themselves are retained (business record). Events and traces are operational data with no legal retention requirement. Configurable to 30 days for GDPR-strict deployments.

---

### Task 9: `expire_hitl_approvals` — HITL Timeout Enforcement

**Registration**: every 60s. `max_retries=0`.

**What it does**:
```sql
UPDATE approval_requests
SET    status='timed_out', resolved_at=NOW()
WHERE  status='pending'
  AND  expires_at IS NOT NULL
  AND  expires_at < NOW()
RETURNING id
```

When an agent pauses for human approval and nobody responds by `expires_at`, this task auto-resolves the request as `timed_out`. The waiting agent's BLPOP unblocks with a timeout event → agent either retries without approval or fails based on step configuration.

---

### Task 10: `check_email_goals` — Email-to-Goal Inbox

**Registration**: every 60s. `max_retries=1`. **Disabled by default** (`IMAP_ENABLED=false`).

**What it does**: Polls IMAP inbox via `imap_listener.check_and_process_emails()`. Each new email becomes a goal submitted under tenant `IMAP_TENANT_ID` with `PlanTier.PROFESSIONAL`. Subject line → goal text. Useful for: "Email agent@mycompany.com to start a deployment" workflows.

---

### Task 11: `consolidate_memories_task` — Memory Deduplication

**Registration**: daily at 3:00 AM UTC.

**Two operations**:
1. **Deduplicate**: `DELETE FROM long_term_memory WHERE id NOT IN (SELECT DISTINCT ON (tenant_id, content) id ...)` — keeps the most recent copy of each unique memory content per tenant.
2. **Expire**: `DELETE FROM long_term_memory WHERE created_at < NOW() - (DATA_RETENTION_DAYS * INTERVAL '1 day')` — prunes old memories.

**Result**: `{duplicates_removed: N, expired_removed: M}`

---

### Task 12: `reindex_stale_knowledge` — Knowledge Freshness

**Registration**: every 1 hour.

**What it does**:
```sql
UPDATE documents
SET    needs_reindex = TRUE, updated_at = NOW()
WHERE  needs_reindex = FALSE
  AND  last_modified IS NOT NULL
  AND  freshness_ttl_hours > 0
  AND  last_modified < NOW() - (freshness_ttl_hours * INTERVAL '1 hour')
```

Documents with `freshness_ttl_hours > 0` are flagged for reindex when their content age exceeds the TTL. The ingestion pipeline (called separately) picks up `needs_reindex=TRUE` docs and re-embeds them.

---

### Task 13: `purge_expired_artifacts` — Storage Cleanup

**Registration**: daily (every 86,400s).

**What it does**: `DELETE FROM artifacts WHERE expires_at IS NOT NULL AND expires_at < NOW()`. The MinIO lifecycle policy handles actual S3-compatible object deletion; this task keeps the DB in sync.

---

### Task 14: `run_gdpr_export` — GDPR Data Portability

**Registration**: on-demand only (triggered by `POST /compliance/gdpr/export`). `max_retries=1`.

**What it does**:
1. `SELECT id, goal_text, status, created_at FROM goals WHERE tenant_id=:tid LIMIT 10000`
2. `SELECT event_id, goal_id, tool_name, outcome FROM audit_log WHERE tenant_id=:tid LIMIT 10000`
3. Serializes to JSON: `{tenant_id, exported_at, goals: [...], audit_entries: [...]}`
4. Generates `download_url = /compliance/export/{uuid}/download`
5. `UPDATE gdpr_export_jobs SET status='complete', download_url=...`

The download endpoint serves the JSON. On failure, sets `status='failed'` with error message.

---

### Tasks 15–17: Civilization Tasks

**`civilization_tick(civilization_id, tenant_id)`**: Triggered per-civilization. Loads `Constitution` from DB. Instantiates full `CivilizationOrchestrator` with Governor, Society, Bus, Blackboard, LearningPipeline. Calls `orchestrator.tick()` → breach check, reputation update, knowledge promotion.

**`civilization_learning_step(civilization_id, tenant_id)`**: Runs `LearningPipeline.run_step()` — advances one candidate knowledge item through validation or rejection.

**`discover_and_tick_civilizations()`**: Every 30s. `SELECT id, tenant_id FROM civilizations WHERE status='active'`. For each → `civilization_tick.delay(id, tenant_id)`. This ensures every active civilization gets periodic maintenance without needing explicit scheduling per-civilization.

---

### Task 18: `warm_jwks_cache` — JWKS Precompute

**Registration**: every 9 minutes (TTL is 10 minutes — 1-minute safety margin).

**What it does**: Calls `_build_jwks(db)` → queries all agent credentials from DB → builds JWK objects from stored public keys → `SETEX "jwks:cache" 600 <json>`. 

**Why 9 minutes?** The JWKS endpoint sets `Cache-Control: max-age=600`. If a client caches for 10 minutes, the beat task refreshes before the cache expires on the server, preventing the 10-second gap where a new key isn't yet cached.

---

### Task 19: `enforce_hitl_sla` — HITL Escalation

**Registration**: every 5 minutes. Queue: `governance`.

**What it does**:
```sql
SELECT id, tenant_id, request_id, sla_deadline_at, escalation_action
FROM   hitl_approval_requests
WHERE  status = 'pending'
  AND  sla_deadline_at IS NOT NULL
  AND  sla_deadline_at < NOW()
LIMIT  100;
-- Then for each:
UPDATE hitl_approval_requests
SET    status = 'sla_escalated', resolved_at = NOW()
WHERE  id = :id
```

SLA-breached approvals are marked `sla_escalated`. The future `escalation_action` field (e.g., `"notify_manager"`, `"auto_approve"`) drives downstream notification hooks.

---

### Task 20: `flush_audit_wal` — Audit WAL Drainer

**Registration**: every 10 seconds. This is the most frequently scheduled task.

**What it does**: `AuditFlusher(redis=r, db=db_factory).flush()` → reads from Redis list `audit_wal:{tenant_id}` → bulk-INSERTs into `audit_events` Postgres table → deletes flushed entries.

**At-least-once guarantee**: Redis RPUSH happens before the tool call returns. Even if the API process crashes after pushing but before the flush task runs, the event survives in Redis until `flush_audit_wal` picks it up.

---

### Task 21: `scan_cost_anomalies` — Cost Anomaly Detection

**Registration**: every hour (top of hour via `crontab(minute="0")`).

**What it does**:
1. `KEYS cost:daily:*` → extracts `tenant_id` from each key
2. For each tenant (capped at 50 per run): `CostTracker.detect_anomaly(tenant_id)`
3. Anomalies are logged/stored; future work: emit alerts via notification service

**Cap at 50 tenants**: Prevents a 1-hour task from scanning 10,000 tenants and exceeding its own time budget.

---

### Tasks 22–25: Stub Tasks (noop — reserved for future implementation)

| Task | Schedule | Future purpose |
|------|----------|----------------|
| `create_guardrail_partitions` | Monthly 1st at 2AM | Create next 3 months of `guardrail_events` partitions |
| `embed_marketplace_templates` | Every 15 min | Embed unembedded marketplace templates for semantic search |
| `conclude_stale_experiments` | Daily 3AM | Conclude A/B prompt optimization experiments >30 days old |
| `expire_stale_documents` | Daily 1AM | Delete knowledge chunks past `freshness_ttl_hours` |

These are registered in the beat schedule but return `{"status": "noop"}`. The beat schedule entry ensures they can be activated without a deployment change — just replace the body.

---

# PART 14: app/rpa/ — Browser Automation with Playwright (Deep Dive)

**Files**: `executor.py` (703 lines) + `session_manager.py` (261 lines) + `circuit_breaker.py`

The RPA package enables agents to control real web browsers. It uses open-source **Playwright** (Chromium) — not Selenium, not cloud browser services. This means it runs entirely within the Docker stack.

---

## executor.py — RPAExecutor

### `RPAResult` Dataclass

Every RPA operation returns:
```python
@dataclass
class RPAResult:
    success: bool
    output: str = ""              # Human-readable description of what happened
    artifact_url: str | None = None  # data:image/png;base64,... OR MinIO URI
    artifact_name: str | None = None # filename (e.g. "screenshot.png")
    duration_ms: float = 0.0     # Wall-clock time for the operation
    error: str | None = None     # Error message if success=False
```

### Three Execution Modes

The executor selects its mode at call time based on what's available:

```
if playwright_installed AND session_manager provided:
    → Mode 1: Stateful session (BrowserSessionManager)
elif playwright_installed:
    → Mode 2: Standalone (fresh browser per call)
else:
    → Mode 3: Simulation (mock responses, 100ms delay)
```

**Mode 1 — Stateful Session** (production path for multi-step workflows):
- Calls `session_manager.get_or_create(session_id, tenant_id)` → reuses live `page` object
- Page state (cookies, auth, DOM) persists across calls: `rpa_open_url` → `rpa_click` → `rpa_extract_text` all share one logged-in browser
- `session.touch()` updates `last_used_at` after every operation
- Credential injection runs before dispatch: `vault://crm-password` → real secret

**Mode 2 — Standalone** (single-operation or session_manager unavailable):
- `async with async_playwright() as p:` → new Chromium process per call
- Browser closed in `finally` block regardless of success/failure
- No state between calls — good for one-shot screenshot operations

**Mode 3 — Simulation** (Playwright not installed, or test environments):
- `await asyncio.sleep(0.1)` — simulates execution time
- Lambda dict maps each tool to a descriptive mock output
- Returns `success=True` always (except unknown tool names)
- Used in CI test environments where Playwright is not installed

### Credential Injection

Before any browser operation:
```python
if self._credential_injector is not None:
    arguments = await self._credential_injector.resolve_arguments(arguments)
```

This replaces `vault://` URI references in arguments:
```python
# Before injection:
{"selector": "#password", "text": "vault://stripe-api-key"}
# After injection:
{"selector": "#password", "text": "sk_live_REAL_KEY_HERE"}
```

The agent never sees the real credential value — it only ever writes `vault://secret-name` in its tool calls.

### Vision Analysis on Screenshots

When `_vision_provider` is set and `rpa_screenshot` is called:
```python
_ba = BrowserAgent(vision_provider=self._vision_provider)
vision_analysis = await _ba.analyze_screenshot(
    b64_screenshot,
    "Describe the main content and purpose of this page."
)
output += f"\nVision analysis: {vision_analysis}"
```

The agent receives both the screenshot artifact and a text description of what the page shows. This enables agents to make decisions based on visual content without needing hardcoded selectors.

---

### All 12 RPA Tools — Complete Reference

#### `rpa_open_url` — Navigate Browser
```python
arguments: {"url": "https://app.salesforce.com"}
```
- `page.goto(url, wait_until="domcontentloaded", timeout=15000)`
- Returns: `"Navigated to https://... — title: Salesforce Dashboard"`
- Session: `session.current_url = url` (tracked for debugging)

#### `rpa_click` — Click Element
```python
arguments: {"selector": "#submit-btn"}
# OR
arguments: {"text": "Create Opportunity"}
# OR
arguments: {"url": "https://...", "selector": "#btn"}  # navigate + click
```
- CSS selector: `page.click(selector, timeout=5000)`
- Text match: `page.get_by_text(text, exact=False).first.click(timeout=5000)`
- **Auto-screenshot**: Always captures a screenshot after click, returned as `artifact_url`
- Returns: `"Clicked: #submit-btn"` + screenshot data URI

#### `rpa_type` — Type Text Into Input
```python
arguments: {"selector": "#search-input", "text": "quarterly revenue"}
```
- `page.fill(selector, text, timeout=5000)` — fills the input (not key-by-key, instant fill)
- Use `page.type()` for key-by-key simulation (not exposed, deliberate — `fill()` is more reliable)

#### `rpa_extract_text` — Read Page Content
```python
arguments: {"selector": ".invoice-total"}  # default: "body"
```
- `page.inner_text(selector, timeout=5000)`
- Capped at **5,000 characters** to prevent oversized tool outputs
- Returns the visible text content (not HTML, not hidden text)

#### `rpa_screenshot` — Capture Visual State
```python
arguments: {"name": "before-submit", "url": "https://..."}
```
**Full pipeline**:
1. `page.screenshot()` → PNG bytes
2. If `artifact_store` available: `artifact_store.write_bytes(goal_id, "before-submit.png", bytes)` → MinIO URI
3. If `vision_provider` available: `BrowserAgent.analyze_screenshot()` → text description
4. Returns: `artifact_url` (MinIO URI or `data:image/png;base64,...`) + optional vision analysis

#### `rpa_wait_for_text` — Wait for Dynamic Content
```python
arguments: {"text": "Payment confirmed", "timeout_ms": 15000}
```
- **Primary**: `page.get_by_text(text, exact=False).wait_for(timeout=timeout_ms)`
- **Fallback**: If locator fails, checks `page.content()` for text substring (handles dynamically injected text outside Playwright's locator tree)
- Use case: waiting for async operations (payment processing, file upload completion)

#### `rpa_select_option` — Choose Dropdown Option
```python
arguments: {"selector": "#country-select", "value": "United States"}
```
- **Primary**: `page.select_option(selector, value=value)` — match by option `value` attribute
- **Fallback**: `page.select_option(selector, label=value)` — match by displayed text
- Handles both `value="US"` and `label="United States"` automatically

#### `rpa_upload_file` — Attach File to Input
```python
arguments: {"selector": "input[type=file]", "file_path": "/tmp/invoice.pdf"}
```
- Validates file exists before attempting upload
- `page.set_input_files(selector, file_path)` — Playwright's native file input handler
- Works with drag-and-drop file inputs too (Playwright handles the abstraction)

#### `rpa_download_file` — Download via Browser
```python
arguments: {"selector": "#export-csv-btn"}
```
**Pipeline**:
1. `async with page.expect_download() as download_info:` → intercepts download dialog
2. `await page.click(selector)` — triggers the download
3. `download.save_as(tempfile)` → saves to temp path
4. If `artifact_store`: uploads to MinIO, gets permanent URI
5. Returns: `"Downloaded 'report.csv' (45231 bytes)"` + `artifact_url`

#### `rpa_submit_form` — Fill and Submit Forms
```python
arguments: {
    "field_values": {
        "#first-name": "John",
        "#country": "US",        # select element → select_option
        "#agree-checkbox": True, # checkbox → check/uncheck
    },
    "submit_selector": "button[type=submit]"
}
```
**Smart field type detection** for each field:
```python
tag = await element.evaluate("el => el.tagName.toLowerCase()")
input_type = await element.evaluate("el => el.type || ''")
if tag == "select":          page.select_option(sel, value=str(value))
elif input_type == "checkbox": element.check() or element.uncheck()
else:                         element.fill(str(value))
```
**Submit fallback**: If `page.click(submit_selector)` fails → `page.keyboard.press("Enter")`.

#### `rpa_detect_captcha` — CAPTCHA Detection (simulation-only)
```python
# Returns: {"success": True, "output": "captcha_detected: false"}
```
Production implementation would use `page.query_selector('[class*=captcha], iframe[src*=recaptcha]')`.

#### `rpa_request_human_help` — Human Takeover Request
```python
arguments: {"reason": "CAPTCHA requires human verification"}
# Returns: {"success": True, "output": "Human help requested... Takeover URL: /rpa/live"}
```
The agent can call this when it hits a blocker. The frontend `/rpa/sessions/:id/live` page shows the live browser stream for the human to take over.

#### `rpa_wait_for_network_idle` — Wait for API Calls to Complete
```python
arguments: {"timeout_ms": 5000}
# Simulation returns immediately; production would call page.wait_for_load_state("networkidle")
```

---

### Browser Configuration

```python
browser = await p.chromium.launch(headless=self._headless)  # headless=True in prod
context = await browser.new_context(
    viewport={"width": 1280, "height": 720},
    user_agent="AgentVerse-RPA/1.0",
)
```

**Why 1280×720?** Covers 95%+ of responsive breakpoints without triggering mobile layouts. The user-agent string is honest (doesn't impersonate a real browser) but unlikely to trigger bot-detection on legitimate business applications.

---

## session_manager.py — BrowserSessionManager

### `BrowserSession` Dataclass

Each live browser session holds:
```python
@dataclass
class BrowserSession:
    session_id: str
    tenant_id: str
    created_at: float       # monotonic timestamp
    last_used_at: float     # updated on every touch()
    current_url: str        # last navigated URL (for debugging)
    _playwright: Any        # async_playwright() context
    _browser: Any           # chromium browser instance
    _context: Any           # browser context (cookies, storage)
    _page: Any              # the active page object

    @property
    def is_alive(self) -> bool:
        return self._browser is not None
```

### Session Lifecycle

```
get_or_create(session_id, tenant_id)
    ├── if existing and is_alive: touch() → return  (REUSE PATH)
    ├── if tenant_active >= max_per_tenant (5):
    │       evict LRU session (asyncio.create_task(old.close()) — non-blocking)
    └── _create_session():
            async_playwright().start()
            chromium.launch(headless=True)
            browser.new_context(viewport=1280×720, user_agent="AgentVerse-RPA/1.0")
            context.new_page()
            → registers in Redis: SETEX rpa_session:{tenant_id}:{session_id} 3600
```

### Per-Tenant Session Cap

- Default: `max_sessions_per_tenant = 5`
- On cap breach: find oldest session by `last_used_at` → `asyncio.create_task(old.close())` — eviction is **non-blocking** (the current call doesn't wait for the browser to close)
- Edge case: if all 5 slots are taken AND the oldest can't be evicted: returns a stub `BrowserSession(session_id, tenant_id)` with `_browser=None`. All subsequent tool calls fall through to simulation mode.

### Idle Session Cleanup

```python
async def cleanup_expired(self) -> int:
    cutoff = time.monotonic() - self._max_idle  # default: 300s = 5 minutes
    # close sessions idle longer than cutoff
```

Called periodically by the background task or by the RPA session API endpoint. Returns count of sessions closed.

### Redis Session Registry

Two purposes:
1. **Cross-restart visibility**: If the API process restarts, `list_active_from_redis()` shows sessions that were active before the restart (they may still be alive in another process or may have died).
2. **Multi-replica listing**: Frontend hits `GET /rpa/sessions` → API queries Redis → lists all sessions across all replicas.

```python
key = f"rpa_session:{tenant_id}:{session_id}"
SETEX key 3600 json.dumps({session_id, tenant_id, created_at, current_url})
```

TTL of 3600s (1 hour) ensures stale Redis entries clean themselves up even if the browser session was closed without calling `_deregister_from_redis()`.

---

### How Agents Use RPA

A real multi-step CRM workflow in the agent loop:

```
Step 1: Plan — "Log into Salesforce and create an opportunity"
Step 2: Execute — rpa_open_url(url="https://login.salesforce.com", session_id="abc123")
        → Returns: "Navigated to https://... — title: Salesforce Login"
Step 3: Execute — rpa_type(selector="#username", text="vault://sf-username", session_id="abc123")
        → Credential injector: text = resolved_from_vault
        → Returns: "Typed into #username"
Step 4: Execute — rpa_type(selector="#password", text="vault://sf-password", session_id="abc123")
Step 5: Execute — rpa_click(selector="#Login", session_id="abc123")
        → Returns: "Clicked: #Login" + screenshot (logged-in dashboard)
Step 6: Execute — rpa_click(text="New Opportunity", session_id="abc123")
Step 7: Execute — rpa_submit_form(field_values={"#opp-name": "Q4 Deal", "#amount": "50000"})
Step 8: Execute — rpa_screenshot(name="opportunity-created", session_id="abc123")
        → Returns: MinIO URI + vision analysis: "Shows Opportunity record with status: Open"
Step 9: Verify — Screenshot confirms opportunity exists → SUCCESS
```

All 8 steps share the same `session_id="abc123"` → single Chromium browser process, one authenticated session. Without `BrowserSessionManager`, each step would re-open Salesforce and re-authenticate.

---

# Summary: The Complete Capability Map

| Module Family | Capabilities | Lines of Code |
|--------------|-------------|---------------|
| `agent/` — Execution Core | LangGraph state machine, multi-agent, debate, supervisor, model routing, workflow planning | ~15,000 |
| `civilization/` — Agent Society | Constitutional governance, reputation, blackboard, bus, learning pipeline | ~2,200 |
| `governance/` — Safety | Policy engine, HITL, budget enforcement, permissions, audit chain, SIEM | ~3,500 |
| `intelligence/` — Learning | Eval scoring, self-optimization, prompt A/B testing, benchmarking, guardrails, cost tracking | ~4,000 |
| `rag/` + `memory/` — Knowledge | Hybrid search, chunking, evaluation, execution memory, long-term memory, tool reliability | ~2,500 |
| `mcp/` — Connectivity | Registry, client, 119 connectors, capability search, OAuth | ~25,000 |
| `rpa/` — Browser Automation | 12 Playwright tools, 3 execution modes, session management, credential injection, vision analysis | ~1,500 |
| `auth/` — Identity & Security | Agent identity, SAML 2.0, SCIM 2.0, scope enforcement, permission cache, IP allowlist | ~2,000 |
| `enterprise/` — Enterprise | Marketplace, simulation, compliance, red team | ~3,500 |
| `services/` — Orchestration | Goal lifecycle, tenant management, notifications, event queue, webhook delivery, persistence adapter | ~4,200 |
| `providers/` — LLM Abstraction | Anthropic, OpenAI, Gemini, Voyage, Fake, vault | ~1,500 |
| `reliability/` — Fault Tolerance | Circuit breaker, rollback, bulkhead, deduplication, distributed lock, idempotency, goal lifecycle signals, result processor | ~1,200 |
| `collab/` + `perception/` | AgentCollabSession protocol, debate persistence, BrowserAgent, PageAnalyzer, vision analysis | ~1,000 |
| `scaling/` — Background Processing | 25 Celery tasks, 10 queues, 25 beat entries, RedBeat HA, per-plan isolation, ParallelExecutor, PriorityQueue | ~2,600 |
| `tools/` — Built-in Agent Tools | Docker code execution, SearXNG search, shell (Docker sandboxed), HTTP (SSRF-protected), email, document parser, file ops, artifacts | ~1,600 |
| `integrations/` — Inbound Triggers | Email-to-goal (IMAP), approval emails (HMAC), Slack (/agentverse slash command + HITL buttons), Zapier webhooks | ~400 |
| `sdk/` — Developer SDK | AgentManifest (versioned YAML format), MockMCPServer (JSON-RPC 2.0 in-memory) | ~270 |
| `analytics/` — Analytics Engine | GoalAnalyticsAggregator: goal/tool/agent/cost metrics, DB+memory dual source | ~250 |
| `pipeline/` — Tool Pipeline | 12-step governance wrapper for every tool call: cost, permissions, dedup, circuit breaker, HITL, audit, memory, rollback, result processing, RAG | ~260 |
| `testing/` — Test Infrastructure | AgentTestHarness: run goals with mocked tools, assertion helpers, FakeProvider + MockMCPClient | ~160 |
| `triggers/` — Scheduling Models | TriggerSpec (10 trigger types), NLScheduler (NL→TriggerSpec via LLM), ScheduleStore | ~550 |
| `observability/` — Platform Telemetry | Prometheus metrics (20+ named metrics), structlog JSON logging, OpenTelemetry tracing, HealthRegistry | ~600 |
| `core/` — Foundation | Settings (12-factor config), secrets (file+env dual-source), connection pools, error classes | ~300 |
| `knowledge/ingestors/` — Data Sources | GitHub (tree+content), Jira (ADF→text), Confluence, PDF, DOCX, Slack | ~480 |
| **TOTAL** | **~70,000+ lines of production Python** | |

---

# PART 15: app/pipeline/ — The 12-Step Tool-Call Pipeline

**File**: `steps.py` (257 lines)

**Purpose**: Every tool call — whether to an MCP connector or a built-in tool — runs through this pipeline. It wraps the raw tool dispatch with the full governance stack: budget check, permission check, deduplication, circuit breaker, HITL gating, audit recording, execution memory lookup, rollback registration, result processing, SSE event streaming, and RAG context injection.

**Why it matters**: Without this pipeline, every tool call is just an HTTP request. With it, every tool call is audited, budget-controlled, permission-checked, deduplicated, and rollback-able. The pipeline is what makes AgentVerse "production-grade" rather than just "functional."

### All 12 Pipeline Steps

| Step | Function | What it does | Returns |
|------|----------|-------------|---------|
| 1 | `cost_check` | `CostController.check_and_record(goal_id, cost_usd, tenant_ctx)` | `bool` — False = over budget, block call |
| 2 | `governance_check` | `PermissionMatrix.check(tool_name, tenant_ctx)` | `ActionLevel` — ALLOW / ALLOW_LOG / APPROVAL / DENY |
| 3 | `dedup_check` | `DeduplicationCache.is_duplicate(content_hash, tenant_ctx)` | `bool` — True = duplicate, skip call |
| 4 | `circuit_breaker_check` | `CircuitBreaker.is_closed()` | `bool` — True = circuit open, block call |
| 5 | `hitl_gate` | `HITLGateway.request_approval(goal_id, action, risk_level)` | `bool` — True = approval requested (caller blocks) |
| 6 | `record_usage` | `AuditLog.record(AuditEvent(goal_id, tool_name, ALLOW_LOG, tokens))` | `None` |
| 7 | `exec_memory_lookup` | `ExecutionMemory.recall(goal_hint, tenant_ctx)` | `list[dict]` — past winning plans |
| 8 | `record_rollback_point` | `RollbackEngine.register(action, inverse)` | `str` — action used as checkpoint ID |
| 9 | `result_processor_step` | `ResultProcessor.process(raw_output)` — redact secrets, truncate to 4000 chars | `str` — sanitized output |
| 10 | `stream_step_event` | Publish SSE event (no-op in pipeline; handled by loop's event_callback) | `None` |
| 11 | `smart_context_fetch` | Hybrid vector+trigram search across ≤3 collections; returns top-3 results | `str` — formatted context |
| 12 | *(return result)* | Processed, redacted, audited, budget-checked result returned to executor | — |

### Design: Graceful Degradation

Every step accepts its dependency as `Optional` with `None` default. This means:
```python
# Full production: all 12 steps are active
result = await pipeline.run(cost_controller=controller, permission_matrix=matrix, ...)

# Tests: inject only what you need
result = await pipeline.run()  # all steps no-op, raw result passes through
```

This is how tests work without mocking the entire stack — the pipeline simply skips governance when dependencies are `None`.

### `smart_context_fetch` — RAG in the Pipeline

```python
async def smart_context_fetch(*, goal, step, tenant_ctx, knowledge_store,
                               query_embedding, context, agent_store) -> str:
```

- **Skips when no embedder**: `query_embedding is None` → returns `""` with a debug log. Avoids random-vector corruption.
- **Agent collection filtering**: If `context["agent_id"]` exists and the agent has `allowed_collection_ids`, only searches those collections.
- **Caps at 3 collections** per call to bound latency.
- **Returns**: `"[Context 1 (score=0.87)]: ...\n[Context 2 (score=0.81)]: ..."`

---

# PART 16: app/tools/ — Built-in Agent Tools

**8 files** providing native capabilities that don't require external MCP connectors.

Unlike MCP connectors (HTTP calls to external servers), these tools run **in-process**. They are injected into `AgentGraph` alongside MCP tools and appear identically to the agent.

---

## `code_interpreter.py` — Sandboxed Code Execution (302 lines)

**Purpose**: Execute Python, JavaScript, or Bash code in a sandboxed Docker container. The agent can write and run code to solve computational problems, process data, or automate tasks.

### Security Constraints (Hard-coded, not configurable)
```python
docker.containers.run(
    image,                          # python:3.12-slim | node:20-alpine | alpine:latest
    command=cmd,
    network_mode="none",            # No internet access from container
    mem_limit="256m",               # Hard memory cap
    cpu_quota=100000,               # 1 CPU maximum
    read_only=True,                 # Immutable root filesystem
    tmpfs={"/tmp": "size=64m,noexec=off"},  # /tmp only, 64MB, no exec
    user="1000:1000",               # Non-root user
    timeout=30,                     # Default 30s, configurable per call
    remove=True,                    # Container auto-removed after execution
)
```

### Language Support
| Language | Docker Image | Run Command | Extension |
|----------|-------------|-------------|-----------|
| `python` | `python:3.12-slim` | `python3 /sandbox/code.py` | `.py` |
| `javascript` | `node:20-alpine` | `node /sandbox/code.js` | `.js` |
| `bash` | `alpine:latest` | `sh /sandbox/code.sh` | `.sh` |

### Execution Flow
1. Write code to host-side temp file
2. Volume-mount read-only: `{tmp_path: {"bind": "/sandbox/code.ext", "mode": "ro"}}`
3. `await run_in_executor(None, lambda: client.containers.run(...))` — non-blocking from async
4. Decode stdout bytes → `CodeResult`
5. Delete temp file in `finally` block

### `CodeResult` Dataclass
```python
@dataclass
class CodeResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    execution_time_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out
```

### Subprocess Fallback
When Docker unavailable: `AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true` → runs via `asyncio.create_subprocess_exec`. **Blocked by default** — explicitly requires opt-in to prevent unsandboxed execution in production.

---

## `web_search.py` — SearXNG/DuckDuckGo Search (138 lines)

**Purpose**: Search the web for current information. Used by agents when they need real-time data, documentation, or facts not in their training data.

### Two Sources (Priority Order)
1. **SearXNG** (if `SEARXNG_URL` set): `GET {SEARXNG_URL}/search?q={query}&format=json`. Returns structured `{results: [{title, url, content, engine}]}`. Falls back to DuckDuckGo on error.
2. **DuckDuckGo Instant Answer API** (fallback, no key required): `GET https://api.duckduckgo.com/?q={query}&format=json`. Extracts `AbstractText` (featured snippet) + `RelatedTopics`.

### `WebSearchResult` Returns
```python
{"query": "...", "source": "searxng|duckduckgo",
 "results": [{"title": "...", "url": "...", "snippet": "..."}]}
```

---

## `shell_tool.py` — Sandboxed Shell Execution (158 lines)

**Purpose**: Execute shell commands for system tasks (git operations, build scripts, package management).

### Security Layers
1. **Disabled by default**: `AGENTVERSE_ALLOW_SHELL_EXEC=true` required
2. **Command allowlist**: Only these commands permitted: `echo, cat, ls, grep, awk, sed, sort, uniq, wc, head, tail, find, date, env, pwd, which, python3, node, git, npm, pip, curl`
3. **Path traversal protection**: `working_dir` validated against `_SAFE_ROOTS = ["/tmp", "/workspace", "/sandbox"]`
4. **Docker sandbox** (when available): `network_disabled=True`, 256MB limit, tmpfs workspace, 30s timeout
5. **Output cap**: `_MAX_OUTPUT = 64 * 1024` (64KB)

---

## `http_tool.py` — HTTP Request Tool (141 lines)

**Purpose**: Make HTTP requests to external APIs. Used by agents when no dedicated MCP connector exists for a service.

### SSRF Protection
```python
_BLOCKED_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254",          # AWS EC2 metadata
    "metadata.google.internal", # GCP metadata
    "100.100.100.200",          # Alibaba Cloud metadata
})
```
Additionally blocks RFC-1918 private ranges (`addr.is_private`), link-local, reserved, multicast IPs, and `.internal` / `.local` hostnames. **Fail-safe**: any parse error → block request.

### Features
- Methods: GET, POST, PUT, PATCH, DELETE, HEAD
- Body: dict → JSON, string → raw bytes
- Response capped at **512 KB** (`_MAX_RESPONSE_BYTES`)
- JSON auto-parsed when `Content-Type: application/json`
- Max timeout: `min(requested, 60.0)` — no unbounded waits
- User-Agent: `AgentVerse/1.0`

---

## `email_tool.py` — Email Send and Read (271 lines)

**Purpose**: Agents can send emails (reports, notifications, HITL escalations) and read inboxes.

**Two configs**:
- `SMTPConfig`: host, port (587), username, password, from_address, use_tls
- `IMAPConfig`: host, port (993), username, password, use_ssl

### Send Path
```python
await aiosmtplib.send(MIMEMultipart(From, To, Subject, plain+html bodies),
                      hostname, port, username, password, use_tls)
```
Returns: `{"status": "sent", "to": ..., "message_id": "<uuid@agentverse>"}`

### Read Path
`aioimaplib.IMAP4_SSL` → `search("UNSEEN" | "ALL")` → `fetch(msg_id, "(RFC822)")` → parse RFC822 → extract `body_preview` (500 chars). Returns `list[{message_id, from, subject, date, body_preview}]`.

### Module-level `email_send()` function
Convenience wrapper that reads `SMTP_HOST/PORT/USER/PASSWORD/TLS` from env — used for dev with MailPit (`SMTP_HOST=localhost SMTP_PORT=1025`).

### `from_vault_config()` class method
Instantiates `EmailTool` from a vault config dict with keys: `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `smtp_from`, `imap_host`, `imap_port`, `imap_username`, `imap_password`.

---

## `document_parser.py` — Multi-Format Document Parser (261 lines)

**Purpose**: Extract text from uploaded documents so agents can reason over their content.

### Supported Formats
| Format | Parser | Library | Max | Metadata |
|--------|--------|---------|-----|----------|
| PDF | `pypdf.PdfReader` (falls back to PyPDF2) | `pypdf` | 50 pages, 100K chars | page_count, metadata dict |
| CSV | `csv.reader` → markdown table | stdlib | 1000 rows, 100K chars | rows, columns, headers list |
| DOCX | `docx.Document` | `python-docx` | 100K chars | paragraph_count |
| TXT/MD | raw decode | stdlib | 100K chars | — |
| JSON | `json.loads` + `json.dumps(indent=2)` | stdlib | 100K chars | — |
| YAML | `yaml.safe_load` + `yaml.dump` | `pyyaml` | 100K chars | — |

All parsing runs in a thread executor (`run_in_executor(None, self._parse_sync, ...)`) to avoid blocking the event loop for large files.

### `ParsedDocument` Return
```python
@dataclass
class ParsedDocument:
    filename: str; content: str; page_count: int | None
    metadata: dict; format: str; truncated: bool = False
```

---

## `file_ops.py` — Tenant-Scoped File Operations (157 lines)

**Purpose**: Give agents a sandboxed filesystem workspace for temporary files (intermediate results, generated reports, downloaded data).

### Workspace Isolation
Every tenant gets an isolated directory: `/tmp/agentverse-workspace/{tenant_id}/`

**Path traversal protection** (`_safe_path`):
```python
resolved = (self._workspace / path).resolve()
resolved.relative_to(workspace_resolved)  # raises ValueError if escaping
```
Any path that resolves outside the workspace raises `PermissionError`.

### Operations
- `read(path)` → async file read via `aiofiles` (stdlib fallback)
- `write(path, content)` → creates parent dirs, writes text, returns bytes_written
- `list(directory)` → returns `[{name, type, size_bytes, modified_at}]`
- `delete(path)` → handles both files (`unlink`) and directories (`shutil.rmtree`)
- `exists(path)` → returns False on PermissionError (treats as not-found)

Module-level wrappers: `file_read`, `file_write`, `file_list`, `file_delete` — return `{success, ...}` dict never raising exceptions (safe for agent tool calls).

---

## `artifact_tool.py` — Artifact Save Tool (81 lines)

**Purpose**: Agents can save any content as a downloadable artifact (CSV reports, markdown summaries, JSON exports).

### Storage Priority
1. **MinIO via artifact_store**: `artifact_store.write_bytes(key, data, content_type)` → returns MinIO URI
2. **Local tmp fallback**: `/tmp/agentverse-artifacts/{tenant_id}/{goal_id}/{artifact_id}/{name}` if artifact_store fails
3. **URL scheme**: `/artifacts/{tenant_id}/{goal_id}/{artifact_id}/{name}` (served by the API)

Returns: `{artifact_id, filename, content_type, size_bytes, artifact_url, created_at, goal_id, expires_hours}`

**Default TTL**: `expires_hours=168` (7 days). The MinIO lifecycle policy enforces actual deletion.

---

# PART 17: app/integrations/ — Inbound Trigger Layer

**4 files** across 3 subdirectories — the complete inbound integration layer for email-to-goal, Slack-to-goal, and Zapier webhooks.

---

## `email/imap_listener.py` — Email-to-Goal (150 lines)

**Purpose**: Monitor an IMAP mailbox and convert inbound emails into agent goals. Enables "email agent@mycompany.com to start a deployment" workflows.

### Configuration
```bash
IMAP_ENABLED=true          # disabled by default
IMAP_HOST=imap.gmail.com
IMAP_PORT=993              # or 143 for STARTTLS
IMAP_USER=agent@mycompany.com
IMAP_PASSWORD=...          # or app password
IMAP_SSL=true              # default
IMAP_MAILBOX=INBOX
```

### Processing Pipeline
1. `aioimaplib.IMAP4_SSL.search("UNSEEN")` → message IDs
2. `fetch(email_id, "(RFC822)")` for each (max 10 per poll)
3. Parse RFC822: `Subject` → goal_text; `From` → context; `body` (text/plain, 500 chars) → additional context
4. `goal_service.submit_goal(goal=goal_text, priority="normal", tenant_ctx=email_tenant_ctx)`
5. `imap.store(email_id, "+FLAGS", r"(\Seen)")` — mark as read so it won't fire again

**Email goal format**:
```
goal_text = subject + "\n\nAdditional context from email:\n" + body.strip()
```

---

## `email/approval_sender.py` — HITL Approval Emails (84 lines)

**Purpose**: Send HMAC-signed HTML approval emails with clickable Approve/Reject buttons when an agent hits a HITL gate.

### HMAC Signature Generation
```python
secret = os.getenv("HITL_EMAIL_SECRET", "changeme-please-set-HITL_EMAIL_SECRET")
payload = f"{request_id}:{action}".encode()
sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:32]
```

- `approve_url = "{frontend_url}/hitl/{request_id}/approve?sig={approve_sig}"`
- `reject_url = "{frontend_url}/hitl/{request_id}/reject?sig={reject_sig}"`

### HTML Email
Blue header + action description + two big buttons (green Approve, red Reject) + 24h expiry notice + link to Approval Inbox.

**Anti-replay**: `_verify(request_id, action, sig)` uses `hmac.compare_digest` (constant-time comparison) to prevent timing attacks on signature verification.

---

## `slack/handler.py` — Slack Integration (137 lines)

**Purpose**: Receive `/agentverse` slash commands from Slack and fire HITL approval buttons back into Slack channels.

### Signature Verification
```python
base = f"v0:{timestamp}:{body.decode()}"
expected = "v0=" + hmac.new(signing_secret.encode(), base.encode(), sha256).hexdigest()
hmac.compare_digest(expected, x_slack_signature)
```
- Rejects requests older than 5 minutes (stale timestamp protection)
- **Fail-open in development** if no `SLACK_SIGNING_SECRET` set; **fail-closed in production**

### `/agentverse [goal]` Slash Command
The `POST /api/integrations/slack` endpoint (in `api/integrations.py`) receives Slack's JSON, extracts the goal text, and calls `goal_service.submit_goal()`.

### `send_approval_request_to_slack()`
Posts a Block Kit message to `SLACK_APPROVAL_CHANNEL` (default `#approvals`):
```json
{
  "type": "section", "text": ":warning: *Approval Required*\nGoal: `{goal_id}`\nAction: `{action}`\nRisk: `{risk_level}`"
  "actions": [
    {"type": "button", "text": "Approve", "style": "primary", "action_id": "approve_hitl"},
    {"type": "button", "text": "Reject", "style": "danger", "action_id": "reject_hitl"}
  ]
}
```

---

## `zapier/handler.py` — Zapier Webhook Handler (40 lines)

**Purpose**: Accept Zapier webhook POST requests and convert them to goals.

### Signature Verification
`ZAPIER_WEBHOOK_SECRET` → constant-time comparison. Fail-closed in production.

### Payload Extraction
```python
# Supports multiple Zapier payload formats:
if "goal" in payload:   return str(payload["goal"])
if "text" in payload:   return str(payload["text"])
if "message" in payload: return str(payload["message"])
return str(payload)[:500]  # fallback: stringify entire payload
```

---

# PART 18: app/sdk/ — Developer SDK

**Files**: `manifest.py` (141 lines) + `mock_server.py` (130 lines)

The internal SDK module is separate from the external `agent-verse-sdk-python`. It provides developer-facing abstractions: a versioned agent manifest format and an in-memory MCP server for testing.

---

## `manifest.py` — AgentManifest (141 lines)

**Purpose**: Version-controlled, commit-able YAML/JSON format for agent configuration. Allows agents to be defined in code, reviewed in PRs, and deployed via CI.

### Complete Schema
```yaml
name: "salesforce-agent"           # required
version: "1.2.0"                   # semver required
description: "Manages Salesforce CRM"
autonomy_mode: "bounded-autonomous" # supervised | bounded-autonomous | fully-autonomous
goal_template: "Execute: {goal}"   # optional Jinja-like template
default_model: "claude-opus-4-8"   # override the system default

connector_requirements:
  - type: "salesforce"             # required connector
  - type: "slack"
    optional: true                 # won't block deployment if missing

knowledge_collections:
  - "salesforce-docs"
  - "company-runbooks"

policies:
  - name: "no-deletes"
    tools_pattern: "*.delete"
    action: "deny"                 # deny | require_approval
  - name: "production-writes"
    tools_pattern: "*.update"
    action: "require_approval"

eval_suite_id: "salesforce-eval-v2"
tags: ["crm", "production"]
```

### API
- `AgentManifest.from_yaml(path)` → parses YAML file
- `AgentManifest.from_json(path)` → parses JSON file
- `manifest.to_yaml()` → serializes back to YAML
- `manifest.validate()` → returns `list[str]` of validation errors (empty = valid)

**Validation rules**: name required, version required and semver-like, autonomy_mode one of 3 valid values, policy actions in `{"deny", "require_approval"}`.

---

## `mock_server.py` — MockMCPServer (130 lines)

**Purpose**: In-memory MCP JSON-RPC 2.0 server for testing agent behavior without real connector deployments.

### Two Response Modes
1. **Callable handler** (`fn`): Real Python function/coroutine called with the tool arguments. Supports both `async def` and regular `def`.
2. **Fixture response** (`fixture`): Static response returned for every call.

### Usage Pattern
```python
server = MockMCPServer("test-crm")
server.register_tool(
    "get_opportunity",
    description="Fetch a Salesforce opportunity",
    fn=lambda id: {"id": id, "amount": 50000, "stage": "Proposal"},
)
server.set_fixture("create_opportunity", {"id": "opp_123", "status": "created"})

# Enable call logging for test assertions
server.enable_call_logging()

await server.handle_jsonrpc("tools/list", {})
await server.handle_jsonrpc("tools/call", {"name": "get_opportunity", "arguments": {"id": "opp_1"}})

# Assert
calls = server.get_call_log()
assert calls[0]["tool"] == "get_opportunity"
assert calls[0]["arguments"] == {"id": "opp_1"}
```

**Fluent API**: Both `register_tool()` and `set_fixture()` return `self` for chaining:
```python
server.register_tool(...).set_fixture("list_accounts", []).enable_call_logging()
```

---

# PART 19: app/analytics/ — GoalAnalyticsAggregator

**File**: `aggregator.py` (253 lines)

**Purpose**: Compute behavioral metrics from goal event history. Powers the `/api/analytics` endpoint and the analytics dashboard (`/analytics` frontend page).

### Dual Data Source
The aggregator reads from whichever source is available:
- **PostgreSQL** (preferred when `tenant_id` + `db` provided): `SELECT id, status, priority, agent_id, created_at FROM goals WHERE tenant_id=:tid AND created_at > NOW()-:days*day LIMIT 10000`
- **In-memory GoalService** (fallback): reads from `goal_service._goals` dict for tests or when DB is not available

### Methods and Metrics

**`goal_metrics(tenant_id, days=30, agent_id=None) → GoalMetrics`**
```python
@dataclass
class GoalMetrics:
    total: int; completed: int; failed: int; cancelled: int
    success_rate: float   # completed / total, rounded to 4 decimal places
    avg_duration_s: float # mean of (completed_at - created_at).total_seconds()
    avg_cost_usd: float   # mean of goal.cost_usd
    total_cost_usd: float # sum of goal.cost_usd
```

**`tool_metrics(days=30) → list[ToolMetrics]`** (in-memory only)
Reads from `goal.events` — events with type `tool_call_complete/failed/step_complete`. Computes per-tool: `call_count`, `failure_count`, `avg_latency_ms`, `failure_rate`. Sorted by call_count descending.

**`cost_trends(days=30, bucket="day") → list[{period, cost_usd}]`**
Aggregates goal costs by day (`%Y-%m-%d`) or week (`%Y-W%V`). Returns sorted list.

**`agent_metrics(days=30) → list[AgentMetrics]`**
Groups goals by `agent_id`. Computes: `goal_count`, `success_rate`, `avg_eval_score` (from `goal.eval_score`), `avg_cost_usd`. Sorted by goal_count descending.

### Status Normalization
The aggregator handles both StrEnum variants and string comparisons:
```python
def _goal_status_completed(status) -> bool:
    return str(status).lower() in ("complete", "completed")
```
This tolerates `GoalStatus.COMPLETE` (StrEnum), `"complete"`, and `"completed"` equally.

---

# PART 20: app/collab/ — Collaboration Protocol

**Files**: `agent_collab.py` (189 lines) + `store.py` (323 lines)

---

## `agent_collab.py` — AgentCollabSession (189 lines)

**Purpose**: Multi-round propose/critique/counter/agree protocol for collaborative decision-making between multiple agents.

### The Protocol
```
Agent A: add_round(CollabRound(agent_id="agent-a", round_type="propose", content="Use Approach X"))
Agent B: add_round(CollabRound(agent_id="agent-b", round_type="critique", content="Approach X has risk Y"))
Agent A: add_round(CollabRound(agent_id="agent-a", round_type="counter", content="Risk Y mitigated by Z"))
Agent B: add_round(CollabRound(agent_id="agent-b", round_type="agree", content="Agreed with mitigation"))
→ session.synthesize_consensus() → ConsensusResult(agreed=True, summary="Use Approach X with Z")
```

### Two Consensus Methods

**Rule-based `synthesize_consensus()`** (no LLM):
- `agree_rounds` present + no `disagree_rounds` → `ConsensusResult(agreed=True, summary=last_proposal)`
- `disagree_rounds` present → `ConsensusResult(agreed=False, dissenter=disagree_rounds[-1].agent_id)`
- No agree rounds → `ConsensusResult(agreed=False, summary="No agreement round yet")`

**LLM-enhanced `synthesize_consensus_llm(provider)`**:
Serializes all rounds as JSON, asks LLM to produce: `{consensus, agreed, key_points, dissenter_id}`. Falls back to rule-based on error.

### Debate Persistence
`persist_debate(session_id, goal_id, tenant_id, ...)` writes to two tables:
- `debate_sessions`: one row per debate (original_goal, consensus, confidence, rounds, status)
- `debate_proposals`: one row per agent proposal (agent_role, proposal_text, critique_text, vote, round_number)

Uses `ON CONFLICT DO NOTHING` to make it idempotent — safe to call multiple times.

---

## `store.py` — CollabSessionStore (323 lines)

**Purpose**: Persists collaboration session state to PostgreSQL and provides SSE streaming for real-time collaboration.

Key methods: `create_session`, `get_session`, `add_message`, `broadcast_event`, `list_sessions` — all with tenant_id scoping. Uses Redis pub/sub for cross-process event delivery when multiple API replicas are running.

---

# PART 21: app/observability/ — Platform Telemetry

**4 files** providing the full observability stack: Prometheus metrics, structured logging, OpenTelemetry tracing, and a composable health check registry.

---

## `metrics.py` — Prometheus Metrics (361 lines)

**Purpose**: Define, register, and expose all platform metrics. A dedicated `CollectorRegistry` (not the global default) keeps test isolation clean.

### Label Normalization
All labels are normalized through frozenset allowlists with alias mappings to prevent cardinality explosion:

```python
# Provider normalization
"azure" → "azure_openai"; "ollama" → "local"
# Status normalization
"completed" → "complete"; "running" → "started"; "unreachable" → "error"
# Token type normalization
"input" → "prompt"; "output" → "completion"; "cache" → "cached"
```

### Core Metrics (All Named `agentverse_*`)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `agentverse_goal_duration_seconds` | Histogram | `status, priority` | Latency distribution for goal execution |
| `agentverse_tool_calls_total` | Counter | `tool_category, status` | Tool call volume by category and outcome |
| `agentverse_llm_tokens_total` | Counter | `provider, model, token_type` | Token consumption tracking |
| `agentverse_llm_cost_usd_total` | Counter | `provider, scope` | LLM cost accumulation |
| `agentverse_active_goals` | Gauge | — | Currently executing goals |
| `agentverse_queue_depth` | Gauge | `queue_name` | Celery queue backlog depth |
| `agentverse_schedule_fires_total` | Counter | `status` | Schedule trigger firings |

**Design rule**: Histograms for latency (not averages), counters for totals, gauges for in-flight state.

### Recording Functions
```python
record_goal_duration(status, duration_seconds, priority)
record_tool_call(tool_name, status)          # normalizes tool to category
record_llm_tokens(provider, model, token_type, count)
record_goal_cost(provider, scope, cost_usd)
set_active_goals(count)
record_queue_depth(queue_name, depth)
record_schedule_fire(status)
```

---

## `logging.py` — Structured Logging (42 lines)

**Purpose**: Configure structlog for structured JSON logging in production and human-readable console output in development.

```python
configure_logging(level="INFO", json_logs=True)
```

**Production** (`json_logs=True`): Each log line is a JSON object:
```json
{"event": "goal_started", "goal_id": "abc", "level": "info", "timestamp": "2026-06-29T..."}
```

**Development** (`json_logs=False`): `structlog.dev.ConsoleRenderer()` — color-coded, human-readable.

**Context variables**: `structlog.contextvars.merge_contextvars` — `request_id`, `tenant_id`, `goal_id` set via `structlog.contextvars.bind_contextvars()` in middleware automatically appear in all log lines from that request.

```python
# Usage everywhere in the codebase:
logger = get_logger(__name__)
logger.info("goal_started", goal_id=goal_id, tenant_id=tenant_id)
```

---

## `tracing.py` — OpenTelemetry Tracing (110 lines)

**Purpose**: Bootstrap distributed tracing. Sends spans to Jaeger via OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise uses an in-memory exporter for local debugging.

### Mode Selection
```python
configure_tracing("agentverse-backend", otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
```
- **With OTLP**: `OTLPSpanExporter(endpoint=..., insecure=True)` + `BatchSpanProcessor` (async, non-blocking)
- **Without OTLP**: `InMemorySpanExporter` + `SimpleSpanProcessor` → accessible via `get_recent_spans(limit=100)`

### `_NoOpTracer` Fallback
When `opentelemetry` is not installed (minimal test environments), `get_tracer(name)` returns `_NoOpTracer` which provides `start_as_current_span(name)` as a context manager that does nothing. **Every call site is safe without OTel installed.**

### `get_recent_spans(limit=100)`
Returns the last N in-memory spans as dicts: `{name, trace_id, span_id, start_time, end_time, attributes, status}`. Used by the `/api/replay` endpoint to show execution traces.

---

## `health.py` — HealthRegistry (45 lines)

**Purpose**: Composable, concurrent health check system. Each service module registers its own health check; the `/health` endpoint runs them all without knowing about individual services.

### Architecture
```python
@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    check: Callable[[], Awaitable[None]]  # raises on failure

@dataclass(slots=True)
class HealthRegistry:
    checks: list[HealthCheck]

    async def run(self) -> tuple[bool, dict[str, Any]]:
        # Runs all checks concurrently via asyncio.gather
        # Returns: (all_healthy, {"postgres": {"status": "up"}, "redis": {"status": "down", "error": "..."}})
```

**Registration pattern**:
```python
# In lifespan (main.py):
health_registry.register(HealthCheck("postgres", lambda: db.execute("SELECT 1")))
health_registry.register(HealthCheck("redis", lambda: redis.ping()))
health_registry.register(HealthCheck("mcp", lambda: mcp_registry.health_check()))
```

Any module can add its own check without modifying the `/health` endpoint.

---

# PART 22: app/perception/ — Browser Vision

**Files**: `browser_agent.py` (217 lines) + `page_analyzer.py` (93 lines) + `multimodal.py` (85 lines)

---

## `browser_agent.py` — BrowserAgent (217 lines)

**Purpose**: Headless Chromium automation with optional vision LLM analysis. The core engine for web perception — used by `RPAExecutor` (production) and `PageAnalyzer` (intelligence gathering).

### `BrowserAction` + `BrowserResult`
```python
@dataclass
class BrowserAction:
    action_type: str  # navigate | click | type | scroll | screenshot | extract_text
    selector: str = ""
    value: str = ""
    url: str = ""

@dataclass
class BrowserResult:
    success: bool; action: str; output: str = ""
    screenshot_b64: str = ""  # Base64-encoded PNG
    error: str = ""
```

### Operations
- `take_screenshot(url)` → full-page=False, 1280×720, returns base64 PNG
- `extract_text(url, selector="body")` → Playwright `inner_text()`, truncated to 5000 chars
- `execute_action(BrowserAction)` → dispatches by `action_type`: navigate, click, type, scroll, screenshot, extract_text
- `analyze_screenshot(screenshot_b64, question)` → sends to vision LLM provider with prompt; returns text description

**Note**: Network is NOT restricted — BrowserAgent has full internet access. Domain-level policies must be enforced at the application layer.

---

## `page_analyzer.py` — PageAnalyzer (93 lines)

**Purpose**: Combine screenshot + text extraction + LLM analysis into a structured `PageAnalysis` for injecting into the planner prompt.

### `PageAnalysis` Dataclass
```python
@dataclass
class PageAnalysis:
    url: str; title: str = ""; text_content: str = ""
    screenshot_b64: str = ""; llm_analysis: str = ""
    success: bool = False; error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_context_block(self) -> str:
        # Returns: "### Page: {url}\nTitle: {title}\nAnalysis:\n{llm_analysis}"
```

### `analyze_url(url, question, extract_text=True, take_screenshot=True)`
Runs screenshot + vision analysis + text extraction. The `question` parameter controls what the vision LLM is asked: `"What is the main purpose and content of this page?"` (default) or a specific question like `"What is the invoice total?"`.

### `analyze_multiple(urls, question)`
`asyncio.gather(*[analyze_url(url) for url in urls])` — concurrent multi-page analysis.

### `build_context_block(analyses)`
Formats all successful analyses as a `## Web Page Context` block for injection into the planner prompt.

---

# PART 23: app/triggers/ — Scheduling Models

**Files**: `models.py` (49 lines) + `nl_scheduler.py` (69 lines) + `store.py` (445 lines)

---

## `models.py` — TriggerSpec (49 lines)

**Purpose**: Defines all supported trigger types and the unified `TriggerSpec` data class.

### TriggerType Enum (10 types)
```python
class TriggerType(enum.StrEnum):
    CRON = "cron"           # Celery Beat cron expression
    INTERVAL = "interval"  # Every N seconds
    WEBHOOK = "webhook"    # HTTP POST to /webhooks/{token}
    EVENT = "event"        # Redis pub/sub channel
    REST = "rest"          # Manual HTTP call to /triggers/{id}/fire
    ONCE = "once"          # Fire once at fire_at_iso
    FILE_DROP = "file_drop"      # New file in watched directory
    ALERTMANAGER = "alertmanager" # Prometheus AlertManager webhook
    DATADOG = "datadog"          # Datadog webhook alert
    PAGERDUTY = "pagerduty"      # PagerDuty incident webhook
```

### `TriggerSpec` Fields
```python
@dataclass
class TriggerSpec:
    trigger_type: TriggerType = TriggerType.ONCE
    cron_expression: str = ""    # "0 9 * * 1-5" — CRON only
    timezone: str = "UTC"
    interval_seconds: int = 0   # INTERVAL only
    webhook_token: str = ""     # WEBHOOK only
    event_channel: str = ""     # EVENT only
    event_filter: str = ""
    fire_at_iso: str = ""       # ONCE only
    condition: str = ""         # optional: only fire if condition met
    description: str = ""       # human-readable description
```

---

## `nl_scheduler.py` — NLScheduler (69 lines)

**Purpose**: Convert natural language schedule descriptions into `TriggerSpec` objects using an LLM.

### Examples
```
"Every weekday at 9 AM UTC"     → TriggerSpec(CRON, "0 9 * * 1-5", "UTC")
"Every hour"                    → TriggerSpec(INTERVAL, interval_seconds=3600)
"At 8 AM, 2 PM, 8 PM daily"    → [TriggerSpec(CRON, "0 8 * * *"), TriggerSpec(CRON, "0 14 * * *"), TriggerSpec(CRON, "0 20 * * *")]
"Tomorrow at noon UTC"          → TriggerSpec(ONCE, fire_at_iso="2026-06-30T12:00:00Z")
```

### Protocol
Sends a strict JSON-only prompt to the LLM provider. Response is cleaned of markdown fences and parsed. For compound schedules (`"schedules": [...]`) returns multiple `TriggerSpec` objects. Falls back to `TriggerSpec(ONCE)` on JSON parse failure.

---

## `store.py` — ScheduleStore (445 lines)

**Purpose**: Persist schedules to Redis (primary, fast) and PostgreSQL (durable). The `fire_due_schedules` Celery task reads from the same Redis keys.

---

# PART 24: app/reliability/ — Complete Fault Tolerance

**PART 7** covered `circuit_breaker.py`, `rollback.py`, `bulkhead.py`, `dedup.py`. These are the 4 missing files:

---

## `distributed_lock.py` — GoalExecutionLock (66 lines)

**Purpose**: Prevent the same goal from being executed by two Celery workers simultaneously (at-most-once guarantee).

### Implementation: Redis SET NX with Lua Script
```python
KEY_PREFIX = "goal_lock:"
# Acquire: SET goal_lock:{goal_id} {uuid} NX PX {ttl_ms}
# NX = only set if not exists; PX = TTL in milliseconds (default: 1,800,000 = 30 min)

# Release: Lua script (atomic check-and-delete)
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
# ARGV[1] = our UUID — only release if we own it

# Extend: Lua script (atomic check-and-extend)
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
end
```

**Why Lua for release?** If our TTL expires (worker crashed, very slow step), another worker acquires the lock. A simple `DEL` would delete the new worker's lock. The Lua script checks the value first — only deletes if it matches our UUID.

Methods: `acquire(goal_id, ttl_ms=1_800_000)`, `release(goal_id)`, `extend(goal_id, ttl_ms)`, `is_locked(goal_id)`

---

## `idempotency.py` — IdempotencyStore (35 lines)

**Purpose**: Prevent duplicate goal submissions — if a client submits the same goal twice (network retry, double-click), only one executes.

```python
KEY_PREFIX = "idempotency:"
redis_key = f"{KEY_PREFIX}{tenant_id}:{key}"

# check_and_set: SET NX EX → returns True (new) or False (duplicate)
result = await redis.set(redis_key, "1", nx=True, ex=ttl_seconds)
return result is not None  # None = key existed = duplicate

# release: for requests that failed — allow retry
await redis.delete(redis_key)
```

**TTL default**: 3600s (1 hour). If the same idempotency key arrives twice within 1 hour, the second is rejected. After 1 hour, the key expires and the request can be retried.

---

## `goal_lifecycle.py` — Cross-Process Goal Signals (118 lines)

**Purpose**: Allow the API server (async) to send pause/resume/cancel signals to Celery workers (separate processes) running goals.

### Signal Mechanism
```python
# API server sends signal:
await signal_pause("goal-123", async_redis)
# → redis.set("goal_paused:goal-123", "1", ex=7200)
# → redis.publish("goal_pause:goal-123", "pause")

# Celery worker checks before each step:
if is_cancelled_sync("goal-123", sync_redis):
    raise GoalCancelledError("Cancelled by operator")
await check_pause_cancel("goal-123", redis)  # blocks until resumed if paused
```

### Three Signals
| Signal | Redis Key | Channel | TTL |
|--------|-----------|---------|-----|
| Pause | `goal_paused:{goal_id}` | `goal_pause:{goal_id}` | 2h |
| Resume | (delete pause key) | `goal_pause:{goal_id}` | — |
| Cancel | `goal_cancelled:{goal_id}` | `goal_cancel:{goal_id}` | 2h |

**`check_pause_cancel`** (async): Checks cancel first (raises immediately), then checks pause and `asyncio.sleep(1)` polls until the pause flag is cleared.

**`is_cancelled_sync`** (sync): Used in Celery tasks (sync context) — checks the Redis key directly without asyncio.

---

## `result_processor.py` — ResultProcessor (59 lines)

**Purpose**: Sanitize tool outputs before they enter the agent context. Prevents credential leakage from noisy tool responses.

### Two Operations

**1. Secret Redaction**
```python
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),        # OpenAI API keys
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),      # GitHub personal tokens
    re.compile(r"xoxb-[A-Za-z0-9\-]+"),       # Slack bot tokens
    re.compile(r"Bearer [A-Za-z0-9\-._~+/]+=*"),  # Bearer tokens
]
# + Context-aware base64: only redacts when preceded by password/secret/token/key/credential/auth/bearer
```

**2. Truncation**
```python
_DEFAULT_MAX_LENGTH = 4000
# Appends "...[truncated]" if over limit
```

**Why context-aware base64 redaction?** Simple base64 patterns would flag commit SHAs, UUIDs, and other legitimate content. Only redacts when the base64 string appears immediately after a credential keyword — drastically reduces false positives.

---

# PART 25: app/testing/ — AgentTestHarness

**File**: `harness.py` (162 lines)

**Purpose**: Run complete agent goals (plan → execute → verify) with mocked tools and LLM responses, without a running server. Used for testing agent decision-making behavior, not just unit testing individual functions.

### Setup
```python
harness = AgentTestHarness(
    planner_responses=['{"steps": ["Search for invoices", "Export to CSV"]}'],
    executor_responses=["Found 5 invoices", "Exported to report.csv"],
    verifier_responses=['{"success": true, "reason": "CSV exported"}'],
)
harness.set_mock_tool("quickbooks.list_invoices", [{"id": "inv_1", "amount": 5000}])
harness.set_mock_tool("file_ops.write", {"success": True, "bytes_written": 1024})
```

### `TestResult` Dataclass
```python
@dataclass
class TestResult:
    __test__ = False  # Prevents pytest from collecting as a test class
    success: bool
    events: list[dict]      # All SSE events emitted during execution
    tools_called: list[str] # Names of all tool calls made
    plan_steps: list[str]   # Steps from the plan_ready event
    output: str             # Last step output
    cost_usd: float
    iterations: int
    error: str | None
```

### Assertion Helpers
```python
harness.assert_tool_called("quickbooks.list_invoices", result)
harness.assert_tool_called("quickbooks.list_invoices", result, times=1)  # exact count
harness.assert_tool_not_called("delete_database", result)
harness.assert_goal_completed(result)
harness.assert_output_contains("report.csv", result)
```

### Internals
The harness constructs a real `AgentGraph` with:
- `FakeProvider(responses=self._planner_responses)` for planner
- `FakeProvider(responses=self._executor_responses)` for executor
- `FakeProvider(responses=self._verifier_responses)` for verifier
- `MockMCPClient(self._mock_tools)` for all tool calls

This means the full LangGraph state machine runs — including the replan logic, HITL gates, and circuit breakers — just without hitting real LLMs or real APIs.

---

# PART 26: app/scaling/ — ParallelExecutor + PriorityQueue

---

## `parallel_executor.py` — ParallelExecutor (30 lines)

**Purpose**: Execute multiple async steps concurrently with a semaphore cap. Used by `WorkflowExecutor` and `GoalTree` to run independent sub-goals in parallel waves.

```python
class ParallelExecutor:
    def __init__(self, max_concurrency: int = 8) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)

    async def run_parallel(self, steps: list[Callable[[], Awaitable[str]]]) -> list[str]:
        async def _bounded(step):
            async with self._sem:
                return await step()
        return list(await asyncio.gather(*[_bounded(s) for s in steps]))
```

**Why a semaphore?** Without it, a plan with 50 independent steps would spawn 50 concurrent tool calls, exhausting connection pools and rate limits. The semaphore caps at 8 simultaneous, processing the rest as slots free up.

---

## `priority_queue.py` — PriorityQueue (61 lines)

**Purpose**: Priority-aware goal scheduling within a worker process. In production, the Redis sorted set uses the same scoring formula for cross-process priority ordering.

### Priority Levels
```python
class Priority(enum.IntEnum):
    P0 = 0  # critical — SLA breach imminent, emergency stop triggered
    P1 = 1  # high — enterprise tenant, time-sensitive
    P2 = 2  # normal (default)
    P3 = 3  # low — background analysis
    P4 = 4  # background — non-urgent maintenance
```

### Scoring Formula
```python
score = priority.value * 1_000_000_000_000 + created_at  # monotonic timestamp
```

**Why 1e12 scale factor?** `time.monotonic()` returns floats with ~6 decimal places of precision. Multiplying priority by 1e12 ensures P0 scores are always lower than P1 scores even after 11+ days of uptime (1e12 seconds ≈ 31,700 years). FIFO within same priority is preserved by the `created_at` component.

---

# PART 27: app/core/secrets.py — Secret Resolution

**File**: `secrets.py` (41 lines)

**Purpose**: Dual-source secret resolution that works identically in Docker Swarm/Kubernetes (mounted secret files) and local development (environment variables).

### Precedence Order
```
1. {NAME}_FILE env var → read & strip the file (Docker secrets, K8s secretsMounts)
2. {NAME} env var → use value directly (local dev, .env files)
3. default parameter → use fallback
4. raise SecretNotFoundError
```

### Usage in Production
```python
# Docker Compose / Kubernetes:
# DATABASE_URL_FILE=/run/secrets/database_url
# (the file contains the DSN string)

from app.core.secrets import read_secret
db_url = read_secret("DATABASE_URL", default="postgresql+asyncpg://localhost/dev")
```

**Why `*_FILE` precedence?** Docker Swarm secrets mount credentials as files at `/run/secrets/`. The file approach avoids credentials appearing in `docker inspect` output (env vars are visible there). Kubernetes `secretKeyRef` also uses file mounts in best practice configurations.

---

# PART 28: app/services/ — webhook_service + persistence

---

## `webhook_service.py` — OutboundWebhookService (157 lines)

**Purpose**: Deliver outbound webhooks (goal completion notifications, HITL events, cost alerts) to user-configured URLs with exponential backoff retry and a Dead Letter Queue.

### `WebhookDelivery` Dataclass
```python
@dataclass
class WebhookDelivery:
    delivery_id: str   # UUID hex
    tenant_id: str
    url: str
    payload: dict
    status: str        # pending | delivered | failed | dead
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    created_at: str    # ISO format UTC
    delivered_at: str  # set on success
```

### Delivery Pipeline
1. `deliver(tenant_id, url, payload)` → `httpx.AsyncClient.post(url, json=payload, timeout=10)`
2. On success: `status = "delivered"`
3. On failure: `status = "failed"`, increment `attempts`
4. At `attempts >= max_attempts`: `status = "dead"` → moved to DLQ list

### In-Memory Storage
- `_deliveries: dict[str, WebhookDelivery]` — active deliveries (max 10,000, TTL 24h)
- `_dlq: list[WebhookDelivery]` — dead letter queue
- `_evict_old_deliveries()` — removes entries older than 24h and caps at size limit

---

## `persistence.py` — Background Persistence Adapter (114 lines)

**Purpose**: Write goal state to PostgreSQL in the background. The in-memory `GoalService` is the source of truth for real-time operations; this module provides eventual persistence to DB without blocking goal execution.

### Design Principle
```python
"""Design: always use in-memory services (fast, testable), and additionally
attempt to persist to PostgreSQL in the background. Failure to persist is
logged but never raises to the caller."""
```

### Functions
- `persist_goal(goal_id, tenant_id, goal_text, status, priority, dry_run, db_session_factory)` → `INSERT INTO goals`
- `persist_goal_status(goal_id, tenant_id, status, error_message, db_session_factory)` → `UPDATE goals SET status`
- `persist_goal_event(goal_id, event_type, event_data, db_session_factory)` → `INSERT INTO goal_events`

All functions: if `db_session_factory is None` → `return` immediately (no-op). On DB error → `logger.warning(...)` and return. **Never raises to caller.**

---

# PART 29: app/knowledge/ingestors/ — Data Source Ingestors

**6 files** for pulling content from external sources into knowledge collections.

| Ingestor | File | Primary API | Output |
|----------|------|-------------|--------|
| GitHub | `github_ingestor.py` (124 lines) | GitHub REST API (`/repos/{owner}/{repo}/git/trees`, `/contents/{path}`) | Code files, README, docs |
| Jira | `jira_ingestor.py` (110 lines) | Jira REST API (`/search?jql=project={key}`) | Issues with ADF→plain text |
| Confluence | `confluence_ingestor.py` (86 lines) | Confluence REST API (`/wiki/rest/api/content`) | Pages as plain text |
| PDF | `pdf_ingestor.py` (59 lines) | pypdf / PyPDF2 | Text extracted per page |
| DOCX | `docx_ingestor.py` (41 lines) | python-docx | Paragraphs + tables |
| Slack | `slack_ingestor.py` (76 lines) | Slack Web API (`conversations.history`) | Messages per channel |

### GitHubIngestor
```python
ingestor = GitHubIngestor(token="ghp_...")
chunks = await ingestor.ingest_repo("owner", "repo")
```
- `_get_tree()` fetches the full repo file tree (recursive)
- `_should_ingest(path)` filters: `.py`, `.ts`, `.tsx`, `.js`, `.md`, `.txt`, `.yaml`, `.yml`, `.json` — skips test files, vendor dirs, build artifacts
- `_fetch_file_content()` fetches individual file content via base64-decoded blob
- Returns list of `{content, metadata: {path, repo, language}}`

### JiraIngestor
```python
ingestor = JiraIngestor(base_url="https://company.atlassian.net", token="...", user="user@co.com")
chunks = await ingestor.ingest_project("PROJ")
```
- JQL: `project={project_key} ORDER BY updated DESC` with pagination
- `_adf_to_text(adf)` converts Atlassian Document Format nodes (paragraph, text, heading, orderedList, bulletList, codeBlock, mention) to plain text
- Returns list of `{content: "PROJ-123: Bug in login\n\n{description}", metadata: {issue_key, type, status, priority}}`

### ConfluenceIngestor
Fetches pages from a Confluence space, strips HTML via BeautifulSoup or fallback regex, returns `{content, metadata: {page_id, title, space}}`.

---

# PART 30: Frontend — Stores, Hooks, Transport Layer

**Previously undocumented**: Zustand stores, React hooks, and the API client extensions for civilization and governance real-time.

---

## `src/stores/auth.ts` — Authentication Store (143 lines)

**Purpose**: Single source of truth for authentication state across the entire frontend. Persists to `sessionStorage` (cleared on tab close) with localStorage fallback for backward compatibility.

### Two Auth Modes

**1. API-key mode** (traditional):
```typescript
{apiKey: string; tenantId: string; plan: string; isAuthenticated: boolean}
setCredentials(apiKey, tenantId, plan)
```

**2. SSO/JWT mode** (Keycloak OIDC):
```typescript
{ssoMode: boolean; accessToken: string; refreshToken: string; tokenExpiresAt: number}
setSSOCredentials(accessToken, refreshToken, expiresIn, tenantId, plan)
updateAccessToken(accessToken, expiresIn)  // called by useTokenRefresh on silent refresh
```

### Security Design
```typescript
// sessionStorage ONLY — never write API keys or JWTs to localStorage
setItem: (name, value) => sessionStorage.setItem(name, value)
// Read localStorage for backward compat (old sessions), never write there
getItem: (name) => sessionStorage.getItem(name) ?? localStorage.getItem(name)
// logout: clear both
removeItem: (name) => { sessionStorage.removeItem(name); localStorage.removeItem(name) }
```

**Why sessionStorage?** `localStorage` survives browser restarts and is accessible to any XSS-injected script. `sessionStorage` is cleared when the tab closes, reducing the exfiltration window.

---

## `src/stores/ui.ts` — UI State Store (48 lines)

**Purpose**: Global UI state: sidebar collapse, theme, and mobile menu.

```typescript
{sidebarCollapsed: boolean; theme: "light" | "dark" | "system"; mobileMenuOpen: boolean}
```

Theme persists via Zustand `persist` middleware to localStorage. Sidebar state resets on page load.

---

## `src/stores/toast.ts` — Toast Notification Store (27 lines)

**Purpose**: Centralized toast notification queue.

```typescript
{toasts: Toast[]}
addToast(message, type: "success" | "error" | "warning" | "info", duration?)
removeToast(id)
```

---

## `src/hooks/useTokenRefresh.ts` — JWT Silent Refresh (121 lines)

**Purpose**: Automatically refresh the Keycloak access token before it expires, maintaining SSO sessions indefinitely.

### Refresh Logic
```typescript
// Refresh when token is within REFRESH_BEFORE_EXPIRY_SECONDS of expiring (default: 60s)
const shouldRefresh = ssoMode && tokenExpiresAt > 0 &&
                      (tokenExpiresAt - Date.now()/1000) < REFRESH_BEFORE_EXPIRY_SECONDS;
```

On refresh: `POST {KEYCLOAK_URL}/protocol/openid-connect/token` with `grant_type=refresh_token`. On success: `updateAccessToken(newToken, expiresIn)`. On failure (401/invalid): `logout()` → redirect to login.

**Polling interval**: Checks every 30 seconds. Only runs when `ssoMode === true`.

---

## `src/hooks/useAppHotkeys.ts` — Global Keyboard Shortcuts (32 lines)

**Purpose**: `?` help overlay + `g+{key}` navigation shortcuts for power users.

| Shortcut | Navigation |
|----------|-----------|
| `g` + `d` | Goals / Dashboard |
| `g` + `g` | Goals list |
| `g` + `a` | Agents list |
| `g` + `t` | Templates |
| `g` + `k` | Knowledge base |
| `g` + `r` | RPA sessions |
| `g` + `o` | Observability |
| `?` | Toggle help overlay |

Uses `react-hotkeys-hook` under the hood. The `g` key starts a "leader key" sequence — next key pressed within 1 second triggers navigation.

---

## `src/lib/api/civilizationApi.ts` — Civilization API Client (126 lines)

**Purpose**: Typed API client for civilization-specific endpoints.

Covers: `createCivilization`, `getCivilization`, `listCivilizations`, `tickCivilization`, `getCivilizationAgents`, `getConstitution`, `updateConstitution`, `getBlackboard`, `postBlackboardEntry`, `getSocietyReputation`.

---

## `src/lib/sse/useCivilizationStream.ts` — Civilization SSE Hook (95 lines)

**Purpose**: React hook for real-time civilization events via Server-Sent Events.

```typescript
const { events, connected, error } = useCivilizationStream(civilizationId);
```

Subscribes to `GET /api/civilization/{id}/stream`. Events: `agent_spawned`, `agent_retired`, `knowledge_promoted`, `breach_detected`, `tick_complete`. Auto-reconnects on disconnect with exponential backoff.

---

## `src/lib/api/governance-realtime.ts` — Governance Real-Time Transport

**Purpose**: Real-time governance events (HITL approval requests, policy violations, budget alerts) delivered via SSE.

```typescript
subscribeToApprovals(tenantId, onEvent)  // HITL approval inbox
subscribeToViolations(tenantId, onEvent) // Policy violation alerts
subscribeToBudgetAlerts(tenantId, onEvent) // Cost threshold alerts
```

Powers the `GovernanceDashboard` real-time updates and the `ApprovalInbox` live refresh.
