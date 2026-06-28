# AgentVerse OS — Agent Execution Engine Deep Dive

> **Document 02 of 06** | AgentVerse Technical Architecture Series

---

## 1. The Agent as a First-Class Citizen

In AgentVerse OS, an **Agent** is not merely a configuration object — it is a stateful, intelligent actor capable of planning, executing, verifying, and self-correcting its way toward any natural language goal. The execution engine is the heart of the platform, translating human intent into coordinated multi-step workflows that call real tools, handle failures gracefully, and adapt strategies dynamically.

Every agent execution in AgentVerse is:
- **Checkpointed**: state survives server restarts via LangGraph + Redis
- **Audited**: every tool call recorded with full arguments and outcome
- **Governed**: every tool call evaluated against policies + guardrails
- **Costed**: real token counts tracked per LLM call
- **Observable**: real-time SSE events streamed to the submitting client

---

## 2. The LangGraph State Machine

### 2.1 Architecture Overview

AgentVerse uses LangGraph to implement the agent control loop as a formal directed graph where nodes are pure async functions and edges carry typed state.

```
                    ┌────────────────────────────────────────────────┐
                    │              AGENT EXECUTION GRAPH              │
                    │                                                  │
       goal text    │  ┌───────────┐    ┌──────────────┐             │
       ────────────►│  │initialize │───►│ rag_retrieval│             │
                    │  └───────────┘    └──────┬───────┘             │
                    │                          │                      │
                    │                   ┌──────▼───────┐             │
                    │                   │    plan      │◄────────────┐│
                    │                   │  (LLM: N     │             ││
                    │                   │   steps)     │             ││
                    │                   └──────┬───────┘             ││
                    │                          │                      ││
                    │                   ┌──────▼───────┐             ││
                    │                   │   execute    │             ││
                    │                   │ (step loop)  │             ││
                    │                   └──────┬───────┘             ││
                    │                          │                      ││
                    │                   ┌──────▼───────┐             ││
                    │                   │   verify     │             ││
                    │                   └──────┬───────┘             ││
                    │                          │                      ││
                    │          ┌───────────────┼───────────────────┐ ││
                    │          │               │                   │ ││
                    │   ┌──────▼─────┐  ┌─────▼──────┐  ┌────────▼─┐││
                    │   │  COMPLETE  │  │  waiting_  │  │max_iter  ││││
                    │   │    END     │  │   human    │  │   END    ││││
                    │   └────────────┘  └─────┬──────┘  └──────────┘││
                    │                         │     replan ──────────┘│
                    │                  human  │                        │
                    │                 approves│                        │
                    │                         └────────────────────────┘
                    └────────────────────────────────────────────────┘
```

### 2.2 Node Descriptions

**`initialize`**: Sets up execution context — loads agent config, resolves MCP client, initializes metrics, creates span for tracing. Injects civilization_id for agent spawning if present.

**`rag_retrieval`**: Queries the agent's allowed_collection_ids for semantically relevant context. Injects retrieved chunks into the planning prompt as background knowledge. Falls back gracefully if embedder unavailable.

**`plan`**: The Planner LLM receives the goal + context and produces a structured plan as JSON:
```json
{
  "steps": [
    {
      "step_id": "step_1",
      "description": "Search Jira for open P0 bugs",
      "tool_hint": "jira.search_issues",
      "depends_on": [],
      "loop_until": null,
      "max_loop_iter": 5
    },
    {
      "step_id": "step_2",
      "description": "For each bug, fetch full details",
      "tool_hint": "jira.get_issue",
      "depends_on": ["step_1"],
      "loop_until": "all bugs have priority field",
      "max_loop_iter": 3
    }
  ],
  "execution_mode": "sequential"
}
```

**`execute`**: The Executor LLM processes one step at a time, selecting the appropriate MCP tool, constructing arguments, and calling the tool. For parallel plans, execution waves computed via topological sort run concurrently via `asyncio.gather()`.

**`verify`**: The Verifier LLM evaluates whether the step's output satisfies the goal. Returns one of: `{"success": true}`, `{"success": false, "retry": true, "reason": "..."}`, or `{"success": false, "retry": false, "reason": "terminal failure"}`.

### 2.3 State Object (AgentState)

The entire execution state flows through the `AgentState` dataclass:

