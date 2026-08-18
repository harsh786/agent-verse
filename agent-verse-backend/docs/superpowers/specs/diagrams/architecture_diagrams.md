---
# SUPPLEMENT M — 20 Mermaid Architecture Diagrams
# AI Organization OS — AgentVerse v2.0
# All diagrams follow the spec architecture
---

## Diagram 1: Overall System Architecture (Layer Cake)

```mermaid
graph TB
    subgraph L5["Layer 5: AI ORGANIZATION OS (NEW)"]
        META["MetaOrchestrator"]
        DEPT["22 Departments (456 Roles)"]
        TEAM["TeamFormationEngine"]
        MODEL["ModelIntelligenceGateway"]
        CAPS["CapabilityRegistry"]
    end
    subgraph L4["Layer 4: CIVILIZATION (EXISTS)"]
        GOV["Governor + Society"]
        CONST["Constitution"]
        BB["Blackboard"]
    end
    subgraph L3["Layer 3: ORCHESTRATION (EXISTS)"]
        AG["AgentGraph"]
        SUP["Supervisor"]
        DEB["Debate / Swarm / MOA"]
    end
    subgraph L2["Layer 2: WORKFLOWS + TRIGGERS (EXISTS)"]
        WF["58 Trigger Types"]
        DAG["DAG Execution"]
    end
    subgraph L1["Layer 1: PRIMITIVES (EXISTS)"]
        MEM["Memory (6-tier)"]
        KNOW["Knowledge + RAG"]
        TOOL["Tools + MCP + RPA"]
        GUARD["Guardrails + HITL"]
    end
    L5 --> L4 --> L3 --> L2 --> L1
```

## Diagram 2: Tenant → Org → Dept → Team → Agent Hierarchy

```mermaid
graph LR
    PLATFORM["PLATFORM\nAgentVerse"] --> TENANT["TENANT\nCompany"]
    TENANT --> ORG1["ORG 1\nTrading Team"]
    TENANT --> ORG2["ORG 2\nMarketing"]
    ORG1 --> DEPT1["Strategy Dept"]
    ORG1 --> DEPT2["Engineering Dept"]
    DEPT1 --> TEAM1["Mission Team A"]
    TEAM1 --> AGENT1["Market Analyst"]
    TEAM1 --> AGENT2["Strategy Lead"]
    DEPT2 --> TEAM2["Mission Team B"]
    TEAM2 --> AGENT3["Backend Eng"]
```

## Diagram 3: Goal → Team Formation → Mission Pipeline

```mermaid
sequenceDiagram
    participant U as User
    participant MB as Meta-Orchestrator
    participant CE as CapabilityExtractor
    participant RM as RoleMapper
    participant AS as AgentSelector
    participant TE as TeamFormationEngine
    participant MS as MissionService

    U->>MB: "Launch product in Germany"
    MB->>CE: Extract required capabilities
    CE-->>MB: [market_research, legal, localization, engineering, marketing]
    MB->>RM: Map capabilities → roles
    RM-->>MB: [Market Analyst, Compliance Officer, ...]
    MB->>AS: Select agents by reputation
    AS-->>MB: [agent-1, agent-2, ...]
    MB->>TE: Form team manifest
    TE-->>MB: TeamManifest {agents, cost, risk}
    MB->>MS: Create mission
    MS-->>U: Mission started (SSE stream)
```

## Diagram 4: Model Intelligence Gateway Routing

