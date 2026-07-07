# AgentVerse — Deep Dive: Core Agentic Patterns

**Author:** Platform Architecture  
**Date:** 2026-07-07  
**Purpose:** Complete technical breakdown of the 8 fundamental agentic patterns — how they work, how they are implemented in AgentVerse, and how to use them together dynamically.

---

## 1. ReAct (Reasoning + Acting)

### What it is

ReAct (Yao et al., 2022) is the foundational loop of all modern agentic systems. The agent alternates between two actions:

- **Thought** — internal reasoning about what to do next
- **Action** — an external operation (tool call, API call, computation)
- **Observation** — the result of the action, fed back into reasoning

```
Thought: I need to find the database connection count
Action: call_tool("postgres", {"query": "SELECT count(*) FROM pg_stat_activity"})
Observation: {"count": 47}
Thought: 47 connections. The limit is 100. Now I need to check which are idle.
Action: call_tool("postgres", {"query": "SELECT state, count(*) FROM pg_stat_activity GROUP BY state"})
Observation: {"idle": 32, "active": 15}
Thought: 32 idle connections. I should recommend connection pooling.
Action: FINISH → "Database has 47/100 connections, 32 idle. Recommend PgBouncer."
```

### Why it works

Pure reasoning (CoT) without acting is brittle — it hallucinates facts. Pure acting without reasoning is blind — it doesn't know WHEN to call what. ReAct grounds reasoning in reality by interleaving observation after every action.

### How AgentVerse implements ReAct

**In `app/agent/graph.py:_node_execute` + `_execute_step`**

Every step in the plan goes through this pipeline (12 steps in `_execute_step`):

```
Step description (from planner)
         │
         ▼
  1. Cost check              — enough budget to continue?
  2. Exec memory recall      — has this step been done before?
  3. Deduplication           — is this step a duplicate?
  4. Circuit breaker         — is the target tool/LLM available?
  5. Permission check        — is this tool allowed for this tenant?
  6. Guardrail check         — prompt injection? PII? secrets?
  7. HITL gate               — is this high-risk? wait for human?
  8. Per-step RAG            — retrieve relevant context for THIS step
  9. LLM call (executor)     — reason about step + call tool
 10. Output guardrail        — check executor output for violations
 11. Result processor        — sanitise/truncate/redact output
 12. Rollback registration   — register undo action for this step
         │
         ▼
  step output → fed into next step as "recent context"
```

The key ReAct insight: the output of step N becomes part of the prompt for step N+1. Recent outputs are injected:

```python
recent_outputs = "\n".join(s.output for s in state.steps[-3:] if s.output)
content = f"Step: {step}\nRecent context:\n{recent_outputs}" if recent_outputs else f"Step: {step}"
```

### Tool Parsing in ReAct

The executor LLM can respond in two ways:

**1. Natural language response** (no tool call)
```
"The database has 47 active connections out of a maximum of 100."
```
→ Treated as the step output directly.

**2. Structured tool call** (triggers MCP execution)
```json
{"tool": "postgres_query", "arguments": {"query": "SELECT count(*) FROM pg_stat_activity"}}
```
→ Parsed by `app/agent/tool_calls.py:extract_tool_call()`, dispatched to MCP client.

The parser handles multiple LLM formats:
- Plain JSON: `{"tool": "X", "arguments": {...}}`
- Markdown blocks: ` ```json {...} ``` `
- Anthropic format: `{"function": {"name": "X", "arguments": {...}}}`
- OpenAI format: `{"tool_calls": [{"function": {"name": "X"}}]}`

### When ReAct is the right pattern

| Situation | ReAct? |
|---|---|
| Multi-step with tool calls | ✅ YES — this is its home |
| Pure computation (math) | ⚠️ Use CoT instead |
| Single lookup | ⚠️ Overkill — use direct RAG |
| Parallel independent tasks | ⚠️ Combine with Goal-Tree |

---

## 2. Plan and Execute

### What it is

Plan-and-Execute separates the **planning phase** (generating a complete list of steps) from the **execution phase** (executing each step). The planner generates the full plan upfront, THEN the executor works through it.

This is different from ReAct in a key way: ReAct plans ONE step at a time (the next action depends on the previous observation). Plan-and-Execute generates ALL steps at once, then executes them.

```
PLANNING PHASE
══════════════
Goal: "Analyse and report on database performance"
Planner: Steps = [
  "1. Check current query execution times",
  "2. Identify slow queries (>500ms)",
  "3. Check index usage statistics",
  "4. Analyse connection pool utilisation",
  "5. Generate performance report"
]

EXECUTION PHASE
═══════════════
Execute step 1 → output A
Execute step 2 (uses output A) → output B
Execute step 3 → output C
Execute step 4 → output D
Execute step 5 (uses A+B+C+D) → REPORT

VERIFICATION PHASE
══════════════════
Did the report cover all required aspects? → yes/no
```