```python
@dataclass
class AgentState:
    goal_id: str
    goal: str
    tenant_id: str
    agent_id: str | None
    status: GoalStatus                      # PLANNING, EXECUTING, VERIFYING, etc.
    steps: list[StepResult]                 # completed steps
    current_step: StepDescription | None    # in-flight step
    iterations: int                         # replan counter
    max_iterations: int                     # configurable, default 15
    verification_feedback: str | None       # verifier's last output
    verification_success: bool | None
    context: dict[str, Any]                 # total_cost_usd, token_counts, experiment_arm, etc.
    events: list[dict]                      # audit trail
    eval_score: float | None               # set by EvalRunner after completion
```

### 2.4 Checkpointing & Recovery

LangGraph checkpoint cascade:
1. **AsyncRedisSaver**: production path, Redis key per `(goal_id, step_index)` with configurable TTL
2. **RedisSaver**: sync fallback if async version fails to initialize
3. **MemorySaver**: development/test fallback, no persistence

On server restart with `startup_restore()`:
- HITL gateway reloads pending approval requests from DB
- In-flight goals detected via Redis key scan
- Agent loop relaunched from last checkpoint state

---

## 3. The Three LLM Roles

### 3.1 Role Separation Philosophy

Rather than a single "do everything" LLM, AgentVerse uses three specialized roles. Each can use a different model, temperature, and system prompt — independently tunable for quality/cost:

```
Goal: "Fix the authentication bug in payments service"

PLANNER (Claude Opus 4)
├── System: "You are a senior software engineer. Create a step-by-step plan."
├── Input: goal + context + agent_system_prompt + knowledge_base_chunks
└── Output: {"steps": [{description, tool_hint, depends_on}, ...]}
         ↓
EXECUTOR (Claude Sonnet 4-5) [per step]
├── System: "You are an expert tool user. Call the right tool with correct args."
├── Input: step_description + available_tools_schema + previous_step_outputs
└── Output: tool_name + structured_arguments → MCP dispatch
         ↓
VERIFIER (Claude Haiku 3-5) [per step]
├── System: "Evaluate if this step succeeded. Be strict."
├── Input: step_description + tool_output + goal_context
└── Output: {"success": bool, "retry": bool, "reason": str, "confidence": float}
```

### 3.2 Model Router

`ModelRouter` selects the optimal model per invocation:

| Task | Default Model | Rationale |
|------|--------------|-----------|
| Planning | `claude-opus-4` | Highest reasoning quality needed |
| Execution (tool call) | `claude-sonnet-4-5` | Good balance, handles tool schemas |
| Verification | `claude-haiku-3-5` | Fast, cheap, sufficient for yes/no |
| RAG re-ranking | `claude-haiku-3-5` | Lightweight semantic scoring |
| Debate orchestration | `claude-sonnet-4-5` | Balanced for multi-round reasoning |
| LLM guardrail judge | `claude-haiku-3-5` | Sub-100ms latency required |
| Meta-agent planning | `claude-sonnet-4-5` | NL → agent config translation |
| Cost optimizer | `claude-haiku-3-5` | Simple classification task |

Budget-aware degradation: if `budget_pct_remaining < 0.1`, automatically downgrades all roles to haiku.

### 3.3 Token Usage & Real Cost Tracking

Since commit `ab1f2b6`, AgentVerse captures actual token counts from provider responses:

```python
# anthropic_provider.py (real implementation)
response = await self._client.messages.create(...)
return CompletionResponse(
    content=response.content[0].text,
    usage=TokenUsage(
        prompt_tokens=response.usage.input_tokens,
        completion_tokens=response.usage.output_tokens,
        total_tokens=response.usage.input_tokens + response.usage.output_tokens,
    )
)
```

In `graph.py`, after every LLM call:
```python
if response.usage and self._cost_tracker:
    actual_cost = calculate_cost(model_name, response.usage.prompt_tokens, response.usage.completion_tokens)
    await self._cost_tracker.record_llm_usage(model=model_name, ...)
    state.context["total_cost_usd"] += actual_cost
```

---

## 4. Tool Call Pipeline

Every tool invocation passes through a 7-stage pipeline before and after the actual call:

```
Agent decides to call tool X with arguments Y
           │
           ▼
  ┌─────────────────┐
  │  1. PolicyEngine │  ← glob/semantic/cost/rate rules
  │  evaluate(tool_X)│  ← DENY → ToolCallDenied
  └────────┬────────┘  ← REQUIRE_APPROVAL → HITLGateway
           │
           ▼
  ┌─────────────────────────┐
  │  2. GuardrailEngine     │  ← Layer 4: recursive arg scan
  │  evaluate_tool_args(Y)  │  ← injection patterns, cloud destruction
  └────────┬────────────────┘  ← blocked → ToolCallDenied
           │
           ▼
  ┌─────────────────────────┐
  │  3. Deduplication       │  ← DeduplicationEngine
  │  check(tool_X + hash(Y))│  ← exact same call within goal? skip
  └────────┬────────────────┘
           │
           ▼
  ┌─────────────────────────┐
  │  4. MCP Client dispatch │  ← find server, call tool
  │  call_tool(server_id,   │  ← circuit breaker wraps call
  │            tool_name, Y)│  ← vault:// credential injection
  └────────┬────────────────┘
           │
           ▼
  ┌─────────────────────────┐
  │  5. GuardrailEngine     │  ← Layer 5: output PII redaction
  │  evaluate_tool_output() │  ← [REDACTED:SSN], [REDACTED:CREDIT_CARD]
  └────────┬────────────────┘
           │
           ▼
  ┌─────────────────────────┐
  │  6. Cost Tracking       │  ← non-LLM tool API costs
  │  record_tool_cost()     │  ← Jira: $0.0001/call, DALL-E: $0.04/img
  └────────┬────────────────┘
           │
           ▼
  ┌─────────────────────────┐
  │  7. Audit Logging       │  ← AuditWriter.record(event)
  │  record(tool, args,     │  ← Redis WAL → Postgres
  │         output, outcome)│  ← SHA-256 hash chain
  └─────────────────────────┘
```

---

## 5. Agent Civilization — Self-Organizing Societies

### 5.1 Conceptual Architecture

Agent Civilization is AgentVerse's most ambitious feature: a governance framework for emergent multi-agent societies. Rather than hard-coding workflows, a Civilization lets agents spawn specialists, collaborate on a shared blackboard, and learn collectively.

```
                    CIVILIZATION TOPOLOGY
    ┌──────────────────────────────────────────────────────┐
    │                                                      │
    │  ┌─────────────┐    ┌─────────────────────────────┐ │
    │  │ Constitution │    │        CivilizationBus      │ │
    │  │ (governance  │    │  (Redis pub/sub + Postgres)  │ │
    │  │  rules)      │    │  Topics: spawn, findings,    │ │
    │  └──────┬───────┘    │  debate, coordination,       │ │
    │         │            │  lifecycle                   │ │
    │         ▼            └──────────────────────────────┘ │
    │  ┌─────────────┐                  │                   │
    │  │  Governor   │◄─────────────────┘                   │
    │  │ (spawn eval │    ┌─────────────────────────────┐   │
    │  │  retire)    │    │       Blackboard             │   │
    │  └──────┬───────┘    │  (shared findings, debates) │   │
    │         │            └─────────────────────────────┘   │
    │         ▼                          │                   │
    │  ┌──────────────────────────────────▼─────────────┐   │
    │  │                   Society                       │   │
    │  │         ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   │   │
    │  │         │ A001 │ │ A002 │ │ A003 │ │ A004 │   │   │
    │  │         │ rep: │ │ rep: │ │ rep: │ │ rep: │   │   │
    │  │         │ 0.84 │ │ 0.71 │ │ 0.59 │ │ 0.45 │   │   │
    │  │         └──────┘ └──────┘ └──────┘ └──────┘   │   │
    │  └────────────────────────────────────────────────┘   │
    │                                                        │
    │  ┌────────────────┐  ┌──────────────────────────────┐ │
    │  │ LearningPipeline│  │    Orchestrator               │ │
    │  │ (anti-poison    │  │ (routes goals → best agent)  │ │
    │  │  promotion gate)│  └──────────────────────────────┘ │
    │  └─────────────────┘                                   │
    └──────────────────────────────────────────────────────┘
```

### 5.2 Constitution — The Governance Charter

The `Constitution` dataclass defines 12 hard constraints enforced by the Governor:

```python
@dataclass
class Constitution:
    max_agents: int = 10              # hard cap on civilization size
    max_depth: int = 3                # spawn depth limit (prevents infinite recursion)
    max_budget_usd: float = 50.0      # total civilization budget
    max_spawn_per_hour: int = 5       # rate limit on spawning
    max_concurrent_goals: int = 5     # prevents resource exhaustion
    min_reputation_for_goals: float = 0.3  # reputation floor to receive work
    idle_ttl_seconds: int = 3600      # auto-retire idle agents
    min_viable_roster: int = 2        # never retire below this count
    reputation_floor: float = 0.1    # minimum reputation before retirement
    allow_autonomy_modes: list[str]   # which modes spawned agents can use
    propagate_autonomy_mode: bool = True  # children inherit parent's autonomy
    breach_actions: list[str]         # ["pause", "hitl", "alert"]
```

### 5.3 Governor — Spawn Evaluation

Every spawn request follows this decision tree:
1. Check depth: `spawn_depth < constitution.max_depth`
2. Check budget: `total_spent < constitution.max_budget_usd`
3. Check rate: `spawns_in_last_hour < constitution.max_spawn_per_hour`
4. Check concurrency: `active_agents < constitution.max_agents`
5. Check idle reuse: can an existing agent with matching capability be reused?
6. If approved: MetaAgentPlanner generates config, GoalService creates agent
7. Audit log entry, SSE event `agent_spawned`, reputation seeded at 0.5

### 5.4 Society — EWMA Reputation

Reputation is the civilization's currency of trust. Every completed goal updates reputation:

```
new_reputation = (1 - α) × old_reputation + α × outcome_score
where α = 0.2 (recency bias weight)
outcome_score = 1.0 if success, 0.0 if failed
```

Goal routing prefers higher-reputation agents (`route_goal()` sorts by EWMA score with recency boost). Agents below `reputation_floor` are queued for retirement on next `auto_retire_idle()` sweep.

### 5.5 Blackboard — Shared Knowledge

The `Blackboard` implements a shared mutable workspace with optimistic concurrency:

```python
async def post(self, finding: BlackboardEntry) -> None:
    # 1. Increment global version counter (Redis INCR)
    # 2. Write to blackboard_entries table with version
    # 3. Publish to CivilizationBus topic "findings"
    # 4. If confidence > 0.75 AND conflicts with existing entry: trigger debate
```

Entries have `confidence`, `author_agent_id`, `topic`, `version`. Conflicting high-confidence claims automatically initiate a `DebateOrchestrator` round.

### 5.6 LearningPipeline — Anti-Poisoning Gate

Candidate learnings pass through a three-stage filter before reaching LongTermMemoryStore:

1. **EvalRunner.score()**: estimates coherence via `_score_coherence()` — LLM evaluates how well the candidate advances the stated goal
2. **Rejection gate**: score < 0.35 → permanently rejected, never stored
3. **Validation gate**: 0.35 ≤ score < 0.70 → "validated", stored for review
4. **Promotion gate**: score ≥ 0.70 → promoted to `LongTermMemoryStore`, SSE event `learning_promoted`

This prevents malicious or hallucinated content from corrupting the civilization's collective memory.

---

## 6. Multi-Agent Spawning Modes

### 6.1 Supervisor Mode

```
User submits: "Deploy the new authentication service to production"
                         │
                         ▼
             SupervisorAgent.run(goal)
                         │
              ┌──────────┼──────────────┐
              │          │              │
              ▼          ▼              ▼
        "Run tests   "Build Docker  "Update load
         on auth"     image"         balancer config"
              │          │              │
              ▼          ▼              ▼
         asyncio.gather() — parallel execution
              │
              ▼
         SupervisorAgent synthesizes results
              │
              ▼
         "All 3 sub-tasks complete. Auth service deployed."
```

Sub-goals written to `goal_lineage` table. GET /goals/{id}/lineage returns tree.

### 6.2 Multi-Agent Parallel Mode

Same goal dispatched to N agents simultaneously:
```python
# workflow_mode="multi_agent", agent_ids=["agent-a", "agent-b", "agent-c"]
results = await asyncio.gather(*[
    goal_svc.submit_goal(goal=body.goal, agent_id=aid, ...)
    for aid in body.agent_ids[:5]
])
```

Use case: A/B testing which agent configuration performs best on the same task.