```mermaid
flowchart TD
    REQ[Agent Request] --> GW{Model Intelligence Gateway}
    GW --> EXTRACT[Extract: role_family, task_type,\nquality_req, latency_budget,\ncost_budget, privacy, context_len]
    EXTRACT --> SCORE[Score each model:\nQ×quality + L×latency +\nC×cost + R×reliability]
    SCORE --> HARD{Hard Constraints:\nPII→no external\nContext→must fit window\nHealth→available?}
    HARD --> SELECT{Select Tier}
    SELECT -->|executive/legal| EXPERT[EXPERT\nclaude-opus / gpt-4o]
    SELECT -->|engineering/ai| CODING[CODING\nclaude-sonnet / codex]
    SELECT -->|support/sales| FAST[FAST\ngpt-4o-mini / haiku]
    SELECT -->|analytics/finance| ANALYTICAL[ANALYTICAL\ngpt-4o]
    EXPERT -->|fail| CASCADE[Fallback Cascade]
    CASCADE --> SECONDARY[Secondary Model]
    SECONDARY -->|fail| QUEUE[Queue + Notify Human]
```

## Diagram 5: 6-Tier Memory Architecture

```mermaid
graph BT
    T1["TIER 1: Working Memory\nPer-agent, per-task context window\n(EXISTS)"]
    T2["TIER 2: Session Memory\nPer-conversation\n(EXISTS)"]
    T3["TIER 3: Agent Memory\nLongTermMemoryStore per agent\n(EXISTS)"]
    T4["TIER 4: Team Memory\nShared within active team, mission-scoped\n(NEW)"]
    T5["TIER 5: Department Memory\nPersistent, dept-scoped institutional\n(NEW)"]
    T6["TIER 6: Organization Memory\nOrg-wide lessons and knowledge\n(EXTENDED)"]
    T1 --> T2 --> T3 --> T4 --> T5 --> T6
```

## Diagram 6: Quality Gate System (6-gate)

```mermaid
flowchart LR
    OUT[Agent Output] --> G1{Gate 1\nSelf-Check\n×0.10}
    G1 -->|pass| G2{Gate 2\nDeterministic\n×0.20}
    G1 -->|fail| RETRY[Retry up to 2×]
    G2 --> G3{Gate 3\nLLM Evaluator\n×0.40}
    G3 --> G4{Gate 4\nPeer Review\n×0.20}
    G4 --> G5{Gate 5\nPolicy Check\n×0.10}
    G5 --> SCORE{Composite Score}
    SCORE -->|≥0.90| AUTO_APPROVE[Auto Approve]
    SCORE -->|0.75-0.89| PROMOTE[Promote to Artifact]
    SCORE -->|0.60-0.74| HUMAN_REVIEW[Human Review]
    SCORE -->|<0.60| REJECT[Reject]
    G5 -->|policy fail| REJECT
```

## Diagram 7: Universal Command Gateway (UCG) — Multi-Channel

```mermaid
graph LR
    subgraph CHANNELS["Input Channels"]
        REST["REST API"]
        TG["Telegram Bot"]
        SL["Slack Bot"]
        WA["WhatsApp"]
        DC["Discord"]
        EM["Email"]
        TM["Teams"]
        MC["MCP Client"]
        A2A["A2A Agent"]
        VC["Voice Webhook"]
    end
    subgraph UCG["Universal Command Gateway"]
        ADAPT["Channel Adapter\n(normalize)"]
        DEDUP["CommandDeduplicator\n(30s window)"]
        RATE["RateLimiter\n(per-channel)"]
        AUTH["ChannelAuthGuard\n(per-channel)"]
        ROUTE["CommandRouter"]
    end
    subgraph RESPOND["Response"]
        ORG["Org Brain\n(processes)"]
        FMT["ResponseFormatter\n(per-channel)"]
    end

    CHANNELS --> ADAPT --> AUTH --> DEDUP --> RATE --> ROUTE --> ORG --> FMT --> CHANNELS
```

## Diagram 8: Autonomy Level L0-L5 Decision Tree