### AgentVerse Implementation

**In `app/agent/graph.py:_node_plan`**

The planner uses `gpt-5.2` (our configured model) with the `PLANNER_SYSTEM` prompt. It receives:
- Goal text
- RAG context (from `rag_prime` node)
- Past failure feedback (if replanning)
- Source inventory (what data is available)
- Tool context (what tools are available)
- HITL rejection notes (what was rejected in prior attempt)

The planner outputs steps in one of two formats:

**Simple format** (default):
```json
{"steps": ["Step 1: ...", "Step 2: ...", "Step 3: ..."]}
```

**Structured format** (when `enable_goal_tree=True`):
```json
{"steps": [
  {"id": "s1", "description": "...", "depends_on": [], "loop_until": null},
  {"id": "s2", "description": "...", "depends_on": ["s1"], "loop_until": null},
  {"id": "s3", "description": "...", "depends_on": [], "loop_until": null}
]}
```

The structured format enables **wave-based parallel execution** — steps with no dependencies execute in parallel (wave 1), their dependents execute after (wave 2), etc.

### Wave Execution (Advanced Plan-and-Execute)

```
Plan: [s1(deps=[]), s2(deps=[s1]), s3(deps=[]), s4(deps=[s2,s3])]

Wave 1: s1, s3          ← asyncio.gather([execute_s1, execute_s3])
Wave 2: s2 (waits s1)   ← sequential
Wave 3: s4 (waits s2+s3)← sequential
```

This is in `_node_execute`:
```python
waves = _structured.execution_waves()
for wave_idx, wave in enumerate(waves):
    if len(wave) == 1:
        # Sequential execution
        output = await self._execute_step(...)
    else:
        # Parallel execution via asyncio.gather
        tasks = [self._execute_step(s.description, ...) for s in wave]
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
```

### When to use Plan-and-Execute

| Use Plan-and-Execute when | Don't use when |
|---|---|
| Steps are known upfront | Steps depend heavily on prior results |
| Steps can be parallelised | Goal is exploratory/uncertain |
| Deterministic multi-step processes | Single-step tasks |
| Long workflows (CI/CD, data pipelines) | Interactive/conversational goals |

### Plan-and-Execute vs ReAct

```
ReAct:                          Plan-and-Execute:
Step 1 → observe → plan step 2  Full plan upfront → execute all
Step 2 → observe → plan step 3  Can parallelise independent steps
Step 3 → observe → DONE         Better for known workflows

Slower (sequential planning)     Faster (parallel execution waves)
Better for exploratory tasks     Better for structured workflows
Handles uncertainty well         Requires plan to be right upfront
```

AgentVerse uses BOTH: Plan-and-Execute for the overall goal structure, ReAct within each individual step execution.

---

## 3. Loop Engineering

### What it is

Loop Engineering is about designing the CONTROL FLOW of the agent — the conditions under which it loops, exits, retries, and escalates. This includes:

1. **The main iteration loop** — plan → execute → verify → (complete | replan)
2. **Step-level loops** — execute a step repeatedly until a condition is met
3. **Persistence loops** — retry the entire goal with strategy rotation
4. **Nested loops** — sub-agents with their own loops inside a parent loop

### AgentVerse's Main Loop

```
                    ┌─────────────────────────────────────────────┐
                    │               MAIN LOOP                      │
                    │  (max_iterations = 15, configurable)         │
                    │                                              │
Goal ──→ RAG ──→ [PLAN] ──→ [EXECUTE] ──→ [VERIFY] ──→ complete  │
                  ↑                          │                     │
                  │              success=false? │                   │
                  │                          ▼                     │
                  │              has reflection?                   │
                  │                   │                            │
                  │              YES: ──→ [REFLECT] ──┐           │
                  │              NO:  skip            │            │
                  │                                   │            │
                  │              context_gap?         │            │
                  │                   │               │            │
                  │              YES: ──→ [RAG_REMEDIATE]──┐      │
                  │              NO:  skip                 │      │
                  │                                        │      │
                  └──────────────────── REPLAN ←───────────┘      │
                                                                   │
                    max_iterations exceeded → FAILED               │
                    retry=false → FAILED immediately               │
                    success=true → COMPLETE                        │
                    HITL pending → WAITING_HUMAN                   │
                    guardrail_rejected → FAILED immediately        │
                    └─────────────────────────────────────────────┘
```

The `_route` function decides the next state:
```python
def _route(self, state) -> str:
    if agent_state.verification_success: return "complete"
    if not retry: return "max_iter"           # permanent failure
    if iteration >= max_iterations: return "max_iter"
    if supervised and hitl_pending: return "waiting_human"
    if is_context_gap: return "rag_remediate" # (planned)
    if enable_reflection: return "reflect"    # diagnose then replan
    return "replan"                           # direct replan
```

### Step-Level Loop Engineering

Individual steps can loop until a condition is met (`loop_until` in structured plans):

