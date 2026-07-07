# AgentVerse — Complete Agentic Patterns Catalogue

**Author:** Platform Architecture  
**Date:** 2026-07-07  
**Purpose:** Reference for which agentic pattern to use for which problem, current implementation status in AgentVerse, and design guidance for each.

---

## 1. Overview

Agentic patterns are reusable architectural shapes for how an AI agent reasons, acts, retrieves information, coordinates with other agents, and recovers from failure. Choosing the wrong pattern for a problem wastes tokens, adds latency, and produces worse results. Choosing the right one can make the difference between a goal succeeding or failing.

This document covers:
- **Core reasoning patterns** — how a single agent thinks
- **Retrieval patterns** — how an agent accesses knowledge  
- **Multi-agent patterns** — how agents collaborate
- **Control-flow patterns** — how agents handle uncertainty and failure
- **Safety patterns** — how agents stay within bounds
- **Memory patterns** — how agents learn over time
- **Orchestration patterns** — how goals are routed and composed

For each pattern: what it is, when to use it, when NOT to use it, implementation status in AgentVerse, and how it relates to other patterns.

---

## 2. Pattern Classification Map

```
CORE REASONING            RETRIEVAL                 MULTI-AGENT
──────────────            ─────────                 ───────────
ReAct                     Naive RAG                 Supervisor
Chain-of-Thought (CoT)    Agentic RAG               Debate / Voting
Reflection                Corrective RAG (CRAG)     Goal-Tree Fanout
Self-Refinement           Speculative RAG           Ensemble
Structured Planning       Adaptive RAG              Peer-Review
Plan-and-Execute          Graph RAG                 
                          HyDE                      
CONTROL FLOW              Fusion RAG                SAFETY
────────────              Multi-Hop RAG             ──────
Persistence (Retry)       Modular RAG               HITL Gateway
Goal Decomposition        Agentic Memory            Guardrails
HITL Routing              Web-Augmented RAG         Exfiltration Guard
Circuit Breaker                                     Consensus Verification
Rollback                  MEMORY                    Permission Matrix
Budget Control            Short-term (Working)      
                          Long-term (Cross-session) ORCHESTRATION
                          Episodic                  ─────────────
                          Semantic Memory           Intent Router
                          Prospective Memory        Skill Selector
                                                    Model Router
                                                    Workflow (DAG)
                                                    Meta-Agent
```

---

## 3. Core Reasoning Patterns

### 3.1 ReAct (Reasoning + Acting)

**What it is:** The agent alternates between Thought (reasoning about what to do) and Action (calling a tool or performing a step), observing results before deciding the next action.

```
Thought: I need to find X  →  Action: search("X")  →  Observation: [results]
   →  Thought: Now I know X, I need Y  →  Action: compute(Y)  → ...
```

**When to use:**
- Tool-heavy goals (database queries, API calls, code execution)
- Goals where the next step depends on the result of the previous step
- Any goal where reasoning and acting are tightly coupled

**When NOT to use:**
- Pure knowledge retrieval (use RAG patterns instead)
- Parallel-executable tasks (use Goal-Tree instead)
- Very simple goals (overhead not worth it)

**AgentVerse status:** ✅ **CORE PATTERN** — `AgentGraph._node_execute` is ReAct. The executor LLM reasons about each step and emits tool calls. The `_execute_step` method in `graph.py` is the action loop.

**Key file:** `app/agent/graph.py:_node_execute`

---

### 3.2 Chain-of-Thought (CoT)

**What it is:** Before planning, the agent writes out its explicit reasoning — decomposing the problem, identifying ambiguities, noting constraints. This reasoning becomes part of the context for the planner.

```
Goal → [CoT node] "Let me think step by step: 
  1. This involves X which requires Y
  2. There's a risk of Z
  3. I should use approach W because..."
→ [Plan node] uses CoT output as context
```

**When to use:**
- Complex, ambiguous goals with multiple valid interpretations
- Goals involving mathematical or logical reasoning
- Goals where the planning approach is non-obvious
- High-stakes goals where reasoning errors are costly

**When NOT to use:**
- Simple, well-defined tasks (overhead with no benefit)
- Goals the agent has solved many times (use execution memory instead)
- Time-sensitive tasks (CoT adds 1–2 extra LLM calls)

**AgentVerse status:** ✅ **IMPLEMENTED** — `enable_cot=True` on an agent adds `_node_think` before `_node_plan`. Off by default (too expensive for routine goals).

**Key file:** `app/agent/graph.py:_node_think`, `app/agent/prompts.py:CHAIN_OF_THOUGHT_SYSTEM`

**Config:** Per-agent `enable_cot: true` flag

---

### 3.3 Reflection (Self-Critique on Failure)

**What it is:** When a step fails or verification fails, instead of blindly replanning, the agent reflects on WHY it failed — diagnosing the root cause — and uses this diagnosis in the next plan iteration.

