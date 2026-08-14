---
title: Multi-Agent Civilization & Coordination
description: How AgentVerse organizes autonomous agents into governed societies with secure inter-agent communication, shared knowledge, and emergent coordination patterns.
outline: deep
---

# Multi-Agent Civilization & Coordination

AgentVerse's **civilization layer** is the operating system for teams of agents. Rather than hard-wiring multi-agent pipelines, it provides a governed substrate in which agents dynamically spawn sub-agents, share findings via a blackboard, coordinate using pluggable patterns (CAMEL, Swarm, GroupChat, MAGENTIC, MOA, Auction), and propagate W3C distributed traces across every hop.

## Architecture at a Glance

| Component | File | Responsibility |
|---|---|---|
| `Governor` | [governor.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/governor.py) | Central authority — only entity that creates/retires agents |
| `Constitution` | [constitution.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/constitution.py) | Immutable policy: depth, budget, rate, autonomy ceiling |
| `Society` | [society.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/society.py) | Membership, reputation (EWMA), goal routing |
| `Blackboard` | [blackboard.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/blackboard.py) | Shared findings store; conflict triggers debate |
| `CivilizationBus` | [bus.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/bus.py) | Redis pub/sub + Postgres persistence for all events |
| `LearningPipeline` | [learning.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/learning.py) | Curated collective learning — EvalRunner-gated anti-poisoning |
| `A2ADispatch` | [a2a_dispatch.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/a2a_dispatch.py) | Secure inter-agent task dispatch via GoalService |
| `A2ASecurity` | [a2a_security.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/a2a_security.py) | HMAC-SHA256-v1 request/callback signing with nonce replay protection |
| `SpawnTool` | [spawn_tool.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/spawn_tool.py) | MCP tool exposed to agents: `civilization_spawn` |
| `CivMetrics` | [metrics.py](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/metrics.py) | Prometheus gauges/counters for civilization-level operations |

## Multi-Agent Topology

The civilization forms a DAG of agent nodes. The Governor sits outside the DAG — it governs topology changes but does not participate in task execution.

```mermaid
graph TB
    User(["👤 User / API"]):::primary
    Gov["🏛 Governor<br>Constitution enforcement"]:::warning
    PA["🤖 Parent Agent<br>depth=0"]:::primary
    SA1["🤖 Specialist A<br>depth=1  jira_triage"]:::success
    SA2["🤖 Specialist B<br>depth=1  confluence_writer"]:::success
    SA3["🤖 Specialist C<br>depth=1  code_reviewer"]:::success
    GA1["🤖 Grand-child<br>depth=2"]:::neutral
    BB[("📋 Blackboard<br>shared findings")]:::warning
    BUS[("📡 CivilizationBus<br>Redis + Postgres")]:::neutral
    LTM[("🧠 LongTermMemory<br>promoted learnings")]:::success

    User -->|"submit goal"| PA
    PA -->|"evaluate_spawn_request()"| Gov
    Gov -->|"SpawnVerdict: APPROVED"| PA
    PA -->|"A2A dispatch_internal_task()"| SA1
    PA -->|"A2A dispatch_internal_task()"| SA2
    PA -->|"A2A dispatch_internal_task()"| SA3
    SA1 -->|"evaluate_spawn_request()"| Gov
    Gov -->|"SpawnVerdict: APPROVED"| SA1
    SA1 -->|"A2A dispatch_internal_task()"| GA1
    SA1 -->|"post finding"| BB
    SA2 -->|"query before acting"| BB
    BB -.->|"conflict → debate"| BUS
    BUS -.->|"events: spawn/findings/debate/lifecycle"| PA
    LTM -.->|"inject prior knowledge"| PA

    style User fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style Gov fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style PA fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style SA1 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style SA2 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style SA3 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style GA1 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style BB fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style BUS fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style LTM fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
```

<!-- Sources:
  agent-verse-backend/app/civilization/governor.py:1-70
  agent-verse-backend/app/civilization/a2a_dispatch.py:24-85
  agent-verse-backend/app/civilization/blackboard.py:36-80
  agent-verse-backend/app/civilization/bus.py:1-70
  agent-verse-backend/app/civilization/society.py:1-70
-->

## The Constitution: Immutable Behavioral Policy