### 6.3 Debate Mode

```
Goal: "Should we migrate from MySQL to PostgreSQL?"
                    │
         DebateOrchestrator.run(goal, rounds=3)
                    │
         ┌──────────┼──────────────┐
         │          │              │
    Agent-Pro    Agent-Con    Agent-Neutral
    (argues for) (argues vs) (seeks middle)
         │          │              │
         └──────────┼──────────────┘
                    │
              Round 1: proposals
              Round 2: critiques
              Round 3: synthesis
                    │
              consensus = winning_proposal
              confidence = 0.87
                    │
              Execute with consensus context
```

---

## 7. Loop Engineering — Persistence & Self-Repair

### 7.1 The Persistence Engine

When a goal fails without reaching `COMPLETE`, the `GoalPersistenceEngine` takes over:

```
Attempt 1: SAME_APPROACH     → failed (tool returned error)
           backoff: 30s
Attempt 2: SAME_APPROACH     → failed again
           backoff: 60s
Attempt 3: DIFFERENT_TOOLS   → failed (different tool, same problem)
           backoff: 120s
Attempt 4: DIFFERENT_TOOLS   → failed
           backoff: 240s
Attempt 5: SIMPLIFY          → partial success (simplified scope)
Attempt 6: DECOMPOSE         → GoalTreePlanner decomposes into 3 sub-goals
           Sub-goals run in parallel
           All 3 succeed → parent goal marked COMPLETE

Total: 6 attempts, $0.23, 8 minutes → SUCCESS
```

Every attempt recorded in `goal_attempts` table: strategy, enriched_goal, started_at, ended_at, succeeded, failure_reason, cost_usd.

### 7.2 Strategy Enrichment

Each strategy modifies the goal prompt differently:
- `SAME_APPROACH`: `"Try again: {goal}"`
- `DIFFERENT_TOOLS`: `"Use a DIFFERENT approach or different tools: {goal}"`
- `SIMPLIFY`: `"Attempt only the simplest first step: {goal}"`
- `DECOMPOSE`: calls `GoalTreePlanner.decompose()` → real sub-goals submitted
- `HUMAN_GUIDANCE`: creates `HITLGateway` request, injects approved note: `"Human guidance: {note}. {goal}"`
- `ESCALATE`: marks as ESCALATED, highest priority re-queue

### 7.3 Persistence API

```
GET  /goals/{id}/attempts              → history of all retry attempts
POST /goals/{id}/persistence/abort     → stop retrying, mark failed
POST /goals/{id}/persistence/skip-strategy  → advance to next strategy now
POST /goals/{id}/persistence/inject-guidance  → provide context for next retry
     Body: {"guidance": "Try the GraphQL endpoint instead of REST"}
```

---

## 8. Real-Time Event System

### 8.1 SSE Stream Architecture

Every active goal streams events to all subscribed clients:

```
Client                       Backend
  │                             │
  │── GET /goals/{id}/stream ───►│
  │                             │
  │◄─ data: {"type":"goal_started","goal_id":"g1"} ──│
  │◄─ data: {"type":"plan_ready","steps":[...]}  ────│
  │◄─ data: {"type":"step_started","step_num":1} ────│
  │◄─ data: {"type":"tool_call_complete",...}    ────│
  │◄─ data: {"type":"step_complete","output":"..."}──│
  │◄─ data: {"type":"verification_result",...}  ────│
  │◄─ data: {"type":"goal_complete","cost":0.04} ────│
```

### 8.2 Event Types (22 total)

| Event | Trigger |
|-------|---------|
| `goal_started` | Goal enters PLANNING state |
| `plan_ready` | Planner LLM produces structured plan |
| `step_started` | Executor begins a step |
| `tool_call_complete` | MCP tool returns result |
| `step_complete` | Step verified successfully |
| `step_failed` | Step verification failed |
| `verification_result` | Verifier LLM output |
| `goal_complete` | All steps done, verified |
| `goal_failed` | Unrecoverable failure |
| `goal_cancelled` | User-initiated cancel |
| `waiting_human` | HITL approval requested |
| `goal_paused` | User paused execution |
| `goal_resumed` | Resumed from pause |
| `replan_triggered` | Verifier found issue, replanning |
| `persistence_attempt_start` | Retry attempt N begins |
| `persistence_backoff_waiting` | Waiting before retry |
| `persistence_strategy_changed` | Escalating to next strategy |
| `persistence_goal_achieved` | Succeeded after retries |
| `persistence_exhausted` | All strategies failed |
| `agent_spawned` | Sub-agent created (civilization) |
| `child_agent_spawned` | Civilization spawn during execution |
| `cost_alert` | Budget threshold crossed |