```
Step fails → [Reflect node] "The step failed because:
  - I tried tool X but it returned 403 (permissions)
  - My query was too broad
  Next time I should: use tool Y, narrow the query"
→ verification_feedback updated with diagnosis
→ Planner uses feedback to replan with the lesson learned
```

**When to use:**
- Any goal with >1 iteration (reflection improves replanning quality)
- Agent is failing on the same step repeatedly
- Complex goals where failure mode is informative

**When NOT to use:**
- Single-iteration goals (no failure to reflect on)
- Simple deterministic tasks

**AgentVerse status:** ✅ **IMPLEMENTED** — `enable_reflection=True` routes verify failures through `_node_reflect` before replanning.

**Key file:** `app/agent/graph.py:_node_reflect`, `app/agent/prompts.py:REFLECTION_SYSTEM`

---

### 3.4 Self-Refinement (Iterative Improvement)

**What it is:** The agent generates output, then critiques its own output and refines it — possibly multiple times — before returning the final answer.

```
Initial output → [Self-critique] "This is incomplete because..."
→ Refined output → [Self-critique] "Better, but still missing..."
→ Final output
```

**Different from Reflection:** Reflection diagnoses a failure. Self-refinement improves a success.

**When to use:**
- Writing tasks (code, documents, summaries)
- Goals where output quality matters more than latency
- Cases where the first attempt is directionally correct but rough

**When NOT to use:**
- Time-sensitive goals
- Goals where the agent has clear success criteria (verification is enough)

**AgentVerse status:** ⚠️ **PARTIAL** — The verify→replan loop is a form of self-refinement, but there's no explicit "refine this output" step separate from full replanning. 

**Gap:** Add a lightweight `_node_refine` that asks the LLM to improve its last answer without full replanning. Cheaper than a full replan iteration.

---

### 3.5 Structured Planning (PLANNER → EXECUTOR → VERIFIER)

**What it is:** The core AgentVerse pattern. Three separate LLMs with three distinct roles — planner (decomposes), executor (acts), verifier (evaluates) — each optimised for its task.

```
Planner: "Goal: X → Steps: [1, 2, 3]"
   ↓
Executor: "Step 1 → output A"
Executor: "Step 2 → output B"  
Executor: "Step 3 → output C"
   ↓
Verifier: "All steps complete, goal achieved: yes/no + reason"
```

**When to use:** Default for almost all goals. The role separation allows:
- Different models per role (gpt-5.2 for planning, gpt-4o-mini for simple execution)
- Independent tuning of prompts
- Parallel execution of independent steps

**AgentVerse status:** ✅ **CORE PATTERN** — `AgentGraph` is built entirely on this pattern. All three roles use gpt-5.2 currently (same model, different prompts).

**Key file:** `app/agent/graph.py`, `app/agent/loop.py`

---

## 4. Retrieval Patterns (RAG Taxonomy)

### 4.1 Naive RAG

**What it is:** Retrieve → Augment → Generate. Single retrieval step, top-k chunks injected into the prompt.

```
Query → embed → vector search → top-k chunks → "Answer using these: [chunks]" → LLM
```

**When to use:** Simple factual Q&A with a well-indexed KB. Fast, predictable.

**When NOT to use:** Complex reasoning, multi-document synthesis, temporal queries.

**AgentVerse status:** ✅ This is what `KnowledgeStore.hybrid_search_db()` + `smart_context_fetch()` does per-step.

---

### 4.2 Hybrid RAG (Vector + Lexical + Fuzzy)

**What it is:** Three retrieval legs fused with Reciprocal Rank Fusion (RRF): vector similarity (semantic), full-text search (keyword), and trigram fuzzy matching (typo-tolerant). RRF combines rank lists without needing normalised scores.

```
Query → [parallel]
  vector ANN (pgvector, HNSW)     → rank list A
  FTS (tsvector, ts_rank_cd)      → rank list B  
  trigram (pg_trgm)               → rank list C
→ RRF fusion → top-k results
```

**When to use:** Default for most production RAG. Better recall than any single leg alone.

**AgentVerse status:** ✅ **FULLY IMPLEMENTED** — `app/rag/engine.py` `hybrid_search()` with RRF_K=60.

---

### 4.3 HyDE (Hypothetical Document Embeddings)

**What it is:** Instead of embedding the query directly, use an LLM to generate a hypothetical document that ANSWERS the query, then embed THAT document and search for real documents similar to it.

```
Query: "How does RAFT consensus work?"
→ LLM generates: "RAFT is a consensus algorithm where a leader is elected..."
→ Embed that hypothetical document
→ Vector search: find real documents similar to the hypothetical
→ Real documents retrieved are semantically closer to the answer
```

**When to use:** Queries are short/ambiguous. The KB uses technical/domain language that differs from user query language.