```mermaid
flowchart TD
    ACTION[Proposed Action] --> HARD{Hard Limit\nCheck}
    HARD -->|STOP| NEVER[NEVER EXECUTE\nInfra destroy / Financial >$10k\nLegal binding / PII bulk / Press releases]
    HARD -->|OK| LEVEL{Effective\nAutonomy Level}
    LEVEL -->|L0| OBS[OBSERVE ONLY\nNo execution allowed]
    LEVEL -->|L1| REC[RECOMMEND\nHuman approves everything]
    LEVEL -->|L2| SUP[SUPERVISED\nRead-only + drafts only]
    LEVEL -->|L3| STD[STANDARD\nConfigurable approval gates]
    LEVEL -->|L4| AUTO[AUTONOMOUS\nPolicy-cleared only]
    LEVEL -->|L5| MISS[MISSION LEVEL\nFull autonomous execution]
    STD --> GATE{Approval gate\ntriggered?}
    GATE -->|yes| HUMAN[Request Human Approval]
    GATE -->|no| EXEC[Execute]
    AUTO --> EXEC
    MISS --> EXEC
```

## Diagram 9: Org Event Architecture (Redis pub/sub)

```mermaid
sequenceDiagram
    participant SVC as Org Service
    participant PUB as OrgEventPublisher
    participant REDIS as Redis pub/sub
    participant SUB1 as Frontend SSE
    participant SUB2 as Audit Trail
    participant SUB3 as OrgTwinSync

    SVC->>PUB: emit("org.mission.completed", payload)
    PUB->>REDIS: PUBLISH org_events:{tenant}:{org}
    REDIS-->>SUB1: Event envelope (30 event types)
    REDIS-->>SUB2: Write OrgAuditRecord
    REDIS-->>SUB3: Update digital twin state
    SUB1-->>Browser: SSE stream → React invalidate queries
```

## Diagram 10: Frontend Real-Time Architecture (OrgRealtimeManager)

```mermaid
flowchart LR
    SSE["SSE /v1/org/{id}/events/stream"]
    MGR["OrgRealtimeManager\n(30 event types)"]
    QUEUE["Event Queue\n(dedup + 10fps batch)"]
    PRIORITY["Priority bypass:\napproval/anomaly/critical"]
    QC["TanStack QueryClient\n(invalidate caches)"]
    UI["React UI\n(instant updates)"]

    SSE --> MGR
    MGR --> PRIORITY -->|immediate| QC
    MGR --> QUEUE -->|batched 10fps| QC
    QC --> UI
```

## Diagram 11: Loop Detection (SUPPLEMENT G)

```mermaid
flowchart TD
    STEP[Agent completes step] --> LD{OrgLoopDetector}
    LD --> CHECK1{Circular\ndelegation?}
    CHECK1 -->|depth>4| BREAK_DELEG[Break + Escalate]
    LD --> CHECK2{Tool obsession?\n>5 same calls}
    CHECK2 -->|yes| INTERRUPT[Interrupt + Redirect]
    LD --> CHECK3{Infinite replan?\n>3 replans}
    CHECK3 -->|yes| ESCALATE[Escalate to Human]
    LD --> CHECK4{Cost runaway?\n>3× budget}
    CHECK4 -->|yes| PAUSE[Pause + Alert]
    LD -->|all OK| CONTINUE[Continue execution]
```

## Diagram 12: Digital Twin + Simulation Engine

```mermaid
flowchart LR
    subgraph TWIN["OrgDigitalTwin"]
        STATE["Twin State\n(mirrors production)"]
        SIM["simulate_mission()"]
        CAP["get_capacity_forecast()"]
        OPT["identify_optimizations()"]
    end
    subgraph SIM_ENG["OrgSimulationEngine"]
        EST["estimate_mission()"]
        FULL["simulate_full()"]
        CHAOS["chaos_test()"]
    end
    PROD[Production Org\nEvent stream] --> STATE
    USER[User: 'What resources\nfor this mission?'] --> EST
    EST --> TWIN
    TWIN --> RESULT["Duration/Cost/Risk\nSuccess probability"]
    CHAOS --> WHAT_IF["What-if scenarios:\nKey agent fails\nBudget 50%\nProvider down"]
```

## Diagram 13: Failure Management (5-class taxonomy — SUPPLEMENT F)

