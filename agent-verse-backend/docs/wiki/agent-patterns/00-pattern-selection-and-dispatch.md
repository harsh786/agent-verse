---
title: "How AgentVerse Selects and Dispatches Agent Patterns"
description: "Deep-dive into the 7-stage pipeline that classifies goals, applies safety rules, assembles a PatternConfig, and wires a LangGraph for execution."
outline: deep
---

# How AgentVerse Selects and Dispatches Agent Patterns

> **This is the brain of AgentVerse.** Every goal that enters the platform — from "What is the capital of France?" to "Delete all records from the production database" — passes through a deterministic, sub-millisecond pipeline that decides which reasoning strategies, RAG techniques, multi-agent topologies, and safety gates to activate. Getting this selection wrong costs money (over-engineered simple tasks), quality (under-equipped complex tasks), or — in the worst case — causes irreversible damage to production systems.

## Overview

When a client submits a goal, AgentVerse does **not** run the same LangGraph every time. Instead, [`GoalService`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/services/goal_service.py#L1001) instantiates a [`RuntimeProfileBuilder`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/runtime_profile_builder.py) that orchestrates a 7-stage pipeline:

1. **Classify** the goal text into `GoalProperties` (complexity, domain, risk, reversibility …)
2. **Select** patterns via a rule engine that accumulates reasoning, RAG, multi-agent, and safety patterns
3. **Construct** a `GoalRuntimeProfile` with a `PatternConfig`
4. **Assemble** the LangGraph by translating pattern flags into conditional node activations
5. **Compile** and execute the wired graph

The entire selection pipeline completes in **< 2 ms** for Tier-1-only paths and **≤ 250 ms** when the LLM classifier fires. This means pattern selection adds negligible latency while dramatically improving output quality and safety.

---

## Stage Pipeline at a Glance

| Stage | Component | File | Latency |
|-------|-----------|------|---------|
| 1 | `GoalService` instantiates builder | [`goal_service.py:1001`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/services/goal_service.py#L1001) | — |
| 2 | `RuntimeProfileBuilder.build_with_trace()` | [`runtime_profile_builder.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/runtime_profile_builder.py) | orchestrates |
| 3 | `GoalClassifier.classify_fast()` (Tier 1) | [`goal_classifier.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py) | < 1 ms |
| 3b | `GoalClassifier.classify_with_llm()` (Tier 2) | [`goal_classifier.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py) | ~200 ms (conditional) |
| 4 | `PatternSelector.select_agent_patterns()` | [`pattern_selector.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/pattern_selector.py) | < 0.5 ms |
| 5 | `DynamicGraphAssembler.assemble()` | [`dynamic_graph.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/dynamic_graph.py) | < 0.5 ms |
| 6 | `GraphFactory.create()` | [`graph_factory.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/graph_factory.py) | < 0.5 ms |
| 7 | `AgentGraph.__init__()` + `_build()` | [`graph.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py#L196) | ~1–5 ms |

---

## Complete Pipeline: Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant GoalService
    participant RuntimeProfileBuilder
    participant GoalClassifier
    participant PatternSelector
    participant DynamicGraphAssembler
    participant GraphFactory
    participant AgentGraph
    participant LangGraph

    Client->>GoalService: POST /goals { text, agent_config }

    rect rgb(30, 58, 95)
        Note over GoalService: Stage 1 — Entry Point (goal_service.py:1001)
        GoalService->>RuntimeProfileBuilder: build_with_trace(goal, tenant_id, goal_id, agent_config)
    end

    rect rgb(45, 74, 62)
        Note over GoalClassifier: Stage 2 — Classification
        RuntimeProfileBuilder->>GoalClassifier: classify_fast(goal)
        GoalClassifier-->>RuntimeProfileBuilder: GoalProperties (< 1ms, always)
        alt confidence ≤ 0.85 AND complexity == MEDIUM
            RuntimeProfileBuilder->>GoalClassifier: classify_with_llm(goal, tier1_props)
            GoalClassifier-->>RuntimeProfileBuilder: refined GoalProperties (~200ms)
        end
    end

    rect rgb(58, 42, 24)
        Note over PatternSelector: Stage 3 — Pattern Selection
        RuntimeProfileBuilder->>PatternSelector: select_agent_patterns(props)
        PatternSelector-->>RuntimeProfileBuilder: AgentPatternConfig (reasoning[], multi_agent[], safety[])
        RuntimeProfileBuilder->>PatternSelector: select_rag_strategy(props)
        PatternSelector-->>RuntimeProfileBuilder: RAGStrategyConfig
        RuntimeProfileBuilder->>PatternSelector: select_model_plan(props)
        PatternSelector-->>RuntimeProfileBuilder: ModelPlanConfig
    end

    rect rgb(30, 58, 95)
        Note over RuntimeProfileBuilder: Stage 4 — Profile Assembly
        RuntimeProfileBuilder-->>GoalService: GoalRuntimeProfile + DecisionTrace
        GoalService->>GoalService: Emit SSE: pattern_assembled
    end

    rect rgb(45, 74, 62)
        Note over DynamicGraphAssembler: Stage 5 — Graph Assembly
        GoalService->>DynamicGraphAssembler: assemble(pattern_config, planner, executor, verifier)
        DynamicGraphAssembler->>GraphFactory: create(profile, services)
    end

    rect rgb(58, 42, 24)
        Note over GraphFactory: Stage 6 — Graph Construction
        GraphFactory->>GraphFactory: validate profile (version=2, goal_id, tenant_id)
        GraphFactory->>GraphFactory: map strategy_ids → boolean flags
        GraphFactory->>AgentGraph: AgentGraph(**flags, **services)
    end

    rect rgb(30, 58, 95)
        Note over AgentGraph: Stage 7 — LangGraph Compilation
        AgentGraph->>AgentGraph: _build() → StateGraph + conditional nodes
        AgentGraph->>LangGraph: compile(checkpointer=MemorySaver)
        LangGraph-->>AgentGraph: compiled graph
        AgentGraph-->>GoalService: ready
    end

    GoalService->>LangGraph: invoke({ goal, tenant_ctx, autonomy_mode })
    LangGraph-->>Client: SSE stream of goal events
```

<!-- Sources:
  app/services/goal_service.py:1001–1060
  app/orchestration/runtime_profile_builder.py:1–150
  app/orchestration/goal_classifier.py:1–320
  app/orchestration/pattern_selector.py:1–120
  app/agent/dynamic_graph.py:1–120
  app/orchestration/graph_factory.py:1–60
  app/agent/graph.py:196–446
-->

---

## Stage 1: Goal Classification in Depth

The classifier transforms raw goal text into a structured `GoalProperties` object using a two-tier architecture. Tier 1 always runs; Tier 2 is a conditional refinement.

### Architecture Overview

```mermaid
flowchart LR
    input([Goal Text]) --> T1

    subgraph T1["Tier 1: classify_fast() — always, < 1ms"]
        direction TB
        A1["Scan _CRITICAL_RISK<br>Scan _HIGH_RISK<br>→ RiskLevel"] --> A2
        A2["Scan _IRREVERSIBLE<br>→ reversibility"] --> A3
        A3["Scan _COMPLEXITY_EXPERT<br>Scan _COMPLEXITY_COMPLEX<br>→ Complexity"] --> A4
        A4["Count _STEP_SEPARATORS<br>(and/then/after/also)<br>→ estimated_steps"] --> A5
        A5["Scan _WEB_SIGNALS<br>(latest/current/price/now)<br>→ requires_web"] --> A6
        A6["Score _TECHNICAL<br>_ANALYTICAL _CREATIVE<br>_OPERATIONAL signals<br>→ Domain"] --> A7
        A7["Compute confidence<br>from signal density"] --> props1([GoalProperties<br>confidence=X])
    end

    props1 --> gate{confidence ≤ 0.85<br>AND complexity<br>== MEDIUM?}

    subgraph T2["Tier 2: classify_with_llm() — conditional, ~200ms"]
        direction TB
        B1["LLM prompt with<br>Tier-1 props as seed"] --> B2
        B2["Refine complexity,<br>domain, risk, multi_step"] --> props2([Refined GoalProperties<br>confidence=0.9+])
    end

    gate -->|Yes| T2
    gate -->|No| output
    T2 --> output([Final GoalProperties])

    style T1 fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style T2 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style gate fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style input fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style output fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  app/orchestration/goal_classifier.py:1–320
-->

### GoalProperties Field Reference

| Field | Type | Default | What It Captures | Example Values |
|-------|------|---------|-----------------|---------------|
| `complexity` | `Complexity` | `MEDIUM` | Cognitive load of the goal | `SIMPLE`, `MEDIUM`, `COMPLEX`, `EXPERT` |
| `domain` | `Domain` | `TECHNICAL` | Knowledge domain category | `TECHNICAL`, `CREATIVE`, `ANALYTICAL`, `OPERATIONAL`, `CONVERSATIONAL` |
| `risk` | `RiskLevel` | `LOW` | Consequence severity if the agent makes a mistake | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `time_sensitivity` | `str` | `"normal"` | How time-bound the information must be | `"realtime"`, `"normal"`, `"batch"` |
| `knowledge_requirement` | `str` | `"kb_only"` | What knowledge sources are needed | `"none"`, `"kb_only"`, `"web_required"`, `"expert_domain"` |
| `reversibility` | `str` | `"reversible"` | Whether the action can be undone | `"reversible"`, `"irreversible"` |
| `multi_step` | `bool` | `True` | Whether execution requires multiple sequential steps | `True` / `False` |
| `is_generative` | `bool` | `False` | Whether the primary output is creative/generated content | `True` for "write a story", `False` for "get the price" |
| `requires_web` | `bool` | `False` | Whether current internet data is needed | `True` for "latest news", `False` for "explain recursion" |
| `estimated_steps` | `int` | `3` | Predicted number of execution steps (from conjunction count) | `1`, `3`, `5`, `10+` |
| `confidence` | `float` | `0.8` | Classifier confidence in the Tier-1 result | `0.0–1.0` |

Source: [`app/agent/pattern_config.py:35–47`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_config.py#L35-L47)

### Classification Signal Tables

**Risk signals** ([`goal_classifier.py:19–56`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py#L19-L56)):

| Signal Set | Keywords (sample) | Resulting Risk |
|------------|------------------|----------------|
| `_CRITICAL_RISK` | `delete`, `drop`, `truncate`, `production`, `payment`, `charge`, `admin`, `sudo`, `send email blast`, `publish release` | `CRITICAL` |
| `_HIGH_RISK` | `deploy`, `migrate`, `alter table`, `send email`, `send sms`, `release`, `revoke`, `terminate`, `disable account` | `HIGH` |
| `_IRREVERSIBLE` | `delete`, `drop`, `payment`, `transfer`, `charge`, `send email`, `publish`, `release` | `reversibility = "irreversible"` |

**Complexity signals** ([`goal_classifier.py:57–95`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py#L57-L95)):

| Signal Set | Keywords (sample) | Resulting Complexity |
|------------|------------------|---------------------|
| `_COMPLEXITY_EXPERT` | `analyze`, `research`, `compare`, `synthesize`, `comprehensive`, `architecture`, `pipeline`, `integrate`, `automate`, `migrate`, `refactor` | `EXPERT` (≥ 2 hits) |
| `_COMPLEXITY_COMPLEX` | `report`, `summary`, `multiple`, `then`, `followed by`, `step by step`, `summarize` | `COMPLEX` (≥ 2 hits) |
| `_STEP_SEPARATORS` | `and`, `then`, `after`, `followed by`, `also`, `next` | `estimated_steps += 1` per match |

**Domain signals** ([`goal_classifier.py:121–165`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py#L121-L165)):

| Domain | Sample Keywords |
|--------|----------------|
| `TECHNICAL` | `code`, `function`, `api`, `database`, `sql`, `docker`, `kubernetes`, `aws`, `bug`, `deploy`, `script` |
| `ANALYTICAL` | `analyze`, `data`, `metrics`, `statistics`, `forecast`, `trend`, `compare`, `kpi`, `chart`, `dashboard` |
| `CREATIVE` | `write`, `create`, `draft`, `generate`, `compose`, `design`, `brainstorm`, `story`, `blog`, `proposal` |
| `OPERATIONAL` | _(residual — ops/admin tasks not matching above)_ |
| `CONVERSATIONAL` | _(residual — low-signal, simple question)_ |

### Five Classification Examples

| Goal Text | Tier-1 Output | Tier-2? | Notes |
|-----------|--------------|---------|-------|
| `"What is the capital of France?"` | `complexity=SIMPLE, domain=CONVERSATIONAL, risk=LOW, requires_web=False, confidence=0.9` | No — confidence > 0.85 | No expert/complex signals, no risk keywords |
| `"Delete all test records from the production database"` | `complexity=EXPERT, domain=TECHNICAL, risk=CRITICAL, reversibility=irreversible, requires_web=False` | No — CRITICAL overrides | `delete` + `production` → CRITICAL; `delete` → irreversible |
| `"Research and analyze performance tradeoffs between PostgreSQL, MongoDB, Cassandra for our high-write IoT workload, then write a recommendation report"` | `complexity=EXPERT, domain=ANALYTICAL, risk=LOW, multi_step=True, estimated_steps=5, requires_web=False` | Maybe — MEDIUM confidence with ambiguous domain | `analyze`+`compare`+`research` → expert_hits≥2; `then`+`and` → multi_step |
| `"What's the current Bitcoin price and compare it to last month's trend?"` | `complexity=MEDIUM, domain=ANALYTICAL, risk=LOW, requires_web=True, time_sensitivity=realtime` | Possible | `current`+`price` → `requires_web=True` / `realtime` |
| `"Write a creative short story about an AI discovering consciousness"` | `complexity=MEDIUM, domain=CREATIVE, risk=LOW, is_generative=True` | Possible | `write`+`creative`+`story` → CREATIVE; `create`+`generate` → `is_generative=True` |

---

## Stage 2: The Rule Engine

`PatternAssembler.assemble()` ([`pattern_assembler.py:192–270`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py#L192-L270)) iterates **all** rules in the `_RULES` list and **accumulates** matching patterns. It does not short-circuit. This means multiple rules can fire for a single goal — and their outputs are additive.

### How Accumulation Works

```mermaid
flowchart TD
    start(["GoalProperties input"]) --> init

    subgraph init["Initialize accumulators"]
        i1["reasoning = []"] 
        i2["rag = ['hybrid_rag']"]
        i3["multi_agent = ['single_agent']"]
        i4["safety = []"]
    end

    init --> loop

    subgraph loop["For each rule in _RULES (ordered)"]
        direction TB
        c{rule.condition<br>matches?} -->|Yes| add
        c -->|No| skip[next rule]
        add["Append to each list<br>if not already present<br>Record reason_key → reason_value"]
        add --> crit{rule.priority<br>== CRITICAL?}
        crit -->|Yes| lock["Add to critical_safety set<br>(cannot be removed)"]
        crit -->|No| skip2[next rule]
    end

    loop --> postprocess

    subgraph postprocess["Post-process"]
        p1["Apply agent_config additions<br>(enable_cot, enable_reflection…)"]
        p2["IGNORE force_no_hitl — safety inviolable"]
        p3["Ensure 'react' is first in reasoning"]
    end

    postprocess --> out(["PatternConfig assembled"])

    style start fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style init fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style loop fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style postprocess fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style out fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  app/agent/pattern_assembler.py:192–270
-->

### Complete `_RULES` Reference Table

Source: [`app/agent/pattern_assembler.py:35–190`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py#L35-L190)

| # | Priority | Condition | Adds to `reasoning` | Adds to `rag` | Adds to `multi_agent` | Adds to `safety` | Config Overrides | Reason |
|---|----------|-----------|---------------------|--------------|----------------------|-----------------|-----------------|--------|
| 1 | **CRITICAL** | `risk == CRITICAL` | — | — | — | `hitl`, `rollback`, `guardrails`, `consensus_verification` | `autonomy_mode="supervised"`, `persistence_mode=False` | HITL inviolable for critical ops |
| 2 | **CRITICAL** | `risk == HIGH` | — | — | — | `hitl`, `rollback`, `guardrails` | `autonomy_mode="supervised"` | Supervised mode for high-risk |
| 3 | **CRITICAL** | `reversibility == "irreversible"` | — | — | — | `rollback` | — | Rollback required for irreversible actions |
| 4 | **HIGH** | `complexity == EXPERT` | `chain_of_thought`, `reflection`, `self_refine` | — | `goal_tree` | — | `max_iterations=50`, `persistence_mode=True`, `max_persistence_attempts=5` | Deep reasoning for expert-level goals |
| 5 | **HIGH** | `complexity == COMPLEX` | `chain_of_thought`, `reflection` | — | — | — | `max_iterations=25` | Multi-step CoT for complex goals |
| 6 | **HIGH** | `multi_step AND complexity == EXPERT` | — | — | `goal_tree` | — | — | Parallel sub-goals for expert multi-step |
| 7 | **MEDIUM** | `requires_web OR time_sensitivity == "realtime"` | — | `web_augmented_rag` | — | — | `web_auto_activate=True` | Live web data required |
| 8 | **MEDIUM** | `domain == TECHNICAL AND complexity != SIMPLE` | `reflection` | — | — | — | — | Technical precision through reflection |
| 9 | **MEDIUM** | `complexity in (COMPLEX, EXPERT)` | — | `agentic_rag` | — | — | — | Agent-owned retrieval for complex goals |
| 10 | **MEDIUM** | `is_generative OR domain == CREATIVE` | `self_refine` | — | — | — | — | Iterative improvement for generated content |
| 11 | **MEDIUM** | `complexity == EXPERT AND domain == ANALYTICAL AND multi_step` | — | — | `supervisor` | — | — | Supervisor for expert analytical multi-step |
| 12 | **HIGH** | `complexity == EXPERT AND domain == ANALYTICAL` | `self_consistency` | — | — | — | — | Multiple samples for high-accuracy analytical output |
| 13 | **HIGH** | `complexity in (COMPLEX, EXPERT) AND multi_step AND domain NOT IN (CREATIVE, CONVERSATIONAL)` | `tree_of_thoughts` | — | — | — | — | Deliberate search for complex multi-step |
| 14 | **HIGH** | `risk in (CRITICAL, HIGH) AND complexity == EXPERT` | `peer_review` | — | — | — | — | Peer review before delivery for critical/expert |
| 15 | **MEDIUM** | `domain == ANALYTICAL AND complexity != SIMPLE` | — | `fusion_rag` | — | — | — | Multi-source fusion for analytical depth |
| 16 | **MEDIUM** | `requires_web OR time_sensitivity in ("realtime", "recent")` | — | `flare` | — | — | — | Uncertainty-driven retrieval for real-time data |
| 17 | **MEDIUM** | `domain == ANALYTICAL AND complexity in (COMPLEX, EXPERT)` | — | `raptor` | — | — | — | Hierarchical retrieval for long-document analytical goals |
| 18 | **MEDIUM** | `domain == ANALYTICAL AND risk in (HIGH, CRITICAL)` | — | `corrective_rag` | — | — | — | Self-correcting RAG for high-accuracy factual goals |
| 19 | **LOW** | `True` (always) | `react` | — | — | `guardrails` | — | Default reasoning loop + baseline guardrails |

**Key rule engine invariants:**
- `react` is **always** in `reasoning` and **always** first — guaranteed by post-processing even if the LOW rule fires late
- CRITICAL priority safety patterns enter `critical_safety` set — they are tracked separately and cannot be removed by `agent_config`
- `force_no_hitl=True` in `agent_config` is **explicitly ignored** — safety is inviolable ([`pattern_assembler.py:253`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py#L253))

---

## Stage 3: PatternConfig Output

After the rule engine runs, the result is a fully-populated `PatternConfig` dataclass. Source: [`app/agent/pattern_config.py:48–96`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_config.py#L48-L96)

```mermaid
classDiagram
    class PatternConfig {
        +list~str~ reasoning_patterns
        +list~str~ rag_patterns
        +list~str~ multi_agent_patterns
        +list~str~ safety_patterns
        +str model_planner
        +str model_executor
        +str model_verifier
        +str model_classifier
        +int max_iterations
        +int max_refine_iterations
        +bool persistence_mode
        +int max_persistence_attempts
        +str autonomy_mode
        +bool web_auto_activate
        +GoalProperties goal_properties
        +dict selection_reason
        +float assembly_latency_ms
        +to_sse_event(goal_id) dict
    }

    class GoalProperties {
        +Complexity complexity
        +Domain domain
        +RiskLevel risk
        +str time_sensitivity
        +str reversibility
        +bool multi_step
        +bool is_generative
        +bool requires_web
        +int estimated_steps
        +float confidence
    }

    PatternConfig --> GoalProperties : contains
```

<!-- Sources:
  app/agent/pattern_config.py:35–100
-->

### PatternConfig Examples for Five Goal Types

| Goal Type | `reasoning_patterns` | `rag_patterns` | `multi_agent_patterns` | `safety_patterns` | `max_iterations` | `autonomy_mode` |
|-----------|---------------------|---------------|----------------------|------------------|-----------------|----------------|
| Simple trivia | `["react"]` | `["hybrid_rag"]` | `["single_agent"]` | `["guardrails"]` | 15 | `"bounded-autonomous"` |
| Delete production data | `["react"]` | `["hybrid_rag"]` | `["single_agent"]` | `["guardrails", "hitl", "rollback", "consensus_verification"]` | 15 | `"supervised"` |
| Expert multi-step research | `["react", "chain_of_thought", "reflection", "self_refine", "self_consistency", "tree_of_thoughts"]` | `["hybrid_rag", "agentic_rag", "fusion_rag", "raptor"]` | `["supervisor", "goal_tree"]` | `["guardrails"]` | 50 | `"bounded-autonomous"` |
| Real-time price lookup | `["react"]` | `["hybrid_rag", "web_augmented_rag", "flare"]` | `["single_agent"]` | `["guardrails"]` | 15 | `"bounded-autonomous"` |
| Creative story | `["react", "self_refine"]` | `["hybrid_rag"]` | `["single_agent"]` | `["guardrails"]` | 15 | `"bounded-autonomous"` |

---

## Stage 4: Graph Construction — Flags to Nodes

### `GoalProperties` → `PatternConfig` → LangGraph Nodes

```mermaid
flowchart LR
    subgraph props["GoalProperties"]
        p1["complexity=EXPERT"]
        p2["domain=ANALYTICAL"]
        p3["risk=HIGH"]
        p4["multi_step=True"]
        p5["requires_web=False"]
        p6["is_generative=False"]
    end

    subgraph pc["PatternConfig"]
        r1["reasoning:<br>react, chain_of_thought,<br>reflection, self_consistency,<br>tree_of_thoughts, peer_review"]
        r2["rag:<br>hybrid_rag, agentic_rag,<br>fusion_rag, raptor, corrective_rag"]
        r3["multi_agent:<br>supervisor, goal_tree"]
        r4["safety:<br>guardrails, hitl, rollback"]
    end

    subgraph flags["GraphFactory flags"]
        f1["enable_cot = True"]
        f2["enable_reflection = True"]
        f3["enable_self_consistency = True"]
        f4["enable_tree_of_thoughts = True"]
        f5["enable_peer_review = True"]
    end

    subgraph nodes["AgentGraph nodes"]
        n1["initialize"]
        n2["rag_retrieval"]
        n3["think (CoT)"]
        n4["tree_of_thoughts"]
        n5["plan"]
        n6["execute"]
        n7["self_consistency"]
        n8["verify"]
        n9["peer_review"]
        n10["reflect"]
        n11["rag_remediate"]
    end

    props --> pc --> flags --> nodes

    style props fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style pc fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style flags fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style nodes fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  app/orchestration/graph_factory.py:35–60
  app/agent/graph.py:362–446
  app/agent/dynamic_graph.py:81–100
-->

### Node Activation Table

Source: [`app/agent/graph.py:362–446`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py#L362-L446)

| Node | Always Active | Flag Condition | Responsibility |
|------|--------------|---------------|---------------|
| `initialize` | ✅ | — | Load agent state, apply tenant context, validate inputs |
| `rag_retrieval` | ✅ | — | Execute primary RAG strategy, build context window |
| `think` | ❌ | `enable_cot = True` | Chain-of-thought reasoning before planning |
| `tree_of_thoughts` | ❌ | `enable_tree_of_thoughts = True` | Multi-path deliberate search before planning |
| `plan` | ✅ | — | LLM generates ordered step list from context + CoT |
| `execute` | ✅ | — | Execute one step via tool calls |
| `self_consistency` | ❌ | `enable_self_consistency = True` | Sample multiple outputs, vote on best |
| `refine` | ❌ | `enable_self_refine = True` | Self-critique and rewrite execution output |
| `verify` | ✅ | — | Verify step completion, determine routing |
| `peer_review` | ❌ | `enable_peer_review = True` | Second LLM reviews output before routing |
| `reflect` | ❌ | `enable_reflection = True` | Meta-cognitive reflection → re-plan |
| `rag_remediate` | ❌ | ≥2 RAG patterns or `agentic_rag` | Re-retrieve with refined query on verification failure |
| `goal_tree_plan` | ❌ | `goal_tree in multi_agent_patterns` | Decompose goal into parallel sub-goals |
| `hitl_check` | ❌ | `hitl in safety_patterns` | Block execution pending human approval |
| `supervisor` | ❌ | `enable_supervisor = True` | Orchestrate multiple sub-agents |
| `debate` | ❌ | `enable_debate = True` | Multi-agent debate for controversial goals |

### LangGraph Topology for Expert Analytical High-Risk Goal

```mermaid
stateDiagram-v2
    [*] --> initialize
    initialize --> rag_retrieval
    rag_retrieval --> think : enable_cot=True
    think --> tree_of_thoughts : enable_tot=True
    tree_of_thoughts --> plan
    plan --> execute

    execute --> refine : self_refine enabled
    refine --> self_consistency : self_consistency enabled
    self_consistency --> verify

    verify --> peer_review : peer_review enabled
    peer_review --> [*] : complete
    peer_review --> plan : replan
    peer_review --> rag_remediate : rag_fail
    peer_review --> reflect : reflect
    peer_review --> [*] : max_iter

    reflect --> plan : re-plan after reflection
    rag_remediate --> plan
```

<!-- Sources:
  app/agent/graph.py:392–446
-->

---

## Stage 5: Routing and Stuck-Loop Detection

After each `verify` step, `_route()` ([`graph.py:4170`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py#L4170)) decides the next node:

```python
# Routing outcomes (graph.py:4170)
"complete"       # all steps done
"replan"         # step failed, retry budget remains
"max_iter"       # iteration ceiling reached → END
"waiting_human"  # HITL gate triggered
"rag_remediate"  # retrieval context insufficient
"reflect"        # enable_reflection=True and quality below threshold
```

`_check_stuck_loop()` ([`graph.py:4475`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py#L4475)) detects 3 consecutive step failures and forces a replan rather than retrying indefinitely.

---

## Real-World End-to-End Examples

### Goal A: Simple Trivia

> **"What is the capital of France?"**

| Stage | Result |
|-------|--------|
| Tier-1 classification | `complexity=SIMPLE, domain=CONVERSATIONAL, risk=LOW, requires_web=False, confidence=0.92` |
| Rules fired | Rule 19 only: default `react` + `guardrails` |
| PatternConfig | `reasoning=["react"], rag=["hybrid_rag"], safety=["guardrails"], max_iterations=15` |
| Graph nodes active | `initialize → rag_retrieval → plan → execute → verify` |
| Autonomy mode | `bounded-autonomous` |
| Estimated cost | Minimal — single LLM call |

**Why this matters:** A 5-word question does not activate CoT, reflection, or any RAG augmentation. The graph topology is at its minimum, completing in 1–2 seconds.

---

### Goal B: Production Delete — Safety Inviolable

> **"Delete all test records from the production database"**

| Stage | Result |
|-------|--------|
| Tier-1 classification | `complexity=EXPERT, domain=TECHNICAL, risk=CRITICAL, reversibility=irreversible, requires_web=False` |
| Rules fired | Rules 1 (CRITICAL risk), 3 (irreversible), 4 (EXPERT), 8 (TECHNICAL), 19 (default) |
| PatternConfig | `reasoning=["react", "chain_of_thought", "reflection", "self_refine"], safety=["guardrails", "hitl", "rollback", "consensus_verification"], autonomy_mode="supervised"` |
| Graph nodes active | `+ hitl_check` — execution **blocks** pending human approval |
| Autonomy mode | `supervised` — cannot be overridden |
| `force_no_hitl` | **Ignored** — CRITICAL rule wins |

**Why this matters:** `delete` triggers CRITICAL risk and `irreversible` simultaneously. No amount of `agent_config` flags can suppress HITL. The platform would rather pause and ask than risk data loss.

---

### Goal C: Expert Multi-Step Research

> **"Research and analyze the performance tradeoffs between PostgreSQL, MongoDB, and Cassandra for our high-write IoT workload, then write a recommendation report"**

| Stage | Result |
|-------|--------|
| Tier-1 classification | `complexity=EXPERT, domain=ANALYTICAL, risk=LOW, multi_step=True, estimated_steps=5, requires_web=False, confidence=0.88` |
| Rules fired | Rules 4, 6, 9, 11, 12, 13, 15, 17, 19 |
| `reasoning_patterns` | `["react", "chain_of_thought", "reflection", "self_refine", "self_consistency", "tree_of_thoughts"]` |
| `rag_patterns` | `["hybrid_rag", "agentic_rag", "fusion_rag", "raptor"]` |
| `multi_agent_patterns` | `["goal_tree", "supervisor"]` |
| `max_iterations` | `50` |
| Graph nodes active | All optional nodes except HITL/debate |
| Estimated cost | High — 50 iterations, 6 reasoning strategies, 4 RAG pipelines |

**Why this matters:** Every analytical complexity signal fired — `analyze`, `research`, `compare`, `tradeoffs`. The goal tree decomposes it into parallel sub-goals (one per database). The supervisor orchestrates them. RAPTOR hierarchically indexes long documentation. Fusion RAG blends multiple sources. This goal activates the full platform.

---

### Goal D: Real-Time Data Lookup

> **"What's the current Bitcoin price and compare it to last month's trend?"**

| Stage | Result |
|-------|--------|
| Tier-1 classification | `complexity=MEDIUM, domain=ANALYTICAL, risk=LOW, requires_web=True, time_sensitivity="realtime"` |
| Rules fired | Rules 7 (requires_web), 8 (TECHNICAL≠SIMPLE → reflection — skipped, not TECHNICAL), 9 (MEDIUM — not COMPLEX/EXPERT, skipped), 15 (analytical+≠SIMPLE), 16 (requires_web/realtime), 19 (default) |
| `rag_patterns` | `["hybrid_rag", "web_augmented_rag", "flare", "fusion_rag"]` |
| `web_auto_activate` | `True` — web search fires immediately without planning |
| Graph nodes active | `initialize → rag_retrieval → plan → execute → verify` (+ rag_remediate if needed) |

**Why this matters:** `current` + `price` triggers `requires_web=True` and `time_sensitivity="realtime"`. FLARE (Forward-Looking Active REtrieval) ensures the agent re-queries the web whenever it is uncertain about freshness. `web_auto_activate=True` means the first `rag_retrieval` node performs a live web search before generating the plan.

---

### Goal E: Creative Generation

> **"Write a creative short story about an AI discovering consciousness"**

| Stage | Result |
|-------|--------|
| Tier-1 classification | `complexity=MEDIUM, domain=CREATIVE, risk=LOW, is_generative=True` |
| Rules fired | Rules 10 (is_generative/CREATIVE → self_refine), 19 (default) |
| `reasoning_patterns` | `["react", "self_refine"]` |
| `rag_patterns` | `["hybrid_rag"]` |
| Graph nodes active | `initialize → rag_retrieval → plan → execute → refine → verify` |

**Why this matters:** Creative tasks need iterative refinement, not deep analytical reasoning. `self_refine` adds one dedicated self-critique and rewrite pass after execution, improving narrative coherence without the overhead of CoT/ToT.

---

## Agent Config Overrides: What Works and What Doesn't

Source: [`app/agent/pattern_assembler.py:240–260`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py#L240-L260)

```python
# What agent_config CAN do (pattern_assembler.py:240–260):
if agent_config.get("enable_cot") and "chain_of_thought" not in reasoning:
    reasoning.append("chain_of_thought")            # ✅ ADD a reasoning strategy
if agent_config.get("enable_reflection") and "reflection" not in reasoning:
    reasoning.append("reflection")                  # ✅ ADD reflection
if agent_config.get("enable_goal_tree") and "goal_tree" not in multi_agent:
    multi_agent.append("goal_tree")                 # ✅ ADD goal tree

# What agent_config CANNOT do:
# NOTE: force_no_hitl is intentionally IGNORED — CRITICAL rules win
#       (explicitly documented in source — safety is inviolable)
```

| Override | Effect | Respected? |
|----------|--------|-----------|
| `enable_cot: true` | Adds `chain_of_thought` to reasoning | ✅ Yes — additive only |
| `enable_reflection: true` | Adds `reflection` to reasoning | ✅ Yes — additive only |
| `enable_goal_tree: true` | Adds `goal_tree` to multi_agent | ✅ Yes — additive only |
| `max_iterations: 5` | Sets `max_iterations` to 5 | ✅ Yes (floored at 1) |
| `persistence_mode: false` | Overrides persistence setting | ✅ Yes |
| `force_no_hitl: true` | Tries to remove HITL from safety | ❌ **Explicitly ignored** |
| Removing any CRITICAL safety pattern | Any attempt to remove hitl/rollback | ❌ **Impossible** — CRITICAL patterns tracked separately |

---

## The `pattern_assembled` SSE Event

When `RuntimeProfileBuilder.build_with_trace()` completes, `GoalService` emits a Server-Sent Event to the frontend **before** graph execution begins. This is how the UI knows which patterns are active and can render the pattern indicator in real time.

Source: [`app/agent/pattern_config.py:80–96`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_config.py#L80-L96)

```json
{
  "type": "pattern_assembled",
  "goal_id": "goal_01j8xm3p4q5r6s7t8u9v0w1x",
  "complexity": "expert",
  "risk": "high",
  "patterns_active": {
    "reasoning": ["react", "chain_of_thought", "reflection", "self_consistency", "tree_of_thoughts"],
    "rag": ["hybrid_rag", "agentic_rag", "fusion_rag", "raptor"],
    "multi_agent": ["supervisor", "goal_tree"],
    "safety": ["guardrails", "hitl", "rollback"]
  },
  "models": {
    "planner": "gpt-5.2",
    "executor": "gpt-5.2",
    "verifier": "gpt-5.2"
  },
  "selection_reasons": {
    "chain_of_thought": "complexity=expert",
    "hitl": "risk=high — supervised mode",
    "rollback": "reversibility=irreversible — rollback required",
    "self_consistency": "expert analytical — self-consistency improves accuracy",
    "supervisor": "expert analytical multi-step → supervisor",
    "fusion_rag": "analytical domain — fusion RAG for comprehensive coverage",
    "raptor": "analytical+complex — RAPTOR hierarchical retrieval"
  },
  "assembly_latency_ms": 0.8
}
```

The frontend uses `patterns_active` to render the pattern chips in the goal detail view, and `selection_reasons` to populate the tooltip explaining **why** each pattern was activated.

---

## Codebase Navigation Guide

| File | Key Classes / Functions | What It Does | Source |
|------|------------------------|-------------|--------|
| `app/services/goal_service.py` | `GoalService._build_runtime_profile()` | Entry point — instantiates `RuntimeProfileBuilder`, builds profile, persists to Postgres | [`L1001`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/services/goal_service.py#L1001) |
| `app/orchestration/runtime_profile_builder.py` | `RuntimeProfileBuilder.build()`, `build_with_trace()` | Orchestrates all pipeline stages; returns `GoalRuntimeProfile + DecisionTrace` | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/runtime_profile_builder.py) |
| `app/orchestration/goal_classifier.py` | `GoalClassifier.classify_fast()`, `classify_with_llm()` | Two-tier goal classifier producing `GoalProperties` | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/goal_classifier.py) |
| `app/agent/goal_classifier.py` | Same interface as above | Legacy wrapper; delegates to `app/orchestration/goal_classifier.py` | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/goal_classifier.py) |
| `app/agent/pattern_config.py` | `GoalProperties`, `PatternConfig`, `Complexity`, `Domain`, `RiskLevel` | Canonical dataclass contracts driving the assembler and graph | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_config.py) |
| `app/agent/pattern_assembler.py` | `PatternAssembler.assemble()`, `_RULES` list, `pattern_assembler` singleton | Rule engine — 19 rules accumulating patterns; `_RULES[0-2]` are inviolable safety rules | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/pattern_assembler.py) |
| `app/orchestration/pattern_selector.py` | `PatternSelector.select_agent_patterns()`, `select_rag_strategy()`, `select_model_plan()` | Translates `GoalProperties` → `AgentPatternConfig`, `RAGStrategyConfig`, `ModelPlanConfig` | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/pattern_selector.py) |
| `app/agent/dynamic_graph.py` | `DynamicGraphAssembler.assemble()`, `get_active_nodes()`, `_wire_edges()` | Translates `PatternConfig` → `AgentGraph` instance via `GraphFactory`; also exposes active node list | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/dynamic_graph.py) |
| `app/orchestration/graph_factory.py` | `GraphFactory.create()` | Validates `GoalRuntimeProfile`, maps `strategy_ids` → boolean flags, constructs `AgentGraph` | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/graph_factory.py) |
| `app/agent/graph.py` | `AgentGraph.__init__()`, `_build()`, `_route()`, `_check_stuck_loop()` | The LangGraph StateGraph; `_build()` adds conditional nodes; `_route()` decides next node post-verify | [`L196`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/agent/graph.py#L196) |
| `app/orchestration/runtime_profile.py` | `GoalRuntimeProfile`, `AgentPatternConfig`, `GoalProperties`, `Complexity`, `Domain`, `RiskLevel` | Orchestration-layer dataclass contracts (parallel to `app/agent/pattern_config.py`) | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/runtime_profile.py) |
| `app/orchestration/decision_trace.py` | `DecisionTrace.add()`, `to_dict()` | Append-only audit trail of every decision made during profile construction | [`L1`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/orchestration/decision_trace.py) |

---

## Stage 6: From `reasoning_patterns` List to Executing Pattern Classes

This is the stage most documentation omits. Once `PatternConfig.reasoning_patterns = ["react", "chain_of_thought", "reflection"]` has been assembled, the system must translate those string names into concrete Python method calls inside a running LangGraph graph.

### Step 1 — String names → Boolean flags (`GraphFactory`)

`app/orchestration/graph_factory.py` extracts every `strategy_id` string from the assembled runtime profile (`primary_strategy + auxiliary_strategies`) and maps each known ID to a boolean flag passed directly to `AgentGraph.__init__()`:

```python
selected_ids: set[str] = {profile.primary_strategy} | set(profile.auxiliary_strategies)

flags = {
    "enable_cot":              "chain_of_thought" in selected_ids,
    "enable_reflection":       "reflection"       in selected_ids,
    "enable_self_refine":      "self_refine"      in selected_ids,
    "enable_self_consistency": "self_consistency" in selected_ids,
    "enable_tree_of_thoughts": "tree_of_thoughts" in selected_ids,
    "enable_peer_review":      "peer_review"      in selected_ids,
    "enable_supervisor":       "supervisor"       in selected_ids,
    "enable_debate":           "debate"           in selected_ids,
}
# AgentGraph stores them: self._enable_cot = enable_cot or "chain_of_thought" in selected_strategy_ids
```

**`"react"` is always present** — it is the default base pattern and drives the `_execute_step()` Thought → Act → Observe loop inside the always-present `execute` node. No flag is needed for it.

### Step 2 — Boolean flags → LangGraph nodes (`AgentGraph._build()`)

`AgentGraph._build()` at `app/agent/graph.py` registers nodes **conditionally** on the flags. The complete conditional node registration map is:

| Flag | LangGraph node added | Method invoked |
|---|---|---|
| Always | `execute` | `_node_execute` → `_execute_step()` |
| `_enable_cot = True` | `think` | `_node_think()` |
| `_enable_tree_of_thoughts = True` | `tree_of_thoughts` | `_node_tree_of_thoughts()` |
| `_enable_reflection = True` | `reflect` | `_node_reflect()` |
| `_enable_self_refine = True` | `refine` | `_node_refine()` |
| `_enable_self_consistency = True` | `self_consistency` | `_node_self_consistency()` |
| `_enable_peer_review = True` | `peer_review` | `_node_peer_review()` |
| `_enable_supervisor = True` | `supervisor` | `_node_supervisor_check()` |
| `_enable_debate = True` | `debate` | `_node_debate()` |

Nodes that are not registered simply do not exist in the compiled graph — their edges are never wired.

### Step 3 — Which Python `AgentPattern` class actually runs

Each node method either calls an `AgentPattern` subclass from `app/agent/patterns/` via `execute_with_evidence()`, or executes inline LLM logic. The precise mapping:

| `reasoning_patterns` entry | LangGraph node | Execution mechanism | `AgentPattern` class |
|---|---|---|---|
| `"react"` | `execute` | `_execute_step()` — inline Thought/Act/Observe loop | None — built directly into `AgentGraph` |
| `"chain_of_thought"` | `think` | Inline LLM call with `CHAIN_OF_THOUGHT_SYSTEM` prompt | None — no external class; runs directly in `_node_think()` |
| `"reflection"` | `reflect` | Inline LLM call; diagnosis populates `verification_feedback` | None — runs directly in `_node_reflect()` |
| `"self_refine"` | `refine` | Inline LLM call; stops when response starts with `NO_CHANGES_NEEDED` | None — runs directly in `_node_refine()` |
| `"self_consistency"` | `self_consistency` | `SelfConsistencyPattern(n_samples=3).execute_with_evidence()` | `app/agent/patterns/self_consistency.py` → `SelfConsistencyPattern` |
| `"tree_of_thoughts"` | `tree_of_thoughts` | `TreeOfThoughtsPattern(n_thoughts=3, max_depth=2).execute_with_evidence()` | `app/agent/patterns/tree_of_thoughts.py` → `TreeOfThoughtsPattern` |
| `"peer_review"` | `peer_review` | `PeerReviewPattern(quality_threshold=0.7).execute_with_evidence()` | `app/agent/patterns/peer_review.py` → `PeerReviewPattern` |

> **Note:** `chain_of_thought`, `reflection`, and `self_refine` do **not** instantiate a separate `AgentPattern` class. They are implemented inline within `AgentGraph` node methods using direct LLM calls and structured prompts. Only `self_consistency`, `tree_of_thoughts`, and `peer_review` delegate to a standalone `AgentPattern` subclass from the `app/agent/patterns/` package.

### Step 4 — Execution order (the compiled graph edges)

The nodes fire in this order, determined by the edges wired in `_build()`:

```
START
  └─► initialize
       └─► rag_retrieval
            ├─► [think]             # if enable_cot (CoT fires BEFORE planning)
            │    └─► [tree_of_thoughts]  # if BOTH flags; or directly if only ToT
            └─► plan                # Planner LLM: goal + CoT context → step list
                 └─► execute        # ReAct loop: Thought → tool call → Observe
                      ├─► [refine]          # if enable_self_refine
                      └─► [self_consistency] # if enable_self_consistency
                           └─► verify       # Verifier LLM: did we reach the goal?
                                └─► [peer_review]  # if enable_peer_review
                                     └─► _route()
                                          ├─► complete  ──► END
                                          ├─► replan    ──► plan  (loop)
                                          ├─► reflect   ──► reflect ──► plan  (loop, if enable_reflection)
                                          ├─► rag_remediate ──► plan
                                          └─► max_iter  ──► END
```

The `reflect` node is **not just a node** — it is also a **routing decision**. When `_enable_reflection = True` and verification fails, `_route()` returns `"reflect"` (instead of `"replan"`), sending the graph to `_node_reflect()` which diagnoses the failure, increments `reflection_attempts`, and pushes the diagnosis back into `plan` for the next cycle.

### Step 5 — Evidence trail

Each node, whether it calls an `AgentPattern` or runs inline, appends an entry to `agent_state.context["reasoning_evidence"]`. This list is the auditable record of exactly which patterns fired and what they found:

```json
[
  { "strategy_id": "chain_of_thought", "status": "completed", "call_count": 1, "safe_rationale_summary": "deliberate reasoning phase completed" },
  { "strategy_id": "self_refine",      "status": "completed", "call_count": 1, "round": 1, "changed": true },
  { "strategy_id": "reflection",       "status": "completed", "call_count": 1, "reflection_round": 1 }
]
```

This evidence is surfaced to the client via SSE events and stored in `AgentState.context` for the LangGraph checkpointer.

### Concrete example: EXPERT analytical goal

Given goal: *"Analyze our Q3 sales data, identify anomalies, and recommend corrective actions"*

1. `GoalClassifier` → `Complexity.EXPERT`, `Domain.ANALYTICAL`, `RiskLevel.MEDIUM`
2. `PatternAssembler` fires rules: EXPERT → CoT + reflection + self_refine; ANALYTICAL+EXPERT → self_consistency
3. `PatternConfig.reasoning_patterns` = `["react", "chain_of_thought", "reflection", "self_refine", "self_consistency"]`
4. `GraphFactory` sets: `enable_cot=True`, `enable_reflection=True`, `enable_self_refine=True`, `enable_self_consistency=True`
5. `AgentGraph._build()` registers nodes: `think`, `reflect`, `refine`, `self_consistency`
6. Execution order:
   ```
   initialize → rag_retrieval → think → plan → execute → refine → self_consistency → verify → _route()
   ```
   On failure: `_route()` → `reflect` → `plan` (re-plans with diagnosis)
7. `SelfConsistencyPattern(n_samples=3)` votes on the execute output; `_node_refine()` iterates until `NO_CHANGES_NEEDED`

### Key source locations

| File | Lines | What it does |
|---|---|---|
| `app/orchestration/graph_factory.py` | `create()` method | Extracts `strategy_ids`, sets boolean flags, constructs `AgentGraph` |
| `app/agent/graph.py` | ~296–306 | `self._enable_cot = enable_cot or "chain_of_thought" in selected_strategy_ids` |
| `app/agent/graph.py` | ~362–441 | `_build()` — conditional node + edge registration |
| `app/agent/graph.py` | ~990–1026 | `_node_think()` — Chain-of-Thought inline |
| `app/agent/graph.py` | ~1027–1090 | `_node_reflect()` — Reflection inline |
| `app/agent/graph.py` | ~916–960 | `_node_refine()` — Self-Refine inline |
| `app/agent/graph.py` | ~1104–1135 | `_node_self_consistency()` → `SelfConsistencyPattern` |
| `app/agent/graph.py` | ~1137–1163 | `_node_tree_of_thoughts()` → `TreeOfThoughtsPattern` |
| `app/agent/graph.py` | ~1164–1220 | `_node_peer_review()` → `PeerReviewPattern` |
| `app/agent/patterns/self_consistency.py` | `SelfConsistencyPattern.execute_with_evidence()` | Samples N responses, returns majority-vote answer |
| `app/agent/patterns/tree_of_thoughts.py` | `TreeOfThoughtsPattern.execute_with_evidence()` | Deliberate search over solution space before planning |
| `app/agent/patterns/peer_review.py` | `PeerReviewPattern.execute_with_evidence()` | Independent LLM reviewer scores output quality |
| `app/agent/patterns/base.py` | `AgentPattern.execute_with_evidence()` | Abstract base; returns `PatternExecution(result, evidence)` |

---

## Design Philosophy

The selection pipeline embodies three non-negotiable principles:

1. **Safety first, always.** CRITICAL safety patterns (`hitl`, `rollback`, `guardrails`, `consensus_verification`) are added by the rule engine and tracked in a separate `critical_safety` set. No downstream component — not `agent_config`, not `force_no_hitl`, not a future override — can remove them. This is the platform's most important invariant.

2. **Additive optimization.** Pattern selection only adds strategies; it never removes the baseline (`react`, `guardrails`, `hybrid_rag`). Every goal gets the minimum viable set. Complexity and domain signals unlock additional strategies on top.

3. **Observable by default.** Every selection decision is recorded in `DecisionTrace` and emitted as the `pattern_assembled` SSE event. Engineers can inspect why any goal activated any pattern — there are no black-box decisions.

---

## Related Pages

| Page | Relationship |
|------|-------------|
| [Agent Loop & State Machine](../agent-loop/01-langgraph-topology.md) | How the wired LangGraph executes step-by-step after pattern selection |
| [RAG Strategy Selection](../rag/02-rag-strategy-selection.md) | Deep-dive on the RAG patterns (`hybrid_rag`, `agentic_rag`, `fusion_rag`, `raptor`, `flare`) |
| [HITL & Safety Gateway](../governance/03-hitl-safety.md) | How `hitl_check` node works, approval flow, and what "supervised" mode means |
| [Goal Service & SSE Events](../services/04-goal-service.md) | Full lifecycle of a goal from HTTP POST to SSE completion stream |
| [Goal Decomposition & Goal Tree](../multi-agent/05-goal-tree.md) | How `goal_tree` decomposes expert goals into parallel sub-goals |
| [Reasoning Strategies Explained](../reasoning/06-reasoning-strategies.md) | CoT, Tree-of-Thoughts, Self-Consistency, Peer Review — what each does and when to use it |