**When NOT to use:** Real-time queries (adds LLM call latency). When KB is small enough that direct search works.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/rag/engine.py:retrieve_hyde()` and `app/rag_platform/retriever.py:_hyde_retrieve()`

---

### 4.4 Multi-Hop RAG

**What it is:** Iterative retrieval where each retrieval step uses the results of the previous to formulate the next query. Models chains of reasoning across documents.

```
Query: "What are the implications of X on Y?"
  Hop 1: retrieve("X") → facts about X
  Hop 2: retrieve("Y implications of " + X_facts[:50]) → bridge docs
  Hop 3: retrieve("synthesis of X and Y") → conclusion docs
→ Combine all hops → answer
```

**When to use:** Multi-document reasoning, "why" and "how" questions, cross-referencing between topics.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/rag/engine.py:retrieve_multi_hop()`. Triggered by `RetrievalPlanner` when query contains "how does", "why did", "what caused".

---

### 4.5 Graph RAG

**What it is:** Use a Knowledge Graph to expand retrieval beyond direct text matches. Retrieve seed entities, traverse relationships, and include connected entities as context.

```
Query: "dependencies of component X"
→ Find entity node: X
→ Traverse edges: X → [depends_on] → A, B, C
→ Traverse: A → [uses] → D
→ Context: X, A, B, C, D with relationship descriptions
```

**When to use:** Relationship questions ("what depends on X?", "who caused Y?"), lineage tracing, impact analysis.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/knowledge_graph/store.py` + `_graph_expand()` in RAGRetriever. Entity extraction via regex + LLM in `app/knowledge_graph/extractor.py`.

---

### 4.6 Corrective RAG (CRAG)

**What it is:** After retrieval, score the relevance of retrieved documents. If all documents score below a threshold, trigger a web search. If some are relevant and some aren't, filter the irrelevant ones before generating.

```
Query → retrieve → score each chunk
  ALL chunks low relevance → web search (corrective action)
  SOME chunks low relevance → filter + use only high-relevance ones
  ALL chunks high relevance → proceed normally
```

**When to use:** KB may be stale or incomplete. High-accuracy requirements.

**AgentVerse status:** ⚠️ **PARTIAL** — Confidence check exists in `RetrieverTool` (being built). Web fallback exists. The scoring + filter step is not explicit.

**Gap:** Add explicit relevance scoring of chunks BEFORE injection. Currently all top-k chunks are injected regardless of individual scores.

---

### 4.7 Adaptive RAG

**What it is:** Classify the query complexity, then choose the RAG strategy adaptively:
- Simple factual → Naive RAG (fast)  
- Complex reasoning → Multi-Hop RAG  
- Relationship question → Graph RAG  
- Domain unknown → Web search

```
Query → classify(complexity, domain) → route to appropriate RAG strategy
```

**When to use:** Production systems with diverse query types where no single strategy fits all.

**AgentVerse status:** ✅ **IMPLEMENTED** — `QueryPlanner.select_strategy()` in `app/rag_platform/query_planner.py` and `RetrievalPlanner` in `app/rag/engine.py`. **But:** the classification uses simple keyword matching, not an LLM classifier.

**Gap:** Replace keyword matching with a lightweight classifier (could be a fast model or simple embedding-based classifier).

---

### 4.8 Modular RAG (Pipeline Composition)

**What it is:** RAG as composable modules: Query Rewriter → Retriever → Reranker → Filter → Generator → Citation Verifier. Each module can be swapped independently.

```
Query → [Query Rewriter] → better_query
      → [Retriever] (vector/graph/web) → raw_chunks  
      → [Reranker] → ranked_chunks
      → [Relevance Filter] → filtered_chunks
      → [Generator] → answer
      → [Citation Verifier] → grounded_answer
```

**When to use:** Always. This is the structural pattern underlying all good RAG systems.

**AgentVerse status:** ✅ **IMPLEMENTED** — `RAGRetriever` in `app/rag_platform/retriever.py` follows this pipeline: retrieve → rerank → synthesize → verify citations. The RetrieverTool (being built) extends this further.

---

### 4.9 Speculative RAG

**What it is:** In parallel: (A) generate a draft answer from parametric LLM knowledge, (B) retrieve from KB. Compare: if retrieval contradicts the draft, use retrieval; if they agree, use combined; if retrieval is empty, use draft with low-confidence flag.

```
[parallel]
  Branch A: LLM generates draft answer from parametric knowledge
  Branch B: Retrieval from KB
→ Compare → reconcile → final answer
```

**When to use:** When KB may not have the answer (new tenants, thin KBs). Reduces latency vs. waiting for retrieval before generation.

**AgentVerse status:** ❌ **NOT IMPLEMENTED**

**Gap:** Implement as an option in `RetrieverTool`. Useful for goals where the agent probably knows the answer but KB might have domain-specific details.

---

### 4.10 Agentic RAG (Retrieval as a Tool)

**What it is:** The agent calls retrieval as an explicit tool it can invoke zero, one, or many times per step. The agent decides WHEN to retrieve, WHAT to search for, and HOW MANY times to iterate.

```
Step: "Research X"
  Agent thinks: "I need to search for X first"
  Agent calls: retrieve_context(query="X definition", strategy="hybrid")
  Agent observes: [chunks about X]
  Agent thinks: "I need more detail on X's relationship to Y"
  Agent calls: retrieve_context(query="X and Y interaction", strategy="graph")
  Agent observes: [more chunks]
  Agent generates: step output with full context