The [`Constitution`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/models.py#L47-L75) is a frozen dataclass that encodes the hard limits of a civilization. It is created once at startup and never mutated — the Governor reads from it on every decision.

```python
# Source: agent-verse-backend/app/civilization/models.py:47-75
@dataclass
class Constitution:
    max_depth: int = 4                        # Max agent spawn depth
    max_total_agents: int = 50                # Total alive agents
    max_concurrent_agents: int = 10           # Simultaneously active agents
    total_budget_usd: float = 100.0           # Civilization-wide budget cap
    per_agent_budget_usd: float = 10.0        # Default per-agent budget
    budget_decay: float = 0.6                 # child_budget = parent * (0.6 ^ depth)
    spawn_rate_limit_per_min: int = 20        # Spawn rate throttle
    high_risk_requires_hitl: bool = True      # HITL gate for risky operations
    autonomy_ceiling: str = "bounded-autonomous"
    reputation_floor: float = 0.2            # Retire agents below this score
```

**Why `budget_decay`?** A child at depth 2 receives `parent_budget * 0.6^2 = 36%` of the parent's budget. This exponential decay prevents runaway cost explosions — a depth-4 sub-agent can only consume `0.6^4 ≈ 13%` of the root budget, regardless of what the root agent was allocated. The calculation lives in [`Constitution.compute_child_budget()`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/models.py#L73-L75).

### Constitution Evaluation Flow

```mermaid
flowchart LR
    SR["Spawn Request"] --> D1{depth <<br>max_depth?}
    D1 -->|No| DENY["❌ DENIED<br>depth exceeded"]
    D1 -->|Yes| D2{total_agents <<br>max_total_agents?}
    D2 -->|No| DENY
    D2 -->|Yes| D3{concurrent_agents <<br>max_concurrent?}
    D3 -->|No| DENY
    D3 -->|Yes| D4{spawn_rate <<br>rate_limit/min?}
    D4 -->|No| DENY
    D4 -->|Yes| D5{child_budget ≤<br>remaining?}
    D5 -->|No| DENY
    D5 -->|Yes| D6{autonomy ≤<br>ceiling?}
    D6 --> APP["✅ APPROVED<br>+ SpawnVerdict"]

    style SR fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style APP fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style DENY fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style D1 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style D2 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style D3 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style D4 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style D5 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style D6 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  agent-verse-backend/app/civilization/constitution.py:1-80
  agent-verse-backend/app/civilization/models.py:47-75
-->

## A2A Dispatch: Secure Agent-to-Agent Communication

### How It Works

The [`dispatch_internal_task()`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/a2a_dispatch.py#L24-L85) function is the only sanctioned path for one civilization agent to task another. It intentionally routes through `GoalService` — the same path as a regular user request — so **per-tenant budget limits, policy engine checks, and audit logging all fire automatically**.

```mermaid
sequenceDiagram
    autonumber
    participant SA as Source Agent
    participant ADS as A2ADispatch
    participant GS as GoalService
    participant REP as A2ARepository
    participant CB as Celery Worker
    participant TA as Target Agent

    SA->>ADS: dispatch_internal_task(from, to, goal, context)
    ADS->>ADS: verify both agents active in same civilization
    ADS->>ADS: derive task_id = UUID5(tenant:civ:idempotency_key)
    ADS->>REP: create A2ATaskRecord (status=pending)
    Note over REP: Idempotency: if record.status != pending,<br>return existing goal_id immediately
    ADS->>ADS: HMAC-SHA256 sign payload (secret per tenant)
    ADS->>ADS: inject W3C traceparent / tracestate headers<br>via opentelemetry.propagate.inject()
    ADS->>GS: submit_goal(to_agent_id, signed_context)
    GS->>CB: Celery task → goals.{plan_tier} queue
    CB->>TA: execute goal with inherited trace context
    TA-->>REP: update task_id → goal_id (status=in_progress)
    TA-->>SA: callback_url result (if provided)
```

<!-- Sources:
  agent-verse-backend/app/civilization/a2a_dispatch.py:1-90
  agent-verse-backend/app/civilization/a2a_security.py:1-80
  agent-verse-backend/app/civilization/a2a_repository.py
-->

### Security: HMAC-SHA256-v1 Signing

Every A2A payload is signed with a **per-tenant, per-purpose HMAC-SHA256** key. The `A2AKey` has a hard expiry, a purpose (`request` or `callback`), and a `revoked` flag so compromised keys can be invalidated without downtime.

| Security Property | Implementation |
|---|---|
| **Payload integrity** | `hmac-sha256-v1` signature over `json-c14n-v1` canonicalized body |
| **Replay prevention** | One-time nonce stored in `A2ANonceStore`; claimed atomically under a lock |
| **Key rotation** | `A2AKey.active_from` / `expires_at` enable rolling rotation without downtime |
| **Revocation** | `A2AKey.revoked = True` immediately blocks all uses |
| **Scope separation** | `A2AKeyPurpose.REQUEST` ≠ `A2AKeyPurpose.CALLBACK` — callback keys cannot forge requests |

Source: [a2a_security.py:1-80](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/a2a_security.py)

## Agent Spawning

### The `civilization_spawn` MCP Tool

Agents can spawn children through the [`civilization_spawn`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/spawn_tool.py#L13-L42) MCP tool — the only sanctioned mechanism. The tool definition is wired into the executor's tool set at runtime and is withheld from agents running outside a civilization context.

```json
// Source: agent-verse-backend/app/civilization/spawn_tool.py:13-42
{
  "name": "civilization_spawn",
  "parameters": {
    "capability": "jira_issue_triage",
    "goal": "Triage all open P1 issues in project PLATFORM",
    "priority": "high"
  }
}
```

### Spawn Lifecycle

```mermaid
stateDiagram-v2
    [*] --> SpawnRequested : agent calls civilization_spawn()
    SpawnRequested --> ConstitutionCheck : Governor.evaluate_spawn_request()
    ConstitutionCheck --> DENIED : any limit exceeded
    DENIED --> [*] : return error to calling agent
    ConstitutionCheck --> PolicyCheck : all limits pass
    PolicyCheck --> HITLGate : high_risk_requires_hitl=true
    PolicyCheck --> MetaAgentPlan : low-risk / pre-approved
    HITLGate --> Rejected : human rejects
    Rejected --> [*]
    HITLGate --> MetaAgentPlan : human approves
    MetaAgentPlan --> AgentCreated : agent_store.create()
    AgentCreated --> SPAWNING : CivMember status=spawning
    SPAWNING --> ACTIVE : first goal submitted
    ACTIVE --> IDLE : goal complete, no pending tasks
    ACTIVE --> FAILED : constitution breach
    IDLE --> RETIRED : idle_ttl_seconds exceeded
    FAILED --> RETIRED : Governor retires
```

<!-- Sources:
  agent-verse-backend/app/civilization/governor.py:60-130
  agent-verse-backend/app/civilization/spawn_tool.py:44-90
  agent-verse-backend/app/civilization/models.py:12-30
-->

## The Blackboard: Shared Findings Store

The [`Blackboard`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/blackboard.py#L37-L80) allows agents to post discoveries and query them before taking action — reducing duplicate work and enabling emergent coordination.

**Key behaviors:**
- **Optimistic concurrency**: each entry carries a `version` field; updates must supply the expected version, preventing conflicting overwrites.
- **Conflict detection**: when two entries on the same `topic` both exceed `confidence > 0.75`, a `debate` event is published to the `CivilizationBus`.
- **Persistence**: writes go to Postgres (via `db_session_factory`) with an in-memory cache for test/dev fallback.

## Coordination Patterns

The [`app/coordination/`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/coordination/) package implements eight pluggable multi-agent coordination strategies. Each runs as a distinct collaboration session on top of the shared `GoalService` and `CivilizationBus`.

| Pattern | Directory | Description | Best For |
|---|---|---|---|
| **CAMEL** | `coordination/camel/` | Dual-role society — AI User instructs AI Assistant in alternating turns until task complete | Creative generation, structured interviews, requirements elicitation |
| **Swarm** | `coordination/swarm/` | Decentralized — agents hand off context when they hit their specialty boundary | Long, heterogeneous pipelines where no single agent is complete |
| **GroupChat** | `coordination/group_chat/` | N agents in a moderated conversation; a manager/selector picks the next speaker | Brainstorming, multi-perspective review, committee decision-making |
| **MAGENTIC** | `coordination/magentic/` | Orchestrator LLM calls specialist agents as functions; recursion-safe | Complex compositional tasks requiring dynamic tool selection |
| **MOA** (Mixture-of-Agents) | `coordination/moa/` | Parallel proposers → aggregator; layered refinement | Consensus answers, multi-perspective synthesis, high-stakes decisions |
| **Auction** | `coordination/auction/` | Sealed-bid (`SealedBidRequest`) — agents bid; highest-scored wins task | Resource allocation, market-based scheduling, prioritized queues |
| **Handoffs** | `coordination/handoffs/` | Explicit token-based handoffs with `HandoffTransitionRequest` and acceptance tokens | Workflows with hard ownership boundaries (e.g., write → review → approve) |
| **Transcript** | `coordination/transcript/` | Ordered message transcript with sequence numbers | Audit trails, human-readable session replay |

### Pattern Decision Guide

```mermaid
flowchart TD
    Start(["What does your task need?"]) --> Q1{Multiple specialist<br>domains?}
    Q1 -->|Yes| Q2{Are they sequential<br>or parallel?}
    Q2 -->|Sequential with handoffs| SWARM["Swarm or Handoffs"]
    Q2 -->|Parallel synthesis| MOA["MOA — parallel<br>+ aggregation"]
    Q1 -->|No| Q3{Need committee<br>review?}
    Q3 -->|Yes| GC["GroupChat<br>moderated"]
    Q3 -->|No| Q4{Structured<br>dialogue?}
    Q4 -->|Dual role| CAMEL["CAMEL<br>user/assistant"]
    Q4 -->|Function composition| MAG["MAGENTIC<br>orchestrated"]
    Q4 -->|Resource allocation| AUC["Auction<br>sealed bid"]

    style Start fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style SWARM fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style MOA fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style GC fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style CAMEL fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style MAG fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style AUC fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style Q1 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style Q2 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style Q3 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style Q4 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  agent-verse-backend/app/coordination/camel/
  agent-verse-backend/app/coordination/swarm/
  agent-verse-backend/app/coordination/group_chat/
  agent-verse-backend/app/coordination/magentic/
  agent-verse-backend/app/coordination/moa/
  agent-verse-backend/app/coordination/auction/
  agent-verse-backend/app/coordination/handoffs/
  agent-verse-backend/app/coordination/transcript/
-->

## Civilization Governance Flow

The `Governor` is the **only** entity in the system that creates or retires civilization members. It is stateless between calls — all state lives in Postgres and Redis.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as 🤖 Running Agent
    participant ST as SpawnTool (MCP)
    participant Gov as Governor
    participant Const as Constitution (pure fn)
    participant Policy as PolicyEngine
    participant HITL as HITL Gateway
    participant AS as AgentStore
    participant Audit as AuditLog

    Agent->>ST: civilization_spawn(capability, goal)
    ST->>Gov: evaluate_spawn_request()
    Gov->>Gov: fetch live metrics (concurrent agents, budget spent, spawn rate)
    Gov->>Const: evaluate_spawn(SpawnContext, constitution)
    Const-->>Gov: SpawnVerdict (APPROVED | DENIED + reasons)
    alt DENIED
        Gov->>Audit: log spawn_denied event
        Gov-->>ST: SpawnVerdict.DENIED
        ST-->>Agent: {"success": false, "error": "..."}
    else APPROVED
        Gov->>Policy: check inherited_policy_ids
        alt requires HITL
            Gov->>HITL: submit approval request
            HITL-->>Gov: approved / rejected
        end
        Gov->>AS: create new agent (MetaAgentPlanner generates config)
        Gov->>Audit: log spawn_approved event
        Gov-->>ST: SpawnVerdict.APPROVED + new_agent_id
        ST-->>Agent: {"success": true, "agent_id": "..."}
    end
```

<!-- Sources:
  agent-verse-backend/app/civilization/governor.py:50-130
  agent-verse-backend/app/civilization/constitution.py:1-80
  agent-verse-backend/app/civilization/spawn_tool.py:44-90
-->

## Civilization-Level Learning

The [`LearningPipeline`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/learning.py) is the anti-poisoning gate for collective memory. Every agent can submit a learning candidate, but only those that pass EvalRunner validation (score ≥ 0.7) are promoted to `LongTermMemoryStore`.

```
candidate (submitted) → EvalRunner validate (async)
  score ≥ 0.7 → PROMOTED → LongTermMemoryStore (shared across all agents)
  score ≤ 0.35 → REJECTED → never reaches shared memory
  0.35 < score < 0.7 → re-evaluated on next tick
```

**Why this matters**: Without the gate, a single failing agent could contaminate the shared memory and degrade every subsequent agent's behavior. The `REJECTION_SCORE_THRESHOLD = 0.35` and `PROMOTION_SCORE_THRESHOLD = 0.7` leave a "quarantine band" where marginal candidates are held for re-evaluation.

## Civilization Metrics

[`civilization/metrics.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/civilization/metrics.py) exposes Prometheus metrics with lazy initialization to avoid import-time conflicts:

| Metric | Type | Labels |
|---|---|---|
| `civ_spawn_requests_total` | Counter | `civilization_id`, `verdict` (approved/denied) |
| `civ_active_members` | Gauge | `civilization_id`, `tenant_id` |
| `civ_goal_success_rate` | Gauge | `civilization_id` |
| `civ_budget_spent_usd` | Gauge | `civilization_id`, `tenant_id` |
| `civ_a2a_dispatch_duration_seconds` | Histogram | `from_agent_id`, `to_agent_id` |

## Related Pages

| Page | Description |
|---|---|
| [Agent Loop & LangGraph](agent-loop.md) | The core state machine each agent runs |
| [Reliability & Infrastructure](reliability-and-infrastructure.md) | Circuit breakers, bulkheads, rollback — the safety net under civilization |
| [Evals & Improvement](evals-and-improvement.md) | EvalRunner used by LearningPipeline to gate knowledge promotion |
| [Governance & Audit](governance-and-audit.md) | HITL gateway and audit trail consumed by the Governor |