```json
{
  "id": "s2",
  "description": "Poll deployment status until ready",
  "loop_until": "output.contains('running') or iterations >= 10",
  "max_loop_iter": 15
}
```

In `_execute_step_with_loop`:
```python
for iteration in range(step.max_loop_iter):
    output = await self._execute_step(step.description, ...)
    done = eval(step.loop_until, {"output": output, "iteration": iteration})
    if done: return output
    delay = min(2 ** iteration, 30)  # exponential backoff
    await asyncio.sleep(delay)
```

This enables **polling patterns**:
- "Wait for build to complete"
- "Check every N seconds until service is healthy"
- "Retry API call until it succeeds"

### Persistence Loop (Outer Loop)

`app/agent/persistence.py:GoalPersistenceEngine` wraps the entire agent graph in a higher-level loop with strategy rotation:

```
Attempt 1: SAME_APPROACH      → fail → backoff(30s)
Attempt 2: DIFFERENT_TOOLS    → fail → backoff(60s)
Attempt 3: SIMPLIFY           → partial → backoff(120s)
Attempt 4: DECOMPOSE          → sub-goal 1 success, sub-goal 2 fail → backoff(240s)
Attempt 5: HUMAN_GUIDANCE     → human clarifies → success ✓
```

Each attempt uses a FRESH agent graph (new plan, new execution), but with the failure history from prior attempts injected as context.

### Loop Engineering Anti-Patterns

| Anti-pattern | Problem | Fix |
|---|---|---|
| Infinite loop (no max_iterations) | Token/cost explosion | Always set max_iterations |
| Retry identical plan | Same failure, same result | Use reflection + strategy rotation |
| No backoff between retries | LLM/API rate limiting | Exponential backoff in persistence engine |
| Loop without condition | Never exits | Clear loop_until condition required |
| Nested loops without coordination | Cost explosion | Cap sub-agent iterations at 5 |

---

## 4. Reflection

### What it is

Reflection is the pattern where the agent, after a failure, explicitly analyses WHY it failed and uses that diagnosis to improve the next attempt. The key word is EXPLICIT — the agent writes out its failure analysis in natural language.

**Without reflection:** fail → replan (same blind approach)  
**With reflection:** fail → diagnose → replan with specific lesson learned

### Three Levels of Reflection in AgentVerse

**Level 1: Verification Feedback (always active)**
The verifier always provides a `reason` when it returns `success: false`. This reason is injected into the next plan iteration as `Previous feedback: {reason}`.

```
Verifier: {"success": false, "reason": "Step 3 returned 403 — missing API key"}
→ Next planner call includes: "Previous feedback: Step 3 returned 403 — missing API key"
```

**Level 2: Reflection Node (optional, `enable_reflection=True`)**
When verification fails, instead of immediately replanning, the agent runs a dedicated reflection LLM call that produces a structured diagnosis:

```python
REFLECTION_SYSTEM = """You are an agent debugger. A step has failed.
Analyse the failure and explain:
1. Root cause of failure
2. What NOT to try again
3. What to try instead
4. Any missing information or permissions
Be specific and actionable."""
```

The output goes into `agent_state.verification_feedback` which the planner reads.

**Level 3: Reflexion (cross-goal learning, planned)**
Store the failure analysis in LongTermMemory so future goals benefit from it. Not yet implemented — see "Reflexion" pattern in previous documents.

### Reflection in action

```
Goal: "Update user preferences in the database"

Attempt 1:
  Step 1: Connect to database → success
  Step 2: UPDATE users SET preferences=? WHERE id=? → ERROR: "permission denied for table users"
  Verify: success=false, reason="permission denied for table users"

Reflection (enabled):
  "Root cause: The agent tried to write directly to the users table but only has 
   SELECT permissions. Must use the /api/user-preferences endpoint instead.
   Do NOT try direct DB writes to the users table."

Attempt 2 (with reflection context):
  Step 1: Connect to API endpoint → success  
  Step 2: POST /api/user-preferences → success
  Verify: success=true ✓
```

### When Reflection pays off

Reflection adds ~1 extra LLM call per failed attempt. It pays off when:
- The failure has a diagnosable root cause (wrong tool, wrong permissions, wrong approach)
- The error message is informative
- Multiple iterations are expected (expert/complex goals)

It does NOT pay off when:
- Simple goals (1-2 iterations expected)
- Transient failures (network timeouts, rate limits)
- The failure message is uninformative

### AgentVerse Reflection Configuration

```python
# Per-agent in agent config:
{
    "enable_reflection": True,  # enables _node_reflect
    "max_iterations": 15,       # higher iterations make reflection more valuable
}

# Per-goal (override):
{
    "execution_context": {
        "enable_reflection": True
    }
}
```

**In graph.py routing:**
```python
if self._enable_reflection and not agent_state.verification_success:
    return "reflect"  # → _node_reflect → back to plan
else:
    return "replan"   # → directly to plan
```