```

**When to use:** Complex research tasks. Any goal where retrieval needs vary per step.

**AgentVerse status:** 🚧 **BEING BUILT** — Design complete (`docs/architecture/2026-07-07-agentverse-agentic-rag-design.md`). `RetrieverTool` is the implementation vehicle.

---

### 4.11 Web-Augmented RAG

**What it is:** Extends KB retrieval with live web search. When KB has no relevant content (or confidence is low), automatically search the web and inject results.

**When to use:** 
- KB may be stale (recent events, current prices, live data)
- KB is empty (new tenant onboarding, agent with no documents)
- Technical lookups (API docs, library versions)

**AgentVerse status:** ⚠️ **PARTIAL** — `WebSearchTool` exists in `app/tools/web_search.py` (SearxNG + DuckDuckGo). Not yet auto-activated when KB is empty. Being wired in Phase A of agentic RAG plan.

---

### 4.12 Fusion RAG

**What it is:** Generate multiple search queries from a single original query (query expansion), run all of them, then fuse results with RRF. Addresses the problem that a single query may not capture all relevant documents.

```
Original query: "authentication flow"
→ Generated variants:
    "user login sequence"
    "token validation process"  
    "OAuth authentication steps"
→ Run all 4 queries → collect results → RRF → top-k
```

**When to use:** Technical domains with multiple valid phrasings. KB uses different terminology than users.

**AgentVerse status:** ⚠️ **PARTIAL** — Query reformulation is planned in `RetrieverTool` (2 attempts). Full multi-query fusion not implemented.

**Gap:** Add `QueryExpander` that generates N query variants, runs them all, applies RRF.

---

## 5. Multi-Agent Patterns

### 5.1 Supervisor-Subagent

**What it is:** A supervisor agent decomposes the goal, assigns subtasks to specialised subagents, monitors their progress, and synthesizes their results.

```
Supervisor: "Goal: Build a report on X"
  → Subagent A: "Research X background"
  → Subagent B: "Find recent data on X"
  → Subagent C: "Analyse implications of X"
→ Supervisor: synthesize A + B + C → final report
```

**When to use:**
- Goals that naturally split into independent specialised tasks
- Long-horizon goals where no single agent has all tools
- Tasks requiring different models for different subtasks

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/supervisor.py:SupervisorAgent`. Subagents run as separate async tasks. Results are collected and fed back to the supervisor for synthesis.

**Key file:** `app/agent/supervisor.py`

---

### 5.2 Debate / Voting

**What it is:** N agents independently generate proposals for the same goal. Each agent critiques the others' proposals. A vote determines the best approach. Majority or supermajority required to proceed.

```
Agent 1: Proposal A + critiques of B and C
Agent 2: Proposal B + critiques of A and C
Agent 3: Proposal C + critiques of A and B
→ Vote: A=2, B=1, C=0 → proceed with A
```

**When to use:**
- High-stakes decisions where a single agent may be biased
- Creative tasks where multiple valid approaches exist
- Adversarial settings (red-team scenarios)

**When NOT to use:**
- Routine goals (3x cost with no benefit)
- Time-sensitive tasks

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/debate.py:DebateOrchestrator`. N agents, proposal + critique + vote phases.

**Key file:** `app/agent/debate.py`

---

### 5.3 Goal-Tree (Parallel Fanout)

**What it is:** Decompose a goal into a dependency graph of sub-goals. Execute independent sub-goals in parallel. Execute dependent sub-goals sequentially after their dependencies complete.

```
Goal: "Analyse and report on A, B, C"
→ Sub-goals:
    analyse_A (no dependencies) ──┐
    analyse_B (no dependencies) ──┼─→ [parallel]
    analyse_C (no dependencies) ──┘
    synthesise (depends on A, B, C) → [sequential after parallel]
```

**When to use:**
- Goals with clearly parallelisable subtasks
- Research tasks across multiple sources
- Any goal where waiting for one subtask before starting another is wasteful

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/goal_tree.py:GoalTreeExecutor`. LLM decomposes the goal into a dependency graph. `asyncio.gather` for parallel execution.

**Key file:** `app/agent/goal_tree.py`

**Config:** Per-agent `enable_goal_tree: true` flag

---

### 5.4 Consensus Verification

**What it is:** For high-stakes goals, run verification by multiple independent verifiers (different models, different providers). Require majority agreement to mark goal as complete.

```
Verifier 1 (primary): success=true
Verifier 2 (cross-model): success=true
Verifier 3 (LLM judge): success=false, score=0.4
→ Majority: 2/3 success → complete
→ But: judge dissented → emit warning, flag for human review
```