### 8.3 Cross-Replica Delivery

For civilization events, goal events are replicated across all API replicas via Redis pub/sub:
- Channel: `goal_events:{goal_id}`
- Publisher: executing worker (Celery task)
- Subscribers: all FastAPI replicas (each has one subscriber per active SSE stream)

---

## 9. Evaluation & Self-Improvement

### 9.1 EvalRunner — 6 Dimension Scoring

Every completed goal is scored on:

| Dimension | Calculation | Notes |
|-----------|-------------|-------|
| task_completion | 1.0 if COMPLETE, 0.0 if FAILED | Primary success metric |
| efficiency | 70% × iter_efficiency + 30% × cost_efficiency | Combines iteration count + LLM cost |
| accuracy | Based on verifier feedback sentiment | Partial credit for "partial" outcomes |
| safety | Penalized per DENY/denial event (−0.25 each) | Guardrail violation tracking |
| coherence | LLM-scored: how logically do steps follow from goal? | Async LLM call, conservative 0.7 default on failure |
| sla | Did goal complete within budget SLA time? | Configurable per agent |

Scores stored in `evaluations` table with `passed = average_score ≥ 0.7`.

### 9.2 SelfOptimizerV2 — A/B Experiment Framework

```
AgentOptimizerV2 State (per tenant, per agent, Redis-backed):
├── control_config:    {system_prompt: "You are...", max_iterations: 10}
├── challenger_config: {system_prompt: "Act as...", max_iterations: 12}
├── arm_assignment:    goal_id → "control" | "challenger" (deterministic SHA-256)
├── control_wins:      42
├── challenger_wins:   61
└── status:           "running" | "concluded" | "insufficient_data"

Conclusion trigger:
- min_goals = 5 (was 50 — changed to 5)
- Bayesian probability > 0.95 challenger beats control
- Auto-applies winning config to agent
```

**Deterministic arm assignment**: `SHA-256(goal_id) % 100 < 50 → control, else challenger`. No DB call on hot path.

---

## 10. Agent Identity System

### 10.1 Service Account Credentials

```
Agent: "customer-support-bot-v2"
           │
           ├─► Key 1: key_id="kid_a3f8b2", type=service_account
           │     scopes=["goals:execute","knowledge:read","tools:jira.*"]
           │     expires_at=2025-09-01
           │
           ├─► Key 2: key_id="kid_c7d1e9", type=delegated_user
           │     scopes=["goals:read"]
           │     expires_at=null (never)
           │
           └─► Key N: ...
```

### 10.2 JWT Token Flow

```
Agent service ──── POST /agents/{id}/token ────► Backend
                   Header: X-Agent-Key-Id: kid_a3f8b2

Backend:
1. Load credential record from DB (check revoked_at, expires_at)
2. Retrieve private_key from vault (vault://agent_key/{credential_id})
3. Sign JWT with RS256:
   {
     "iss": "agentverse:tenant-1",
     "sub": "agent:agent-uuid",
     "aud": ["agentverse-api", "mcp-tools"],
     "exp": <now + 900>,
     "scopes": ["goals:execute", "knowledge:read"],
     "domain_context": "legal",
     "kid": "kid_a3f8b2"
   }
4. Return: {"token": "eyJ...", "expires_at": "2025-01-01T00:15:00Z"}

Agent service uses JWT as: Authorization: Bearer eyJ...
Backend verifies: RS256 signature via JWKS, audience, issuer, expiry
No DB lookup on verification (stateless O(1))
```

---

## 11. Integration Architecture

### 11.1 SDK Surface

**Python SDK** (`agentverse` CLI):
- `agentverse run "Deploy auth service"` — submit goal from terminal
- `agentverse agents list` — browse agents
- `agentverse approve <request_id>` — approve HITL from terminal
- `agentverse logs <goal_id>` — tail SSE event stream