---

## 5. Tool Use

### What it is

Tool Use is the mechanism by which an agent extends its capabilities beyond LLM knowledge to interact with real-world systems. A tool is any callable function: database queries, API calls, file operations, web search, code execution.

In AgentVerse, tools come from two sources:
1. **MCP Connectors** — registered external services (GitHub, Jira, Postgres, Slack, etc.)
2. **Built-in Tools** — RPA, web search, file operations, code interpreter

### The Tool Use Pipeline

```
Executor LLM output
         │
         ▼
  extract_tool_call()    ← parse JSON/markdown from LLM response
  app/agent/tool_calls.py
         │
    tool found?
    ├── NO  → treat output as plain text → step complete
    └── YES ↓
         ▼
  validate_tool_name()   ← is this tool in the allowed list?
         │
  validate_tool_arguments() ← schema validation
         │
  Guardrails TOOL_ARGS   ← PII, injection, secrets check
         │
  HITL gate              ← high-risk? wait for approval
         │
  Circuit breaker        ← tool available? not rate-limited?
         │
  Permission matrix      ← policy allows this tenant to use this tool?
         │
         ▼
  MCPClient.call_tool()  ← actual external call
  app/mcp/client.py
         │
         ▼
  Guardrails TOOL_OUTPUT ← validate tool response
         │
         ▼
  RollbackEngine.register() ← register undo action
         │
         ▼
  AuditLog.record()      ← immutable audit trail
         │
         ▼
  step output
```

### MCP Tool Discovery

When an agent starts, it discovers available tools from all connected MCP servers:

```python
# In goal_service.py _build_tool_context():
for connector_id in agent.connector_ids:
    discovered = await mcp_client.discover_tools(server_id=connector_id, tenant_ctx=...)
    tools.extend(discovered)

# Also always include built-in RPA tools
tools.extend(RPA_TOOLS)

# ToolSelector then ranks and filters:
selection = await tool_selector.select(goal=goal, tools=tools, tenant_ctx=...)
```

The full tool list is formatted as a prompt block injected into the executor:

```
Available tools:
[TIER 1 - Highly relevant]
- postgres_query(query: str): Execute a SQL query
- postgres_explain(query: str): Get query execution plan

[TIER 2 - Potentially useful]
- web_search(query: str): Search the web

[TIER 3 - Available if needed]  
- slack_send(channel: str, message: str): Send a Slack message
```

### Tool Calling Formats Supported

The LLM can call tools in any format — `extract_tool_call()` handles all of them:

```python
# Format 1: Standard JSON
{"tool": "postgres_query", "arguments": {"query": "SELECT version()"}}

# Format 2: Anthropic-style
{"function": {"name": "postgres_query", "arguments": {"query": "SELECT version()"}}}

# Format 3: OpenAI-style
{"tool_calls": [{"function": {"name": "postgres_query", "arguments": "{\"query\": \"SELECT version()\"}"}}]}

# Format 4: Markdown code block
```json
{"tool": "postgres_query", "arguments": {"query": "SELECT version()"}}
```

# Format 5: Name/input style (some models)
{"name": "postgres_query", "input": {"query": "SELECT version()"}}
```

### Tool Risk Classification

Tools are classified by risk level in `app/agent/tool_risk.py`:

| Risk Level | Examples | Action |
|---|---|---|
| LOW | read queries, search, list | Execute directly |
| MEDIUM | write queries, API calls | Log + execute |
| HIGH | delete, update, deploy | HITL if supervised mode |
| CRITICAL | drop table, rm -rf, prod deploy | ALWAYS require HITL |

```python
_HIGH_RISK_KEYWORDS = frozenset((
    "deploy", "delete", "drop", "prod", "production",
    "destroy", "wipe", "truncate", "purge", "nuke",
))
```

### RPA Tools (Built-in)

Browser automation tools available to every agent without MCP configuration:
- `navigate_browser(url)` — open a URL
- `click_element(selector)` — click a DOM element
- `fill_form(selector, value)` — fill a form field
- `screenshot()` — capture current page
- `extract_text(selector)` — extract page text
- `scroll_page(direction, amount)` — scroll

### Tool Use Best Practices in AgentVerse

1. **Always use `ToolSelector`** — don't inject all tools into every prompt. Rank by relevance.
2. **Validate before calling** — schema validation catches bad arguments before the external call fails.
3. **Register rollback** — every mutating tool call gets an undo action registered.
4. **Audit everything** — every tool call goes to the immutable audit log.
5. **Circuit break** — if a tool fails repeatedly, open the circuit and skip it.

---

## 6. Multi-Agent Patterns

### 6.1 Three Coordination Models

AgentVerse has three distinct multi-agent coordination models:

```
MODEL 1: Supervisor-Subagent       MODEL 2: Debate/Voting       MODEL 3: Goal-Tree
─────────────────────────          ──────────────────────        ─────────────────
      Supervisor                   Agent A  Agent B  Agent C     Goal
      /    |    \                   Proposal Proposal Proposal    /  \
  Sub-A  Sub-B  Sub-C   →          Critique Critique Critique  Sub1  Sub2
  (spec) (spec) (spec)              Vote     Vote     Vote      │     │
      \    |    /                        Winning Proposal       Sub3  Sub4
      Synthesiser                                                \   /
                                                                Sub5
```

### 6.2 Supervisor-Subagent

**Architecture:**
```python
class SupervisorAgent:
    async def run(self, goal, tenant_ctx):
        # 1. Decompose
        tasks = await self._decompose_goal(goal)
        
        # 2. Route each task to the best agent
        for task in tasks:
            agent = await self._router.route(task.goal, tenant_ctx)
            task.agent_id = agent.agent_id
        
        # 3. Execute in parallel (up to max_parallel=5)
        semaphore = asyncio.Semaphore(self._max_parallel)
        results = await asyncio.gather(*[
            self._run_subtask(task, tenant_ctx, semaphore)
            for task in tasks
        ], return_exceptions=True)
        
        # 4. Synthesize
        return await self._synthesize_results(goal, results)
```

**How sub-agent results are synthesized:**
```python
SUPERVISOR_SYNTHESIS_PROMPT = """
You are synthesizing results from {n} sub-agents:

{results_by_subtask}

Create a coherent final answer that:
1. Integrates all sub-agent findings
2. Resolves any contradictions
3. Highlights the most important findings
4. Answers the original goal: {goal}
"""
```

**When to use Supervisor pattern:**
- Goal naturally splits into independent specialised domains
- Different sub-tasks need different agent personalities/tools
- Results need to be combined into a single coherent output
- Example: "Audit our system" → sub-agents for security, performance, cost, reliability

### 6.3 Debate and Voting

**Architecture:**
```python
class DebateOrchestrator:
    async def run(self, goal, context=""):
        # Round 1: Independent proposals
        proposals = await asyncio.gather(*[
            self._generate_proposal(f"agent_{i}", goal, context)
            for i in range(self._n_agents)
        ])
        
        for round_num in range(self._rounds - 1):
            # Rounds 2+: Each agent critiques all other proposals
            for i, agent in enumerate(proposals):
                others = [p for j, p in enumerate(proposals) if j != i]
                agent.critique_of = await self._generate_critiques(agent.agent_id, others)
        
        # Final vote: each agent votes for the best proposal (not their own)
        winning = max(proposals, key=lambda p: p.votes_received)
        return DebateResult(winning_proposal=winning.proposal, ...)
```

**The debate SSE events:**
```
[debate_proposal] agent_1: "Use approach X because..."
[debate_proposal] agent_2: "Use approach Y because..."
[debate_proposal] agent_3: "Use approach X with modification Z..."
[debate_critique] agent_1 critiques agent_2: "Approach Y fails under load because..."
[debate_vote] agent_1 votes for: agent_3
[debate_vote] agent_2 votes for: agent_3
[debate_result] Winner: agent_3, consensus: 0.67
```