**When to use:**
- Destructive or irreversible actions
- Regulated domains (financial, medical, legal)
- Goals where incorrect completion has high cost

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/consensus.py:ConsensusVerifier`. High-risk tool detection triggers multi-verifier mode.

**Key file:** `app/agent/consensus.py`

---

### 5.5 Peer Review

**What it is:** After the executor generates output, a separate "reviewer" agent critiques it before it's passed to the verifier. The reviewer may request corrections.

```
Executor output → [Peer Review Agent] → "Missing X, incorrect Y" → Executor revises
                                     → "Output looks good" → Verifier
```

**When to use:** Writing tasks (code, documentation). When output quality is paramount.

**AgentVerse status:** ❌ **NOT IMPLEMENTED** — Could be built as a node between execute and verify.

---

## 6. Control-Flow Patterns

### 6.1 Persistence (Smart Retry with Strategy Rotation)

**What it is:** When a goal fails, don't just retry identically. Rotate through a strategy sequence: same approach → different tools → simplify → decompose → human guidance → escalate.

```
Attempt 1: SAME_APPROACH → fail
Attempt 2: DIFFERENT_TOOLS → fail  
Attempt 3: SIMPLIFY → partial success
Attempt 4: DECOMPOSE → sub-goal 1 success, sub-goal 2 fail
Attempt 5: HUMAN_GUIDANCE → human clarifies → success
```

**When to use:** Long-horizon goals. Any goal where single-attempt failure is expected.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/persistence.py:GoalPersistenceEngine`. Up to 10 attempts, configurable strategy sequence, exponential backoff, escalation.

**Key file:** `app/agent/persistence.py`

**Config:** `persistence_mode: true` in goal's execution_context

---

### 6.2 HITL (Human-in-the-Loop) Gateway

**What it is:** High-risk steps are paused and submitted for human approval before execution. The agent waits (with timeout) for a human to approve, reject, or modify the action.

```
Step: "DELETE all records matching criteria"
→ Risk assessment: HIGH
→ [HITL Gateway] submit_approval(step, risk=HIGH, tenant=X)
→ Human reviews → APPROVE or REJECT with reason
→ If APPROVE: execute
→ If REJECT: inject rejection note → replan avoiding the rejected action
```

**When to use:**
- Destructive operations (delete, drop, wipe)
- Production deployments
- Financial transactions above a threshold
- Any action matching the high-risk keyword list

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/governance/hitl.py`, wired in `_execute_step`. HITL rejection notes are fed back to the planner to avoid repeating the rejected action.

**Key file:** `app/governance/hitl.py`, `app/agent/graph.py:_execute_step`

---

### 6.3 Budget Control (Cost Circuit Breaker)

**What it is:** Track cumulative cost per goal. When cost exceeds the budget, downgrade to cheaper models or stop execution. Prevents runaway spending on infinite loops.

```
Budget: $0.50/goal
  After step 1: cost=$0.08 (22% of budget)
  After step 2: cost=$0.21 (42% of budget, "standard" tier — downgrade to gpt-4o-mini for execution)
  After step 3: cost=$0.38 (76% of budget, "economy" tier — downgrade verification model)
  After step 4: cost=$0.51 — HARD STOP, emit goal_failed with budget_exceeded reason
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/governance/cost.py:CostController` + `app/governance/cost.py:RedisCostController`. Cost tiers (standard/economy) trigger model downgrade in `_node_plan`.

**Key file:** `app/governance/cost.py`

---

### 6.4 Rollback (Compensating Transactions)

**What it is:** For each tool execution, register an "undo" action. If the goal fails, execute the undo actions in reverse order to restore prior state.

```
Step 1: create_file("report.pdf") → rollback: delete("report.pdf")
Step 2: send_email("report.pdf") → rollback: (irreversible — mark as warning)
Step 3: update_db(record_id, new_data) → rollback: update_db(record_id, old_data)
→ Goal fails at step 3
→ Rollback: undo step 3, skip step 2 (irreversible), undo step 1
```

**When to use:** Goals with side effects. Any goal that modifies external state.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/reliability/rollback.py:RollbackEngine` + `app/reliability/tool_inverses.py`. LIFO rollback point registration per step.

**Key file:** `app/reliability/rollback.py`

---

### 6.5 Circuit Breaker (Provider + Tool)

**What it is:** Track failure rate for each tool/provider. When failure rate exceeds a threshold, open the circuit (stop calling that tool/provider) for a cooldown period.

```
Tool: web_search
  Call 1: timeout → failure_count=1
  Call 2: timeout → failure_count=2
  Call 3: 500 error → failure_count=3
  → Circuit OPEN (threshold=3) → cooldown=60s
  Call 4: circuit open → skip, return "Circuit open, step skipped"
  → After 60s: circuit HALF-OPEN → try one call
  Call 5: success → circuit CLOSED
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/reliability/circuit_breaker.py:CircuitBreaker` (in-process) + `app/reliability/redis_circuit_breaker.py:RedisCircuitBreaker` (cross-replica). Wired per-connector and for the LLM provider in `_make_agent_loop_for_tenant`.