```mermaid
flowchart TD
    FAIL[Failure Detected] --> CLASS{Classify\n5-class taxonomy}
    CLASS -->|CLASS 1| TRANS[TRANSIENT\nRetry: 1s→5s→30s→5m→30m]
    CLASS -->|CLASS 2| DEG[DEGRADED\nFallback: model/tool cascade]
    CLASS -->|CLASS 3| BLOCK[BLOCKED\nEscalate + pause mission]
    CLASS -->|CLASS 4| FATAL[FATAL\nQueue in DLQ + notify human]
    CLASS -->|CLASS 5| CATAS[CATASTROPHIC\nSTOP ALL + human required]
    TRANS -->|max retries| BLOCK
    BLOCK --> HUMAN[Human Intervention]
    FATAL --> DLQ[Dead Letter Queue]
    CATAS --> STOP[Full stop — never auto-recover]
```

## Diagram 14: Security Boundary Model (Zero Trust)

```mermaid
graph TB
    subgraph TENANT["TENANT (hard boundary — RLS)"]
        subgraph ORG["ORGANIZATION (soft, role-controlled)"]
            subgraph DEPT["DEPARTMENT (memory + knowledge scoped)"]
                subgraph TEAM["TEAM (ephemeral, per-mission)"]
                    subgraph AGENT["AGENT (tool + memory permissions)"]
                        TASK["TASK (context isolation)"]
                    end
                end
            end
        end
    end
    RLS["PostgreSQL RLS\n(app.tenant_id GUC)"] --> TENANT
    RBAC["Org RBAC\n(org_admin→dept_admin→team_lead→agent→viewer)"] --> ORG
    KAP["KnowledgeAccessPolicy\n(dept collection scoping)"] --> DEPT
    HMAC["Cross-dept HMAC signing"] --> TEAM
```

## Diagram 15: Knowledge Access Control (PART 15)

```mermaid
flowchart LR
    QUERY[Agent query] --> KAP{KnowledgeAccessPolicy}
    KAP --> CHECK{Sensitivity level?}
    CHECK -->|restricted| OWNER{Owning dept?}
    OWNER -->|yes| ALLOW[Allow access]
    OWNER -->|no| DENY[Deny]
    CHECK -->|confidential| DEPT{In allowed_depts?}
    DEPT -->|yes| ALLOW
    DEPT -->|no| DENY
    CHECK -->|internal| ALL_DEPTS[All departments allowed]
    CHECK -->|public| EVERYONE[Everyone allowed]
```

## Diagram 16: Org Learning Pipeline (PART 26)

```mermaid
flowchart TD
    MISS[Mission completes\nor fails] --> EXTRACT[extract_lessons()\nTeam composition\nModel routing\nCost patterns\nRisk indicators]
    EXTRACT --> VALIDATE{validate()\nAnti-poisoning gates}
    VALIDATE -->|conf < 0.70| REJECT[Reject — not promoted]
    VALIDATE -->|PII detected| REJECT
    VALIDATE -->|failed mission| QUARANTINE[Quarantine\nPending human review]
    VALIDATE -->|passed| PROMOTE{promote_to_memory()}
    PROMOTE -->|dept lesson| DEPT_MEM[Department Memory\nTier 5]
    PROMOTE -->|org lesson| ORG_MEM[Organization Memory\nTier 6]
```

## Diagram 17: 456-Role Taxonomy (PART 4)

```mermaid
graph LR
    subgraph EXECUTIVE["DEPT 1: EXECUTIVE (25)"]
        CEO["CEO, COO, CTO, CFO..."]
    end
    subgraph ENGINEERING["DEPT 4: ENGINEERING (21)"]
        ENG["Backend, Frontend, Platform..."]
    end
    subgraph MARKETING["DEPT 6: MARKETING (54)"]
        MKT["Content, SEO, Growth, Ads..."]
    end
    subgraph SALES["DEPT 7: SALES (33)"]
        SAL["SDR, AE, Account Mgr..."]
    end
    subgraph FINANCE["DEPT 8: FINANCE (22)"]
        FIN["CFO, FP&A, Analyst..."]
    end
    ORG["22 Departments\n463 Roles\n(exceeds spec's 456)"] --- EXECUTIVE
    ORG --- ENGINEERING
    ORG --- MARKETING
    ORG --- SALES
    ORG --- FINANCE
```