**When to use Debate:**
- High-stakes decisions with multiple valid approaches
- Creative tasks (multiple design options)
- Adversarial verification (one agent's output reviewed by others)
- When single-agent bias is a concern

**Cost:** N agents × 2 rounds × LLM calls. For N=3, rounds=2: ~6× cost of single-agent. Only use for genuinely high-value decisions.

### 6.4 Goal-Tree (Parallel Fanout)

**Architecture:**
```python
async def execute_goal_tree(goal, planner, tenant_ctx, parent_goal_id, graph_factory):
    # 1. Decompose goal into sub-goals with dependency graph
    decomp = await decompose_goal(goal, planner, tenant_ctx, parent_goal_id)
    
    if not decomp.should_decompose:
        return []  # Single agent handles it
    
    # 2. Execute in dependency order (topological sort → waves)
    sub_goals = decomp.sub_goals
    completed = {}
    
    for wave in topological_waves(sub_goals):
        # All sub-goals in a wave are independent → parallel
        tasks = [
            run_sub_goal(sg, graph_factory(), tenant_ctx, completed)
            for sg in wave
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for sg, result in zip(wave, results):
            completed[sg.id] = result
    
    return list(completed.values())
```

**Dependency graph example:**
```
Goal: "Build a full stack feature"

Decomposed:
  s1: "Write backend API"           [no deps]  ─┐
  s2: "Write database migration"    [no deps]   ├── Wave 1 (parallel)
  s3: "Write unit tests"            [no deps]  ─┘
  s4: "Integrate API with frontend" [deps: s1]  ─┐ Wave 2
  s5: "Run migrations"              [deps: s2]   │
  s6: "Run test suite"              [deps: s3,s4]─┘ Wave 3
```

**Threshold for activation:** Goal-tree kicks in when the plan has ≥ `goal_tree_threshold` steps (configurable, default likely 5+).

### 6.5 When to Use Which Multi-Agent Pattern

```
Goal type                          Pattern          Why
──────────────────────────────────────────────────────────────────────
"Audit system across 4 domains"    Supervisor       Specialised sub-agents
"Design this feature (3 options)"  Debate           Multiple approaches needed
"Research 5 independent topics"    Goal-Tree        Parallel, no dependencies
"Implement full feature"           Goal-Tree        Parallel + sequential waves
"Critical deployment decision"     Debate           Need consensus
"Write, test, document code"       Goal-Tree        Sequential + parallel mix
"Simple single-domain task"        Single Agent     No multi-agent overhead
```

---

## 7. Advanced Loop Engineering Patterns

### 7.1 The Persistence Loop (Outer Loop)

`app/agent/persistence.py` adds a META-LOOP around the entire agent graph:

```python
class GoalPersistenceEngine:
    async def run(self, goal, agent_factory, tenant_ctx, event_callback):
        for attempt_num in range(self._config.max_attempts):
            strategy = self._pick_strategy(attempt_num)
            
            # Modify goal based on strategy
            modified_goal = self._apply_strategy(goal, strategy, attempt_records)
            
            # Build fresh agent graph for this attempt
            agent = agent_factory()
            
            try:
                # Run the agent graph (full plan → execute → verify loop)
                result = await agent.run(modified_goal, ...)
                
                if result.verification_success:
                    return True, attempt_records
                
                attempt_records.append(AttemptRecord(strategy=strategy, error=...))
                
            except Exception as exc:
                attempt_records.append(AttemptRecord(error=str(exc)))
            
            # Backoff before next attempt
            delay = min(base_backoff * (2 ** attempt_num), max_backoff)
            await asyncio.sleep(delay)
        
        return False, attempt_records  # All attempts exhausted
```

**Strategy sequence (default):**
```
Attempt 0-1: SAME_APPROACH         ("Try again with same plan")
Attempt 2:   ESCALATE              ("Raise priority, alert human")
Attempt 3:   DIFFERENT_TOOLS       ("Try different tool for each step")
Attempt 4:   SIMPLIFY              ("Reduce scope to partial success")
Attempt 5:   DECOMPOSE             ("Break into smaller sub-goals")
Attempt 6:   HUMAN_GUIDANCE        ("Ask human for clarification")
Attempt 7+:  HUMAN_GUIDANCE        (continued escalation)
```

**Strategy switching activates new context in the next plan:**
```python
def _apply_strategy(self, goal, strategy, attempts):
    context_additions = []
    
    if strategy == RetryStrategy.DIFFERENT_TOOLS:
        context_additions.append(
            "Previous tool calls failed. Use DIFFERENT tools this time. "
            f"Avoid: {[t for a in attempts for t in a.tools_used]}"
        )
    elif strategy == RetryStrategy.SIMPLIFY:
        context_additions.append(
            "Previous attempts failed. Simplify the goal to achieve "
            "PARTIAL success rather than full success."
        )
    elif strategy == RetryStrategy.DECOMPOSE:
        context_additions.append(
            "This goal is too complex. Break it into 3-5 smaller sub-goals "
            "and achieve each one independently."
        )
    
    return goal + "\n\n[RETRY CONTEXT]\n" + "\n".join(context_additions)
```

### 7.2 The Verification Loop

The verify → route → replan loop is the innermost control flow mechanism:

```
verify() returns:
  success=true             → DONE ✓
  success=false, retry=true  → replan (up to max_iterations)
  success=false, retry=false → FAIL immediately (permanently failed)
  
retry=false is set when:
  - "impossible" in reason
  - "cannot be done" in reason  
  - "permanently failed" in reason
  - Guardrail blocks the action
```

### 7.3 HITL Loop (Human-in-the-Loop)

The HITL loop is a PAUSE mechanism — execution halts until human approves:

```
Execute step → high_risk_keyword detected
    │
    ▼
HITLGateway.request_approval(step, risk_level)
    │
    ▼ (event emitted to SSE: "waiting_approval")
Wait up to timeout_seconds for human decision
    │
    ├── APPROVED → continue execution
    ├── REJECTED → inject rejection_note → replan
    └── TIMEOUT  → fail step with "approval timed out"
```

The rejection note is fed back to the PLANNER:
```python
agent_state.context["hitl_rejection_note"] = note
# → PLANNER_SYSTEM sees:
# [IMPORTANT — Previous Action REJECTED by Human Operator]
# Rejection reason: {note}
# Do NOT repeat the rejected action. Replan with a different approach.
```

---

## 8. Other Important Patterns in AgentVerse

### 8.1 Grounding (Hallucination Prevention)

After each executor step, `GroundingChecker` verifies that specific factual claims in the output are supported by retrieved context or tool outputs.

```python
class GroundingChecker:
    def check(self, output: str, context: str, tool_outputs: list[str]) -> GroundingResult:
        # Extract specific claims (numbers, names, dates, URLs)
        claims = self._extract_claims(output)
        
        for claim in claims:
            # Is this claim supported by any context?
            supported = any(claim.lower() in c.lower() for c in [context] + tool_outputs)
            if not supported:
                return GroundingResult(grounded=False, ungrounded_claims=[claim])
        
        return GroundingResult(grounded=True)
```

Ungrounded claims are added to `agent_state.ungrounded_claims` and inform the verifier.

### 8.2 Self-Optimisation (A/B Testing Prompts)

`app/intelligence/self_optimizer_v2.py` runs A/B experiments on prompt variants:

```python
# For each plan call, self-optimizer may inject a variant prompt:
if self_opt_v2 and self._agent_id:
    arm = self_opt_v2.get_experiment_arm(
        agent_id=self._agent_id,
        experiment_type="planner_prompt"
    )
    if arm:
        planning_model = arm.config.get("model", planning_model)
        # Different arm → different system prompt / model → compare results
```

This enables the platform to automatically discover better prompts/models over time.

### 8.3 Consensus Verification (3-Way)

For high-stakes goals, verification runs 3 times with different verifiers and requires majority:

```python
class ConsensusVerifier:
    async def verify(self, agent_state, tenant_ctx):
        # Run 3 verifiers concurrently
        results = await asyncio.gather(
            self._primary_verifier.verify(agent_state),     # standard verifier
            self._cross_model_verifier.verify(agent_state), # different model
            self._llm_judge.evaluate(agent_state),          # rubric-based judge
        )
        
        successes = sum(1 for r in results if r.success)
        
        if successes >= 2:  # majority
            return VerificationResult(success=True)
        elif successes == 1:  # dissent
            return VerificationResult(success=False, needs_human_review=True)
        else:
            return VerificationResult(success=False)
```

### 8.4 Prompt Compression

Before expensive LLM calls (planning, verification), the system prompt is compressed to reduce token usage:

```python
class PromptCompressor:
    def compress(self, text: str) -> str:
        # Remove redundant whitespace
        # Remove duplicate sentences
        # Truncate very long sections
        # Keep: instructions, examples, key context
        # Remove: verbose explanations of obvious things
```

---

## 9. How All Patterns Work Together

### Full Goal Execution Flow (All Patterns Active)

```
Goal: "Design and implement a rate-limiting system for our API"
Properties: complexity=expert, domain=technical, risk=low, multi_step=true

PATTERN SELECTION (dynamic, ~1ms):
  reasoning: [cot, reflection]
  rag: [hybrid_rag, agentic_rag, multi_hop_rag]
  multi_agent: [goal_tree]
  safety: [guardrails, grounding, rollback]

═══════════════════════════════════════════════════════════════

[initialize]
  → Load agent config, tool context, memory
  → Emit: goal_started

[rag_prime] ← AGENTIC RAG
  → Parallel: KB search("rate limiting") + KG("rate limit algorithm") + ExecMemory
  → Web: NOT needed (KB has docs)
  → Emit: rag_prime_complete {sources: [kb, kg, memory], chunks: 12}

[think] ← CHAIN-OF-THOUGHT
  → "Let me think: Rate limiting needs a sliding window or token bucket algorithm.
     The system needs Redis for distributed counting.
     Need to consider: per-IP, per-user, per-endpoint granularity.
     Current stack uses FastAPI + Redis — good fit for Redis-based rate limiting."
  → Emit: cot_complete

[plan] ← PLAN-AND-EXECUTE (structured)
  → Generate structured plan with dependencies:
    s1: Design algorithm (no deps)
    s2: Design Redis schema (no deps)  
    s3: Implement middleware (deps: s1, s2)
    s4: Write unit tests (deps: s3)
    s5: Write integration tests (deps: s3)
    s6: Update API documentation (deps: s3)
  → Emit: plan_ready {steps: 6, waves: 3}

[execute] ← REACT + GOAL-TREE + TOOL USE
  Wave 1 (parallel): s1, s2
    s1: [SEARCH:kb:"sliding window algorithm"] ← AGENTIC RAG directive
        → retrieve: 3 chunks about sliding window rate limiting
        → LLM: "Use sliding window with Redis sorted sets"
        → Grounding check: ✓ grounded
    s2: [SEARCH:kb:"Redis sorted set schema"]
        → retrieve: 2 chunks
        → LLM: "ZADD rate:user:{id} {timestamp} {request_id}"
        → Grounding check: ✓
  
  Wave 2: s3 (depends on s1, s2)
    s3: Execute tool: write_file("middleware.py", code)
        → Permission check: ✓
        → Guardrail TOOL_ARGS: ✓
        → MCP: write file
        → Rollback registered: delete("middleware.py")
        → Grounding: ✓
  
  Wave 3 (parallel): s4, s5, s6
    s4, s5: run tests, fix if failing (loop_until="tests pass")
    s6: write docs

[verify] ← VERIFICATION
  → Summary of all 6 steps + outputs + citations
  → success=true, reason="All implementation complete and tested"
  → Emit: verification_done {success: true}

[complete]
  → Store winning plan in execution memory
  → Emit: goal_complete
  → Duration: ~45s, cost: ~$0.08
```

### What Each Pattern Contributed

| Pattern | Contribution to this goal |
|---|---|
| CoT | Identified Redis as the right backend before planning |
| Agentic RAG | Retrieved specific algorithm and schema docs per step |
| Structured Plan | Enabled parallel execution of s1+s2, then s4+s5+s6 |
| Goal-Tree | s1 and s2 ran simultaneously (40% faster) |
| ReAct | Each step reasoned about context before generating output |
| Grounding | Verified outputs reference actual retrieved docs |
| Rollback | Registered undo for file creation |
| Reflection | Not needed (first attempt succeeded) |

---

## 10. Dynamic Pattern Combinations Per Goal Type

```
╔══════════════════╦══════════════╦════════════╦═══════════╦═════════╗
║ Goal Type        ║ Reasoning    ║ RAG        ║ MultiAgt  ║ Safety  ║
╠══════════════════╬══════════════╬════════════╬═══════════╬═════════╣
║ Simple lookup    ║ (none)       ║ hybrid     ║ single    ║ guard   ║
║ Code generation  ║ cot          ║ hybrid+web ║ single    ║ guard   ║
║ Data analysis    ║ cot+reflect  ║ hybrid+kg  ║ goal_tree ║ ground  ║
║ System design    ║ cot+reflect  ║ agentic+   ║ goal_tree ║ ground  ║
║                  ║              ║ multi_hop  ║           ║         ║
║ Production op    ║ reflect      ║ hybrid     ║ single    ║ hitl+   ║
║                  ║              ║            ║           ║ rollback║
║ Critical action  ║ reflect      ║ hybrid     ║ debate    ║ hitl+   ║
║                  ║              ║            ║           ║ consensus║
║ Research         ║ cot+reflect  ║ agentic+   ║ supervisor║ ground  ║
║                  ║              ║ web+graph  ║           ║         ║
║ Writing task     ║ cot+self_ref ║ hybrid+web ║ single    ║ guard   ║
║ Realtime task    ║ (none)       ║ hybrid fast║ single    ║ guard   ║
╚══════════════════╩══════════════╩════════════╩═══════════╩═════════╝
```

---

## 11. Implementation Status Summary

| Pattern | File | Status | Activation |
|---|---|---|---|
| **ReAct** | `graph.py:_node_execute` + `_execute_step` | ✅ CORE | Always active |
| **Plan-and-Execute** | `graph.py:_node_plan` + `_node_execute` | ✅ CORE | Always active |
| **Wave Execution** | `graph.py:execution_waves()` | ✅ ACTIVE | Structured plan |
| **Loop-Until** | `graph.py:_execute_step_with_loop` | ✅ ACTIVE | `loop_until` in plan |
| **Persistence Loop** | `agent/persistence.py` | ✅ ACTIVE | `persistence_mode: true` |
| **Reflection** | `graph.py:_node_reflect` | ✅ ACTIVE | `enable_reflection: true` |
| **CoT** | `graph.py:_node_think` | ✅ ACTIVE | `enable_cot: true` |
| **Tool Use (MCP)** | `mcp/client.py` + `tool_calls.py` | ✅ CORE | Always active |
| **Tool Use (RPA)** | `rpa/executor.py` | ✅ ACTIVE | Always available |
| **Grounding** | `agent/grounding.py` | ✅ ACTIVE | Always active |
| **Supervisor** | `agent/supervisor.py` | ✅ ACTIVE | `workflow_mode=multi_agent` |
| **Debate** | `agent/debate.py` | ✅ ACTIVE | `workflow_mode=debate` |
| **Goal-Tree** | `agent/goal_tree.py` | ✅ ACTIVE | `enable_goal_tree: true` |
| **Consensus** | `agent/consensus.py` | ✅ ACTIVE | High-risk tools |
| **HITL** | `governance/hitl.py` | ✅ ACTIVE | High-risk keywords |
| **Circuit Breaker** | `reliability/circuit_breaker.py` | ✅ ACTIVE | All goals |
| **Rollback** | `reliability/rollback.py` | ✅ ACTIVE | All goals |
| **Guardrails** | `guardrails_v2/engine.py` | ✅ ACTIVE | All goals |
| **Self-Refine** | — | ❌ PLANNED | Phase B |
| **Self-Consistency** | — | ❌ PLANNED | Phase C |
| **Tree of Thoughts** | — | ❌ PLANNED | Phase D |
| **Dynamic Pattern Select** | — | ❌ PLANNED | Phase A |