---

## 7. Safety Patterns

### 7.1 Guardrails (Input + Output Filtering)

**What it is:** Inspect every tool call input and every LLM output for: PII, prompt injection, secrets, toxicity, jailbreaks. Apply configurable actions: ALLOW, WARN, REDACT, BLOCK, REQUIRE_HUMAN_REVIEW.

**When to use:** Always, on every goal. Multiple layers:
1. TOOL_ARGS: before any tool is called
2. TOOL_OUTPUT: after tool returns
3. FINAL_OUTPUT: before returning to user

**AgentVerse status:** ✅ **FULLY IMPLEMENTED** — `app/guardrails_v2/engine.py`. 9 layers, 6 actions, PII/secrets/injection patterns. Wired in `graph.py` and `loop.py`.

**Key file:** `app/guardrails_v2/engine.py`

---

### 7.2 Exfiltration Guard

**What it is:** Detect when the agent is about to send sensitive data to an external destination it shouldn't. Checks for: API keys in URL params, PII in POST bodies to external URLs, secrets in tool arguments.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/exfil_guard.py`

---

### 7.3 Grounding Checker (Hallucination Detection)

**What it is:** After each executor step, verify that specific claims in the output are actually supported by tool outputs or retrieved context. Claims with no supporting evidence are flagged.

```
Step output: "The API returns a 200 status code"
Tool output: {"status": 404, "error": "Not found"}
→ Grounding check: FAIL — "200 status" contradicts tool output → flag
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/grounding.py:GroundingChecker`. Fired after each executor step.

---

### 7.4 Permission Matrix

**What it is:** Define per-tenant, per-tool permission levels: ALLOW_LOG (execute but audit), REQUIRE_APPROVAL (HITL), DENY (block entirely).

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/governance/permissions.py:PermissionMatrix`

---

## 8. Memory Patterns

### 8.1 Short-Term / Working Memory

**What it is:** The current goal's context: steps executed so far, outputs, intermediate results. Scoped to a single goal execution. Cleared when goal completes.

**AgentVerse status:** ✅ **CORE** — `AgentState` object. `state.steps`, `state.context`.

---

### 8.2 Execution Memory (Episodic)

**What it is:** After a goal succeeds, store the winning plan in memory. On future similar goals, recall the past plan and use it to guide the new plan. Avoids rediscovering the same approach.

```
Goal "analyse Python code coverage" → solved via [pytest --cov, coverage.xml parse]
→ Stored in execution memory

Next goal "check test coverage for my project":
→ Recall: found similar past goal → inject past plan as context
→ Planner reuses the approach instead of reinventing it
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/memory/execution.py:ExecutionMemory`. Async pgvector recall.

---

### 8.3 Long-Term Memory (Cross-Session)

**What it is:** Learnings that persist across goals and users. Domain facts, user preferences, agent skills. Stored with embeddings for semantic recall.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/memory/long_term.py:LongTermMemoryStore`. pgvector semantic search, lifecycle management (active → stale → archived).

---

### 8.4 Prospective Memory (Future-Scheduled Actions)

**What it is:** The agent can schedule future actions during execution. "At 9am tomorrow, check if the report was sent."

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/triggers/store.py:ScheduleStore` + `app/triggers/nl_scheduler.py:NLScheduler`. Natural language → cron trigger.

---

### 8.5 Semantic Memory (Knowledge as Memory)

**What it is:** Store domain knowledge (facts, procedures, concepts) in a searchable semantic store. Different from episodic: this is general knowledge, not goal-specific experience.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/knowledge/store.py:KnowledgeStore` (documents + chunks with embeddings). Also: `app/knowledge_graph/` for entity-relationship knowledge.

---

## 9. Orchestration Patterns

### 9.1 Intent Router

**What it is:** Analyse an incoming goal and route it to the most appropriate agent based on: goal content, required tools, agent specialisation, and historical performance.

```
Goal: "Check our Kubernetes cluster health"
→ Router: embeddings similarity + keyword match
→ Agent A (DevOps specialist, k8s tools): confidence=0.91
→ Agent B (monitoring specialist): confidence=0.72
→ Route to Agent A
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/router.py:AgentRouter`. Score = embedding similarity + keyword match + historical success rate.

---

### 9.2 Skill Selector

**What it is:** From the available skills (code review, RAG eval, security audit, etc.), select the top 1–3 most relevant skills for a given goal and inject their instructions into the agent's context.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/skill_selector.py:SkillSelector`. Embedding-based trigger matching. Returns top skills ranked by similarity.

---

### 9.3 Model Router (Per-Task Model Selection)

**What it is:** For each task type (planning, execution, verification, embedding, classification), select the optimal model: best quality for planning, cheapest for simple execution, fastest for classification.

```
Task type: planning → gpt-5.2 (quality)
Task type: simple execution → gpt-4o-mini (cheap)
Task type: verification → gpt-5.2 (quality, high stakes)
Task type: embedding → text-embedding-3-small (fast, cheap)
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/model_router.py:ModelRouter`. Also `app/ai_router/router.py:AIRouter` (11 models × 5 providers, routing modes).