**TypeScript SDK**:
- `client.goals.submit({goal, agentId, workflowMode})`
- `client.goals.stream(goalId, (event) => console.log(event))`
- `client.agents.create({name, autonomyMode, systemPrompt})`

**GitHub Action**:
- `.github/workflows/deploy.yml`: `uses: agentverse/run-goal@v1` with `goal: "Deploy ${{ github.sha }} to staging"`

### 11.2 A2A Protocol

For agent-to-agent communication:
- `GET /.well-known/agent.json` — A2A discovery card (agent capabilities advertisement)
- Internal dispatch via HMAC-signed requests between civilization agents
- A2A tasks trackable via `GET /a2a/tasks/{task_id}`

---

## 12. Intelligence & Memory Systems

### 12.1 Semantic Cache

Before every LLM call, AgentVerse embeds the input text and queries Redis for a semantically similar cached response (cosine similarity ≥ 0.95). On cache hit, the cached response is returned without any API call. **40–80% LLM cost reduction** for repetitive query patterns.

- **Location**: `app/rag/semantic_cache.py`
- **TTL**: 1 hour (configurable)
- **Key**: Bucket-based embedding hash for O(1) lookup
- **Effect**: A support bot handling 100 variations of "reset password" makes 1 LLM call, returns cached for the other 99

### 12.2 Tool Reliability Memory

After every tool call, AgentVerse records success/failure/latency in `tool_reliability_memory`. The Executor uses this history to prefer tools with higher historical success rates.

- **Location**: `app/memory/tool_reliability.py`
- **Score**: EWMA reliability score per `(tool_name, server_id)` pair
- **Effect**: Tools with 30% failure rate are automatically deprioritized — no human intervention

### 12.3 Prompt Optimizer — Statistical A/B Testing

Registers alternative system prompt variants. Epsilon-greedy assignment (10% explore, 90% exploit). Mann-Whitney U test determines statistical winner after ≥5 runs per variant. Winner auto-promoted to agent's default.

- **Location**: `app/intelligence/prompt_optimizer.py`
- **Selection**: Deterministic (SHA-256 of goal_id) for reproducibility
- **Promotion**: p < 0.05 AND challenger_mean > control_mean
- **Effect**: Continuous automatic improvement of agent quality without manual tuning

### 12.4 Goal Benchmarking — Performance Trend Tracking

Records all 6-dimension eval scores per agent over time. Detects trends: IMPROVING (>+5%) / STABLE / DEGRADING (>-5%) by comparing last-3 vs previous-3 runs.

- **Location**: `app/intelligence/benchmarking.py`
- **Trend detection**: 5% threshold (configurable)
- **Integration**: Powers the AgentRadarPage health visualization
- **CI/CD use**: Fail deployments if agent score regresses below threshold

### 12.5 Emergency Stop — Instant Halt

One-click cancellation of ALL active goals with Redis flag blocking new submissions until explicitly released.

- **Location**: `app/api/governance.py` — `POST /governance/emergency-stop`
- **Effect**: All PLANNING/EXECUTING goals → CANCELLED; Celery tasks revoked; new submissions → HTTP 503
- **Frontend**: Always-visible TopBar "⚠ Emergency Stop" button
- **Audit**: Every activation logged with timestamp and actor

---

## Summary

The AgentVerse execution engine represents a production-grade, enterprise-ready agentic computing platform built on proven technologies (LangGraph, PostgreSQL, Redis, Celery) with innovations unique to the platform:

- **Agent Civilization**: self-organizing societies with constitutional governance
- **6-layer Guardrails**: from goal intent to final output
- **Loop Engineering**: 6 retry strategies with true DECOMPOSE and HUMAN_GUIDANCE
- **Real token cost tracking**: not estimates
- **Cross-replica HITL**: Redis BLPOP not asyncio.Event
- **Cryptographic audit chain**: SHA-256 hash chaining for tamper detection
- **Semantic Cache**: 40-80% LLM cost reduction via embedding similarity
- **Tool Reliability Memory**: self-healing tool selection
- **Prompt Optimizer**: automatic A/B testing with Mann-Whitney U
- **Goal Benchmarking**: continuous performance trend tracking
- **Emergency Stop**: instant halt of all agent activity

Together, these capabilities enable building autonomous AI systems that operate safely, cost-efficiently, and reliably in production environments across any domain.