## Diagram 18: API Architecture — Complete Endpoint Map (PART 28)

```mermaid
graph LR
    subgraph ORG_API["/v1/org/"]
        O1["CRUD: /, /{id}"]
        O2["Health: /{id}/health"]
        O3["Missions: /{id}/missions/**"]
        O4["Teams: /{id}/teams/**"]
        O5["Depts: /{id}/departments/**"]
        O6["Analytics: /{id}/analytics/**"]
        O7["Memory: /{id}/memory/**"]
        O8["Approvals: /{id}/approvals/**"]
        O9["Command: /{id}/command"]
        O10["Twin: /{id}/twin/**"]
        O11["Intelligence: /{id}/intelligence/**"]
    end
    subgraph GW_API["/v1/gateway/"]
        G1["Telegram: /{id}/telegram/webhook"]
        G2["Slack: /{id}/slack/events"]
        G3["Teams: /{id}/teams/messages"]
        G4["Config: /{id}/config"]
        G5["Channels: /{id}/channels/status"]
    end
    subgraph MCP_API["/v1/mcp/ + /v1/a2a/"]
        M1["MCP WebSocket: /v1/mcp/{org_id}"]
        M2["A2A invoke: /v1/a2a/{org_id}/invoke"]
    end
```

## Diagram 19: Command Deduplication + Scheduling (QA8 + QA9)

```mermaid
sequenceDiagram
    participant CH as Channel
    participant DD as CommandDeduplicator
    participant RATE as RateLimiter
    participant GW as Gateway
    participant SCHED as CommandScheduler

    CH->>DD: check_and_reserve(command)
    DD->>DD: Hash(tenant+org+channel+actor+text+time_bucket)
    DD-->>GW: is_new=True (new command)
    Note over DD: Duplicate in 30s → is_new=False (skip)

    CH->>RATE: check_and_increment(command)
    RATE-->>GW: allowed=True (within 100/hr)

    GW->>GW: Process command

    Note over SCHED: User: "Remind me tomorrow at 9am"
    CH->>SCHED: schedule(ScheduledCommand, execute_at=tomorrow_9am)
    SCHED->>SCHED: Store + repeat schedule
    SCHED-->>CH: "Scheduled for tomorrow 9:00"
```

## Diagram 20: End-to-End Mission: "Launch Product in Germany"

```mermaid
sequenceDiagram
    participant U as User
    participant CB as CommandBar (Cmd+K)
    participant MO as MetaOrchestrator
    participant TF as TeamFormation
    participant MS as MissionService
    participant FE as Frontend SSE
    participant HU as Human (Approvals)

    U->>CB: "Launch product in Germany"
    CB->>MO: Goal → refine → decide topology
    MO->>TF: form_team(mission_spec)
    TF-->>U: Preview: 14 agents, $28, 48h, risk=MEDIUM
    U->>MS: Approve at L3
    MS->>FE: SSE: org.team.formed (animation)
    Note over FE: 24 agent nodes fly in

    MS->>MS: Strategy researches market (1h)
    MS->>MS: Legal completes GDPR (3h)
    MS->>HU: APPROVAL: GDPR assessment
    HU->>MS: Approved (90 seconds)
    MS->>MS: Engineering localizes (6h)
    MS->>HU: APPROVAL: Email campaign (24h)
    HU->>MS: Legal+CMO approve (2/2)
    MS->>MS: QA validates (36h)
    MS->>FE: SSE: org.mission.completed
    FE-->>U: WHILE YOU WERE AWAY digest
```