---

### 9.4 Workflow (Static DAG Execution)

**What it is:** Pre-defined workflow where steps, their order, and dependencies are specified upfront. No LLM planning needed. Deterministic.

```
Workflow: "Deploy pipeline"
  step_1: run_tests (dependencies: [])
  step_2: build_image (dependencies: [step_1])
  step_3: push_image (dependencies: [step_2])
  step_4: deploy (dependencies: [step_3])
```

**When to use:** Well-defined repetitive processes. CI/CD. Data pipelines. Any process where the steps are known and don't need LLM planning.

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/agent/workflow_planner.py:WorkflowPlanner` + `app/agent/workflow_executor.py:WorkflowExecutor`. Frontend workflow builder.

---

### 9.5 Meta-Agent (Agent that Creates Agents)

**What it is:** A meta-agent accepts a natural language description of a new agent and generates its full configuration: system prompt, tool list, memory settings, autonomy mode, model selection.

```
Input: "Create an agent that monitors our Postgres database and alerts on slow queries"
Meta-Agent outputs:
  name: "Postgres Monitor"
  system_prompt: "You monitor Postgres databases..."
  tools: ["postgres", "alerting", "slack"]
  autonomy_mode: "bounded-autonomous"
  model: "gpt-5.2"
  enable_goal_tree: true
```

**AgentVerse status:** ✅ **IMPLEMENTED** — `app/intelligence/meta_agent.py:MetaAgentPlanner`. Takes NL description → structured agent config.

---

## 10. Pattern Selection Matrix

Use this table to choose the right pattern(s) for your goal type:

| Goal Type | Primary Pattern | Supporting Patterns | Avoid |
|---|---|---|---|
| Simple factual Q&A | Naive RAG | Hybrid RAG | Multi-Hop RAG, Debate |
| Complex research | Agentic RAG | Multi-Hop, Graph RAG, Web-Augmented | Single-pass RAG |
| Code generation | Structured Plan | Self-Refinement, Peer Review | Goal-Tree |
| Data analysis | Goal-Tree | Supervisor, Multi-Hop RAG | Debate |
| Production deployment | HITL Gateway | Consensus Verification, Rollback | Full Autonomy |
| Long multi-step goal | Persistence | Reflection, Strategy Rotation | Single attempt |
| Multi-domain research | Supervisor | Goal-Tree, Agentic RAG | Single agent |
| High-stakes decision | Debate/Voting | Consensus, HITL | Single verifier |
| Repetitive process | Workflow DAG | Execution Memory | LLM Planning |
| Creative writing | CoT + Self-Refinement | Debate | Workflow DAG |
| Security audit | Guardrails | Grounding Checker, Exfil Guard | Full Autonomy |
| Unknown domain | Web-Augmented RAG | HyDE, Adaptive RAG | KB-only RAG |

---

## 11. Current AgentVerse Pattern Coverage

### Fully Implemented ✅

| Pattern | Key File | Activation |
|---|---|---|
| ReAct | `graph.py:_node_execute` | Default |
| Structured Plan | `graph.py`, `loop.py` | Default |
| Chain-of-Thought | `graph.py:_node_think` | `enable_cot: true` |
| Reflection | `graph.py:_node_reflect` | `enable_reflection: true` |
| Hybrid RAG | `rag/engine.py` | Default when KB has docs |
| HyDE | `rag/engine.py:retrieve_hyde` | `strategy="hyde"` |
| Multi-Hop RAG | `rag/engine.py:retrieve_multi_hop` | Auto via planner |
| Graph RAG | `rag_platform/retriever.py` | `strategy="graph"` |
| Adaptive RAG | `rag_platform/query_planner.py` | Default |
| Modular RAG | `rag_platform/retriever.py` | Default |
| Web-Augmented RAG | `tools/web_search.py` | When KB empty (being wired) |
| Supervisor | `agent/supervisor.py` | `workflow_mode="multi_agent"` |
| Debate / Voting | `agent/debate.py` | `workflow_mode="debate"` |
| Goal-Tree | `agent/goal_tree.py` | `enable_goal_tree: true` |
| Consensus Verification | `agent/consensus.py` | High-risk tools |
| Persistence | `agent/persistence.py` | `persistence_mode: true` |
| HITL Gateway | `governance/hitl.py` | High-risk keywords |
| Budget Control | `governance/cost.py` | All goals |
| Rollback | `reliability/rollback.py` | All goals |
| Circuit Breaker | `reliability/circuit_breaker.py` | All goals |
| Guardrails | `guardrails_v2/engine.py` | All goals |
| Exfil Guard | `agent/exfil_guard.py` | All goals |
| Grounding Checker | `agent/grounding.py` | All goals |
| Permission Matrix | `governance/permissions.py` | All goals |
| Execution Memory | `memory/execution.py` | All goals |
| Long-Term Memory | `memory/long_term.py` | All goals |
| Prospective Memory | `triggers/store.py` | Schedule goals |
| Intent Router | `agent/router.py` | Auto-routing |
| Skill Selector | `agent/skill_selector.py` | All goals |
| Model Router | `agent/model_router.py` | Per-agent config |
| Workflow DAG | `agent/workflow_planner.py` | `workflow_mode="workflow"` |
| Meta-Agent | `intelligence/meta_agent.py` | Admin API |

### Partial / In Progress ⚠️

| Pattern | Gap | Plan |
|---|---|---|
| Agentic RAG | RetrieverTool not yet built | Phase A of RAG plan |
| Corrective RAG | No chunk-level relevance filter | Add to RetrieverTool |
| Fusion RAG | Only 2 reformulations, no query expansion | Add QueryExpander |
| Web-Augmented RAG | Not auto-activated on empty KB | Phase A of RAG plan |
| Speculative RAG | Not implemented | Phase D |

### Not Implemented ❌

| Pattern | Priority | Effort |
|---|---|---|
| Self-Refinement (explicit) | MEDIUM | Low — add `_node_refine` before verify |
| Peer Review | LOW | Medium — new agent role |
| Speculative RAG | MEDIUM | Medium — parallel draft + retrieval |
| Fusion RAG (query expansion) | MEDIUM | Low — QueryExpander module |

---

## 12. Pattern Interaction Map

Patterns do not operate in isolation. This map shows how they compose:

```
Goal submitted
     │
     ▼
[Intent Router] ──selects→ Agent (tool list, system prompt, specialisation)
     │
     ▼
[Skill Selector] ──injects→ Skill instructions into context
     │
     ▼
[RAG Prime] ──parallel──→ [Hybrid RAG] + [Graph RAG] + [Memory] + [Web RAG if needed]
     │                          ↑
     │                    [Adaptive RAG] selects which legs to run
     ▼
[CoT Node?] ──→ explicit reasoning before planning
     │
     ▼
[Planner: gpt-5.2]
     │
     ▼
[Execute Loop] for each step:
  [ReAct] reason → act
  [Agentic RAG] explicit retrieval calls
  [Guardrails] check tool args + output
  [Permission Matrix] tool allowed?
  [HITL] high-risk?  
  [Circuit Breaker] tool/LLM available?
  [Exfil Guard] about to send secrets?
  [Grounding Check] output contradicts tool results?
  [Budget Control] within spend limit? → [Model Router] downgrade if needed
     │
     ▼
[Verifier: gpt-5.2]
  [Consensus?] if high-risk → 3 verifiers, majority vote
  [Citation Check] output grounded in retrieved context?
     │
     ├── success → complete
     ├── context gap → [RAG Remediate] → replan with new context
     ├── retryable → [Reflection] diagnose → replan
     ├── retry=False → fail
     └── max_iterations → [Persistence] strategy rotation → retry
                               ↓
                     [Rollback] undo side effects
```

---

## 13. Recommended Per-Goal-Type Configurations

### Research & Analysis Goals
```yaml
enable_cot: true
enable_reflection: true  
enable_goal_tree: true    # parallel research branches
rag_strategy: agentic     # explicit tool calls
web_search: auto          # fallback if KB empty
persistence_mode: false   # 1 attempt is enough
```

### Production Operations Goals (deploy, delete, configure)
```yaml
enable_cot: false         # don't overthink
enable_reflection: true   # diagnose failures
autonomy_mode: supervised # HITL for every high-risk step
consensus_verification: true
persistence_mode: true    # keep trying with strategy rotation
rollback: true
```

### Creative / Writing Goals
```yaml
enable_cot: true          # rich reasoning
enable_reflection: true
# self_refine: true       # when implemented
rag_strategy: kb_or_web
debate: false             # creative work doesn't benefit from voting
```

### Data Processing Goals
```yaml
workflow_mode: workflow   # deterministic DAG, no LLM planning
persistence_mode: false
rag_strategy: none        # no docs needed
budget: high              # may have many steps
```

### Customer-Facing / Real-Time Goals
```yaml
enable_cot: false         # fast
model_override: gpt-4o-mini  # cheap + fast for execution
rag_strategy: hybrid_fast     # vector only, no multi-hop
persistence_mode: false
timeout_seconds: 30
```

---

## 14. Design Principles for Adding New Patterns

When adding a new agentic pattern to AgentVerse:

1. **New pattern = new LangGraph node** — keep the graph clean and readable
2. **Opt-in via agent config** — don't enable expensive patterns by default
3. **Every pattern emits SSE events** — users must see what's happening
4. **Every pattern has a degradation path** — must work without its dependencies
5. **Every pattern has tenant isolation** — no cross-tenant state
6. **Every pattern is tested** — unit test the node function, integration test the routing
7. **Every pattern tracks cost** — add to AgentRunTrace
8. **Compose, don't replace** — new patterns should add nodes to the graph, not change existing ones
