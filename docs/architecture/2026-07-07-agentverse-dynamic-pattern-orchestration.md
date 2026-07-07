# AgentVerse — Industry Agentic Patterns & Dynamic Pattern Orchestration

**Author:** Platform Architecture  
**Date:** 2026-07-07  
**Purpose:** Complete industry-wide agentic pattern taxonomy + design for dynamic pattern selection per goal in AgentVerse

---

## 1. Why Dynamic Pattern Selection Matters

Today in AgentVerse, patterns are configured **statically per agent** — `enable_cot: true` is set once and applies to every goal that agent runs. This is wrong:

- A simple "list files" goal doesn't need CoT or Debate
- A "design a distributed system architecture" goal does need CoT + Self-Consistency + Multi-Hop RAG
- A "delete these 50,000 records" goal needs HITL + Consensus + Rollback, regardless of what's configured on the agent

**Dynamic pattern orchestration** means: every time a goal is submitted, analyse it, classify its properties, and automatically assemble the right combination of patterns — just for that goal.

---

## 2. Complete Industry Pattern Taxonomy

### 2.1 Foundational Reasoning Patterns

#### ReAct (Yao et al., 2022)
Think → Act → Observe loop. Agent alternates between explicit reasoning traces and tool calls.
**Best for:** Tool-heavy tasks, debugging, any goal where the next action depends on the previous result.

#### Chain-of-Thought / CoT (Wei et al., 2022)
Generate step-by-step reasoning before answering. Dramatically improves performance on complex tasks.
**Best for:** Math, logic, multi-step reasoning, ambiguous goals.

#### Zero-Shot CoT
Append "Let's think step by step" to any prompt. No examples needed.
**Best for:** Quick reasoning boost with no overhead.

#### Few-Shot CoT
Provide worked examples of reasoning before the actual question.
**Best for:** Domain-specific tasks where style/format matters.

#### Self-Consistency (Wang et al., 2022)
Sample N independent CoT reasoning paths, take the majority-vote answer.
**Best for:** Math, factual questions, any task where multiple paths should converge on the same answer.
**AgentVerse gap:** Not implemented. Relevant for verification — run verifier 3× and take majority.

#### Tree of Thoughts — ToT (Yao et al., 2023)
Explore a TREE of intermediate reasoning steps, using search (BFS/DFS/beam) to find the best path.
```
Goal
 ├── Thought A1 → A2 → A3 → Dead end
 ├── Thought B1 → B2 → B3 → Partial
 └── Thought C1 → C2 → C3 → SUCCESS ← best path
```
**Best for:** Complex planning, creative tasks, puzzle-solving, multi-step reasoning where some paths fail.
**AgentVerse gap:** Not implemented. The replan loop is a degenerate 1-wide beam search. Full ToT would explore multiple plan paths simultaneously.

#### Graph of Thoughts — GoT (Besta et al., 2023)
Reasoning as a GRAPH (not just a tree) — thoughts can merge, aggregate, and refine each other.
```
Thought A + Thought B → merged Thought C (both inform the next step)
```
**Best for:** Complex synthesis tasks, research requiring multiple sources to be integrated.
**AgentVerse gap:** Not implemented. Most relevant for multi-document synthesis goals.

#### Least-to-Most Decomposition (Zhou et al., 2022)
Solve simpler subproblems first, use their solutions to solve harder ones.
```
Goal: "Translate 'The cat sat on the mat' into French"
→ Subproblem 1: "Translate 'cat'" → "chat"
→ Subproblem 2: "Translate 'sat'" → "s'est assis"
→ Subproblem 3: Combine → "Le chat s'est assis sur le tapis"
```
**Best for:** Translation, incremental computation, building complexity progressively.
**AgentVerse gap:** Partially implemented via Goal-Tree. Explicit least-to-most ordering not enforced.

#### Plan-and-Solve (Wang et al., 2023)
Generate the FULL plan first, THEN execute all steps. Contrast: ReAct generates plan step-by-step during execution.
**Best for:** Long-horizon goals where early steps shape later ones. Better when the full scope is known upfront.
**AgentVerse status:** ✅ This IS the Structured Planning pattern (plan → execute → verify).

#### ReWOO (Xu et al., 2023 — Reasoning WithOut Observation)
Pre-plan ALL tool calls before executing any of them. Then execute the plan. Reduces LLM calls vs. ReAct.
```
Plan phase: decide ALL tool calls upfront
  → call search("X"), call compute(Y), call summarise(Z)
Execute phase: run all planned calls in parallel
Synthesise phase: combine all results
```
**Best for:** Tool calls that can be determined upfront. Faster than ReAct (no back-and-forth).
**AgentVerse gap:** Not explicitly implemented. The batch prefetch in execute is related.

---

### 2.2 Self-Improvement Patterns

#### Reflexion (Shinn et al., 2023)
After failure, generate a VERBAL reinforcement signal ("I failed because I tried X when I should have tried Y") and store it in memory. The next attempt starts with this self-critique as context.

**Different from Reflection:** Reflexion stores the critique in PERSISTENT memory. Reflection only uses it for the current goal.

```
Attempt 1: fail → Reflexion: "used wrong API endpoint, should use /v2/..."
                → stored in long-term memory as "failure lesson"
Attempt 2: reads failure lesson → uses correct endpoint → success
```
**Best for:** Long-horizon tasks, tasks the agent repeatedly fails at.
**AgentVerse gap:** `app/agent/grounding.py` has reflection but NOT Reflexion (persistent storage). Should store failure analysis in LongTermMemory.

#### Self-Refine (Madaan et al., 2023)
Generate → Critique → Refine → Repeat until quality threshold met or max iterations.
```
Output v1 → "This code is inefficient (O(n²))"
Output v2 → "Better but missing error handling"
Output v3 → "Good. Meets quality threshold" → DONE
```
**Best for:** Code generation, writing tasks, any output where quality is iterative.
**AgentVerse gap:** Verify→replan loop is a coarse version of this. Need a lightweight `_node_self_refine` that improves a single output without full replanning.

#### Constitutional AI (Bai et al., 2022)
Define a constitution (set of principles). After generating output, check it against each principle, self-critique, and revise. Then check again.
```
Output → [Check principle 1: "Is this helpful?"] → revise
       → [Check principle 2: "Is this safe?"] → revise
       → [Check principle 3: "Is this honest?"] → revise
→ Constitutional output
```
**Best for:** Safety-critical outputs, customer-facing responses, any domain with compliance requirements.
**AgentVerse gap:** Guardrails v2 does rule-based checks. Constitutional AI does LLM-based principle checking — more nuanced but slower.

#### Self-Play / Self-Improvement Loop
Agent plays against itself — generates a response, then acts as the critic of that response.
**Best for:** Training-time improvement. Less relevant for inference.

---

### 2.3 Multi-Agent Coordination Patterns

#### Supervisor-Subagent (LangGraph Multi-Agent)
Central coordinator assigns tasks to specialised workers.
**AgentVerse status:** ✅ Implemented.

#### Debate / Voting (Irving et al., 2018 + modern LLM variants)
N agents independently argue for solutions. Cross-critique. Vote.
**AgentVerse status:** ✅ Implemented.

#### Mixture of Agents — MoA (Wang et al., 2024)
Layer 1: Multiple proposer agents generate independent answers.
Layer 2: Aggregator agent synthesises all proposals into a final answer.
```
Proposer 1: Answer A (gpt-5.2)
Proposer 2: Answer B (claude-3.5-sonnet)
Proposer 3: Answer C (gemini-2.0-pro)
→ Aggregator: synthesise A+B+C → final_answer
```
**Best for:** Tasks where diverse perspectives improve quality. Leverages model diversity.
**AgentVerse gap:** Debate is close, but MoA uses different models in each layer and explicit aggregation, not voting.

#### CAMEL (Li et al., 2023 — Communicative Agents)
Two agents role-play: one as "User" (task instructor), one as "Assistant" (task solver). They communicate to refine the task.
```
User-agent: "Can you help me with X?"
Assistant-agent: "Sure. First tell me Y."
User-agent: "Y is Z."
Assistant-agent: "In that case, here's the solution..."
```
**Best for:** Ambiguous goals where clarification is needed before execution. Interactive task refinement.
**AgentVerse gap:** Not implemented. HITL handles the "user asks for clarification" case, but agent-to-agent clarification dialogues are not.

#### Generative Agents (Park et al., 2023)
Agents with rich memory, personality, and social behaviours. Simulate human-like interactions over time.
**Best for:** Simulation, NPCs, social modelling, long-running autonomous agents.
**AgentVerse gap:** Partially relevant for long-running agent personas.

#### LLM Compiler (Kim et al., 2023)
Decompose task into parallel function calls, compile a DAG of dependencies, execute DAG.
```
Task → Compiler: [fetch_data(X) || fetch_data(Y)] → join → analyse(X, Y)
     Fast parallel execution with minimal sequential steps
```
**Best for:** Data-heavy pipelines. Maximises parallelism.
**AgentVerse status:** Goal-Tree is the AgentVerse equivalent.

#### Agent Network (Peer-to-Peer)
Agents communicate directly with each other without a central coordinator.
**Best for:** Decentralised problems, emergent behaviour, complex simulations.
**AgentVerse gap:** Not implemented. Relevant for advanced multi-tenant scenarios.

---

### 2.4 RAG Patterns (Extended)

#### Self-RAG (Asai et al., 2023)
The agent decides FOR EACH TOKEN whether retrieval is needed. Generates special tokens: `[Retrieve]`, `[No Retrieve]`, `[Relevant]`, `[Irrelevant]`, `[Fully supported]`.
```
"The capital of France is [No Retrieve] Paris" (factual, parametric knowledge)
"The latest version of React is [Retrieve] [search query] 19.0" (needs retrieval)
```
**Best for:** Mixed tasks with both known facts and retrieval-needed facts.
**AgentVerse gap:** Not implemented. Our current RAG always retrieves (waste if answer is known).

#### FLARE (Active Retrieval, Jiang et al., 2023)
Forward-Looking Active Retrieval — predict the next sentence, detect low-confidence tokens, retrieve context for those specific tokens.
```
Generating: "The GDP of Germany is..."
→ Detect: low confidence on the number
→ Retrieve: "Germany GDP 2024"
→ Continue with retrieved fact
```
**Best for:** Long-form generation where specific facts need retrieval mid-generation.
**AgentVerse gap:** Not implemented. Relevant for report-writing goals.

#### RAFT (Zhang et al., 2024 — RAG Fine-Tuning)
Training pattern: fine-tune the LLM on RAG-style examples so it learns to use retrieved context correctly.
**Best for:** Production systems where fine-tuning is feasible.
**AgentVerse:** Not applicable (we don't fine-tune). Relevant if adding fine-tuned model support.

#### Agentic Chunking
Use an LLM to decide HOW to chunk documents (by semantic boundaries, not by fixed token count).
**Best for:** Complex documents (contracts, code, research papers) where fixed chunking loses context.
**AgentVerse gap:** Current chunker uses sliding window (`app/rag/chunker.py`). LLM-based semantic chunking would improve retrieval quality significantly.

#### RAPTOR (Sarthi et al., 2024)
Recursive Abstractive Processing: recursively cluster documents, summarise each cluster, embed summaries. Build a tree of abstractions for multi-level retrieval.
```
Documents → cluster → summarise clusters → embed summaries
         → cluster summaries → summarise meta-clusters → embed
→ Query can match at any level of abstraction
```
**Best for:** Large document corpora where single-level retrieval misses high-level topics.
**AgentVerse gap:** Not implemented. Very useful for large KBs.

#### ColBERT / Late Interaction
Instead of encoding the full query into one vector, use token-level late interaction: match each query token against each document token.
**Best for:** Higher precision retrieval in technical domains.
**AgentVerse gap:** Using pgvector cosine similarity currently. ColBERT would require model-level change.

---

### 2.5 Tool Use Patterns

#### MRKL (Karpas et al., 2022 — Modular Reasoning Knowledge Language)
Route different parts of a query to different specialist modules (calculator, search, code interpreter, database).
```
Query: "What is 15% of the revenue from last quarter (found in DB)?"
→ MRKL router:
    DB module: "fetch last quarter revenue"
    Calculator module: "compute 15% of result"
```
**AgentVerse status:** ✅ This is essentially what MCP connectors + tool routing does.

#### Toolformer (Schick et al., 2023)
Model learns WHEN and HOW to call tools by inserting API calls inline with text generation.
**Best for:** Training-time approach. Inference: relevant as a prompting strategy.

#### CodeAct (Wang et al., 2024)
Use code execution as the primary action space. Instead of calling structured APIs, generate Python/JS code and execute it.
```
Step: "Calculate compound interest"
→ CodeAct generates: ```python
    principal=1000; rate=0.05; years=3
    result = principal * (1+rate)**years
    print(result)
  ```
→ Execute → observe result
```
**Best for:** Math, data analysis, algorithmic tasks, any task expressible as code.
**AgentVerse gap:** `app/tools/code_interpreter.py` exists but is not the PRIMARY action mode.

#### HuggingGPT / TaskMatrix (Shen et al., 2023)
Use GPT as a controller to select from hundreds of specialised models (vision, audio, text, etc.) for different subtasks.
```
Goal: "Describe this image and translate the description to French"
→ Task 1: image captioning model (BLIP-2)
→ Task 2: translation model (Helsinki-NLP)
```
**Best for:** Multi-modal tasks requiring specialised models.
**AgentVerse:** Partially via MCP connectors and multi-modal pipeline.

---

### 2.6 Long-Horizon & Autonomous Patterns

#### AutoGPT Pattern
Self-directed agent with a persistent task list. Spawns sub-tasks, executes them, creates new tasks based on results. Continues until the top-level goal is achieved.
**Best for:** Open-ended research, autonomous work requiring many sub-tasks.
**AgentVerse status:** ✅ Persistence + Goal-Tree is the AgentVerse equivalent.

#### BabyAGI Pattern
Task queue + priority management. Every completed task generates new tasks. Prioritise tasks based on objective alignment.
**Best for:** Exploratory goals, research that expands as it proceeds.
**AgentVerse gap:** Goal queue exists but doesn't dynamically generate new goals based on results.

#### Voyager (Wang et al., 2023)
Lifelong learning agent. After each skill is learned, it's stored in a library. Future tasks try skills from the library before inventing new ones.
**Best for:** Agents that repeatedly operate in the same environment (Minecraft, coding, DevOps).
**AgentVerse gap:** Execution memory is similar but stores plans, not reusable skills. Skill library with tested, verifiable code snippets would be more powerful.

#### LATS (Liu et al., 2023 — LLM As Tree Search)
Monte Carlo Tree Search with LLMs. Expand promising nodes, evaluate with value function, backpropagate scores, select best path.
**Best for:** Complex planning where many paths must be explored. Games, puzzles, optimisation problems.
**AgentVerse gap:** Not implemented. High compute cost but dramatically better for hard planning problems.

---

### 2.7 Specialised Domain Patterns

#### Program of Thought (PoT)
Generate a program (code) that solves the problem, then execute it. More reliable than CoT for quantitative tasks.
**Best for:** Math, data analysis, any task with a computable answer.

#### Scratchpad Pattern
Provide an explicit "scratchpad" section in the context where the agent can write intermediate computations before the final answer.
**Best for:** Multi-step calculations, keeping track of state across a long response.

#### Cognitive Architecture (SOAR/ACT-R inspired)
Explicit modelling of working memory, procedural memory, declarative memory, perception, and action modules. Inspired by cognitive science.
**AgentVerse:** Our memory system (working + episodic + semantic + prospective) maps to this.

#### Event-Driven Agent
Agent activates on events (webhook, schedule, database change, SSE message) rather than on explicit goal submission.
**AgentVerse status:** ✅ Triggers/schedules system in `app/triggers/`.

#### Simulation Agent
Agent tests dangerous actions in a sandboxed simulation before executing them in production.
**AgentVerse status:** ✅ `app/enterprise/simulation.py:SimulationEngine`.

---

## 3. Dynamic Pattern Orchestration Design

### 3.1 The Problem

Currently, patterns in AgentVerse are set **once per agent at creation time**:
```python
agent_config = {
    "enable_cot": True,      # STATIC — applies to every goal
    "enable_reflection": True,
    "enable_goal_tree": False,
    "autonomy_mode": "bounded-autonomous",
}
```

**A single agent runs goals as varied as:**
- "List the 5 most recent commits" (simple, no CoT needed)
- "Design a disaster recovery plan for our Postgres cluster" (complex, needs CoT + multi-hop RAG + debate)
- "Delete all test records in the users table" (dangerous, needs HITL regardless of config)

Static configuration is always wrong for at least some goals.

### 3.2 Solution: Goal Property Classifier → Pattern Assembler

```
Goal text + agent_config + tenant_context
          │
          ▼
┌─────────────────────────────────────────┐
│        GOAL PROPERTY CLASSIFIER         │
│  (fast, lightweight, rule+LLM hybrid)   │
└─────────────────────────────────────────┘
          │
          ▼ GoalProperties {
               complexity: simple|medium|complex|expert
               domain: technical|creative|analytical|operational|conversational
               risk: low|medium|high|critical
               time_sensitivity: realtime|normal|batch
               knowledge_requirement: none|kb_required|web_required|expert_required
               reversibility: reversible|irreversible
               multi_step: yes|no
               requires_external_data: yes|no
               is_generative: yes|no   (writing/code output)
               estimated_tokens: int
             }
          │
          ▼
┌─────────────────────────────────────────┐
│          PATTERN ASSEMBLER              │
│  maps GoalProperties → PatternConfig   │
└─────────────────────────────────────────┘
          │
          ▼ PatternConfig {
               reasoning: [cot, reflection, self_refine]
               rag: [hybrid, graph, web, multi_hop]
               multi_agent: [single|supervisor|debate|goal_tree]
               safety: [guardrails, hitl, consensus, rollback]
               memory: [exec_memory, ltm, kg]
               model: {planner: gpt-5.2, executor: gpt-4o-mini, ...}
               max_iterations: int
               persistence: bool
             }
          │
          ▼
┌─────────────────────────────────────────┐
│          GRAPH ASSEMBLER                │
│  builds LangGraph dynamically from     │
│  PatternConfig                         │
└─────────────────────────────────────────┘
          │
          ▼
      AgentGraph (customised per goal)
```

### 3.3 Goal Property Classifier

**Two-tier approach (fast + accurate):**

#### Tier 1: Rule-based (< 1ms, always runs)
```python
class RuleBasedClassifier:
    """Fast heuristic classification — runs before any LLM call."""
    
    RISK_KEYWORDS = frozenset({
        "delete", "drop", "truncate", "destroy", "wipe", "purge",
        "deploy", "production", "prod", "rm -rf", "overwrite",
        "payment", "charge", "billing", "transfer funds",
        "admin", "sudo", "root access",
    })
    
    COMPLEXITY_SIGNALS = {
        "expert": ["design", "architect", "optimise", "analyse", "evaluate",
                   "compare", "strategy", "tradeoffs", "distributed", "system"],
        "complex": ["explain", "how does", "why", "implement", "create",
                    "build", "write code", "research", "investigate"],
        "simple": ["list", "show", "get", "fetch", "what is", "how many",
                   "count", "status", "check"],
    }
    
    def classify(self, goal: str) -> GoalProperties:
        goal_lower = goal.lower()
        words = set(goal_lower.split())
        
        # Risk
        risk = "high" if (words & self.RISK_KEYWORDS) else "low"
        
        # Complexity
        complexity = "medium"
        for level, signals in self.COMPLEXITY_SIGNALS.items():
            if any(s in goal_lower for s in signals):
                complexity = level
                break
        
        # Multi-step
        multi_step = len(goal.split(".")) > 2 or "and then" in goal_lower or "after" in goal_lower
        
        # Generative
        is_generative = any(w in goal_lower for w in ["write", "generate", "create", "draft", "code"])
        
        # External data
        requires_web = any(w in goal_lower for w in [
            "latest", "current", "recent", "today", "news", "price", "version"
        ])
        
        return GoalProperties(
            complexity=complexity, risk=risk,
            multi_step=multi_step, is_generative=is_generative,
            requires_web=requires_web,
        )
```

#### Tier 2: LLM Classifier (< 200ms, runs for complex/ambiguous goals)
```python
CLASSIFIER_PROMPT = """Classify this goal for an AI agent. Reply with JSON only.

Goal: {goal}

JSON fields:
- complexity: "simple" | "medium" | "complex" | "expert"
- domain: "technical" | "creative" | "analytical" | "operational" | "conversational"  
- risk: "low" | "medium" | "high" | "critical"
- time_sensitivity: "realtime" | "normal" | "batch"
- knowledge_requirement: "none" | "kb_only" | "web_required" | "expert_domain"
- reversibility: "reversible" | "irreversible"
- multi_step: true | false
- is_generative: true | false
- estimated_steps: 1-10
- recommended_patterns: array of pattern names from the allowed list

Allowed patterns: [
  "cot", "reflection", "self_refine", "self_consistency",
  "hybrid_rag", "graph_rag", "web_rag", "multi_hop_rag", "agentic_rag",
  "single_agent", "supervisor", "debate", "goal_tree",
  "hitl", "consensus", "rollback", "persistence",
  "exec_memory", "ltm"
]"""
```

### 3.4 Pattern Assembler — Decision Table

```python
PATTERN_DECISION_TABLE = [
    # Rule format: (condition, patterns_to_add, patterns_to_remove, config_overrides)
    
    # SAFETY RULES (always evaluated, cannot be overridden)
    Rule(
        condition=lambda p: p.risk in ("high", "critical"),
        add=["hitl", "rollback", "guardrails_strict"],
        remove=["full_autonomy"],
        config={"autonomy_mode": "supervised"},
        priority=CRITICAL,  # cannot be overridden by other rules
    ),
    Rule(
        condition=lambda p: p.reversibility == "irreversible",
        add=["hitl", "consensus_verification"],
        priority=CRITICAL,
    ),
    
    # REASONING RULES
    Rule(
        condition=lambda p: p.complexity in ("complex", "expert"),
        add=["cot", "reflection"],
        config={"model_planner": "gpt-5.2"},
    ),
    Rule(
        condition=lambda p: p.complexity == "simple",
        remove=["cot", "debate"],
        config={"model_executor": "gpt-4o-mini"},
    ),
    Rule(
        condition=lambda p: p.is_generative and p.complexity != "simple",
        add=["self_refine"],
        config={"max_refine_iterations": 2},
    ),
    
    # RAG RULES
    Rule(
        condition=lambda p: p.knowledge_requirement == "web_required" or p.requires_web,
        add=["web_rag"],
        config={"web_auto_activate": True},
    ),
    Rule(
        condition=lambda p: p.domain == "analytical" and p.complexity == "expert",
        add=["multi_hop_rag", "graph_rag"],
    ),
    Rule(
        condition=lambda p: p.complexity in ("complex", "expert"),
        add=["agentic_rag"],  # agent drives retrieval explicitly
    ),
    
    # MULTI-AGENT RULES
    Rule(
        condition=lambda p: p.multi_step and p.complexity == "expert",
        add=["goal_tree"],  # parallel sub-tasks
        config={"enable_goal_tree": True},
    ),
    Rule(
        condition=lambda p: p.risk == "critical" and p.is_generative,
        add=["debate"],  # multiple proposals, vote
        config={"debate_agents": 3},
    ),
    Rule(
        condition=lambda p: p.complexity == "expert" and p.domain == "analytical",
        add=["supervisor"],  # specialised sub-agents
    ),
    
    # PERSISTENCE RULES
    Rule(
        condition=lambda p: p.complexity == "expert" and p.multi_step,
        config={"persistence_mode": True, "max_attempts": 5},
    ),
    
    # BUDGET RULES
    Rule(
        condition=lambda p: p.time_sensitivity == "realtime",
        remove=["cot", "debate", "multi_hop_rag"],
        config={"model_executor": "gpt-4o-mini", "max_iterations": 3},
    ),
    Rule(
        condition=lambda p: p.complexity == "simple",
        config={"model_executor": "gpt-4o-mini", "max_iterations": 5},
    ),
]
```

### 3.5 Dynamic Graph Assembly

```python
class DynamicGraphAssembler:
    """Builds a LangGraph graph customised to the PatternConfig for each goal."""
    
    def assemble(self, config: PatternConfig, base_graph_config: AgentGraphConfig) -> AgentGraph:
        """
        Start from the base graph and add/remove nodes based on PatternConfig.
        Returns a compiled LangGraph graph.
        """
        g = StateGraph(GraphState)
        
        # ALWAYS present: initialize, execute, verify
        g.add_node("initialize", self._make_initialize_node())
        g.add_node("execute", self._make_execute_node(config))
        g.add_node("verify", self._make_verify_node(config))
        
        # RAG node — always present, but configured differently
        g.add_node("rag_prime", self._make_rag_prime_node(config))
        
        # OPTIONAL: Chain-of-Thought
        if "cot" in config.reasoning_patterns:
            g.add_node("think", self._make_think_node())
        
        # OPTIONAL: Self-Refine
        if "self_refine" in config.reasoning_patterns:
            g.add_node("refine", self._make_refine_node(config))
        
        # OPTIONAL: RAG Remediation
        if "agentic_rag" in config.rag_patterns:
            g.add_node("rag_remediate", self._make_rag_remediate_node())
        
        # OPTIONAL: Reflection
        if "reflection" in config.reasoning_patterns:
            g.add_node("reflect", self._make_reflect_node())
        
        # OPTIONAL: Debate
        if "debate" in config.multi_agent_patterns:
            g.add_node("debate", self._make_debate_node(config))
        
        # OPTIONAL: Goal-Tree
        if "goal_tree" in config.multi_agent_patterns:
            g.add_node("plan", self._make_goal_tree_plan_node())
        else:
            g.add_node("plan", self._make_standard_plan_node(config))
        
        # Wire edges
        self._wire_edges(g, config)
        
        return g.compile(checkpointer=config.checkpointer)
    
    def _wire_edges(self, g: StateGraph, config: PatternConfig):
        g.add_edge("initialize", "rag_prime")
        
        if "cot" in config.reasoning_patterns:
            g.add_edge("rag_prime", "think")
            g.add_edge("think", "plan")
        else:
            g.add_edge("rag_prime", "plan")
        
        if "debate" in config.multi_agent_patterns:
            g.add_edge("plan", "debate")
            g.add_edge("debate", "execute")
        else:
            g.add_edge("plan", "execute")
        
        g.add_edge("execute", "verify")
        
        # Conditional routing from verify
        routing_map = {"complete": END, "max_iter": END, "waiting_human": END}
        
        if "reflection" in config.reasoning_patterns:
            routing_map["reflect"] = "reflect"
            g.add_edge("reflect", "plan")
        
        if "agentic_rag" in config.rag_patterns:
            routing_map["rag_remediate"] = "rag_remediate"
            g.add_edge("rag_remediate", "plan")
        
        if "self_refine" in config.reasoning_patterns:
            routing_map["refine"] = "refine"
            g.add_edge("refine", "verify")
        
        routing_map["replan"] = "plan"
        
        g.add_conditional_edges("verify", self._make_route_fn(config), routing_map)
```

---

## 4. Pattern-to-Goal Type Matrix (Complete)

### Simple → Mapping

```
Goal type           Complexity  Risk    Patterns activated
─────────────────────────────────────────────────────────────────────
"list files"        simple      low     ReAct + Naive RAG
"what is X?"        simple      low     Hybrid RAG (no planning overhead)
"run tests"         simple      low     ReAct + Execution Memory
"status check"      simple      low     ReAct (no RAG needed)
"fetch API data"    simple      medium  ReAct + Circuit Breaker
─────────────────────────────────────────────────────────────────────
"write a function"  complex     low     CoT + ReAct + Self-Refine
"explain X"         complex     low     CoT + Multi-Hop RAG + Grounding
"design system"     expert      low     CoT + Graph RAG + Goal-Tree + Web RAG
"analyse data"      complex     low     CoT + Hybrid RAG + CodeAct
"research topic"    complex     low     Agentic RAG + Multi-Hop + Web + LTM
─────────────────────────────────────────────────────────────────────
"delete records"    medium      HIGH    HITL + Rollback + Consensus + Guardrails
"deploy to prod"    complex     CRITICAL HITL + Debate + Rollback + Consensus + Simulation
"send all users X"  medium      HIGH    HITL + Guardrails + Dry-Run first
"payment transfer"  simple      CRITICAL HITL + Consensus + Irreversibility check
─────────────────────────────────────────────────────────────────────
"daily report"      medium      low     Workflow DAG (deterministic)
"process CSV"       batch       low     Workflow DAG + CodeAct
"monitor Postgres"  medium      low     Event-Driven + Persistence + LTM
─────────────────────────────────────────────────────────────────────
"latest news on X"  simple      low     Web RAG (web_auto_activate)
"current price of Y" simple     low     Web RAG (no KB needed)
```

---

## 5. Implementation Design for AgentVerse

### 5.1 New Files to Create

```
app/agent/
├── pattern_config.py        ← GoalProperties, PatternConfig dataclasses
├── goal_classifier.py       ← RuleBasedClassifier + LLMClassifier
├── pattern_assembler.py     ← Decision table, PatternAssembler
└── dynamic_graph.py         ← DynamicGraphAssembler (builds LangGraph per goal)
```

### 5.2 `app/agent/pattern_config.py`

```python
"""Goal properties and assembled pattern configuration."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class Complexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Domain(str, Enum):
    TECHNICAL = "technical"
    CREATIVE = "creative"
    ANALYTICAL = "analytical"
    OPERATIONAL = "operational"
    CONVERSATIONAL = "conversational"


@dataclass
class GoalProperties:
    """Properties of a goal, determined by the classifier."""
    complexity: Complexity = Complexity.MEDIUM
    domain: Domain = Domain.TECHNICAL
    risk: RiskLevel = RiskLevel.LOW
    time_sensitivity: str = "normal"          # realtime | normal | batch
    knowledge_requirement: str = "kb_only"    # none | kb_only | web_required | expert_domain
    reversibility: str = "reversible"         # reversible | irreversible
    multi_step: bool = True
    is_generative: bool = False
    requires_web: bool = False
    estimated_steps: int = 3
    confidence: float = 0.8                   # classifier confidence


@dataclass
class PatternConfig:
    """Assembled pattern configuration for a specific goal."""
    # Reasoning patterns
    reasoning_patterns: list[str] = field(default_factory=lambda: ["reflection"])
    # RAG patterns
    rag_patterns: list[str] = field(default_factory=lambda: ["hybrid_rag"])
    # Multi-agent patterns
    multi_agent_patterns: list[str] = field(default_factory=lambda: ["single_agent"])
    # Safety patterns (safety rules can only ADD, never remove)
    safety_patterns: list[str] = field(default_factory=lambda: ["guardrails"])
    
    # Model assignments
    model_planner: str = "gpt-5.2"
    model_executor: str = "gpt-5.2"
    model_verifier: str = "gpt-5.2"
    model_classifier: str = "gpt-4o-mini"     # lightweight model for classification
    
    # Graph configuration
    max_iterations: int = 15
    max_refine_iterations: int = 2
    persistence_mode: bool = False
    max_persistence_attempts: int = 3
    
    # Autonomy
    autonomy_mode: str = "bounded-autonomous"  # supervised | bounded-autonomous | fully-autonomous
    
    # Web search
    web_auto_activate: bool = False
    
    # Metadata for observability
    goal_properties: GoalProperties | None = None
    selection_reason: dict[str, str] = field(default_factory=dict)  # pattern → why selected
    assembly_latency_ms: float = 0.0
```

### 5.3 `app/agent/goal_classifier.py`

```python
"""Goal classifier — determines properties of a goal for pattern assembly."""
from __future__ import annotations
import re
from app.agent.pattern_config import GoalProperties, Complexity, RiskLevel, Domain

_RISK_KEYWORDS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge", "rm -rf",
    "deploy", "production", "prod", "overwrite", "payment", "charge",
    "billing", "transfer funds", "admin", "sudo", "root access",
    "send email", "send sms", "post to", "publish", "release",
})

_COMPLEXITY_SIGNALS = {
    Complexity.EXPERT: [
        "design", "architect", "optimise", "analyze", "evaluate",
        "compare", "strategy", "tradeoff", "distributed system",
        "security audit", "performance", "scalability"
    ],
    Complexity.COMPLEX: [
        "explain", "how does", "why", "implement", "create", "build",
        "write code", "research", "investigate", "integrate"
    ],
    Complexity.SIMPLE: [
        "list", "show", "get", "fetch", "what is", "how many",
        "count", "status", "check", "ping", "find"
    ],
}

_DOMAIN_SIGNALS = {
    Domain.TECHNICAL: ["code", "api", "database", "server", "deploy", "debug", "test"],
    Domain.CREATIVE: ["write", "generate", "draft", "story", "poem", "design", "create"],
    Domain.ANALYTICAL: ["analyse", "evaluate", "compare", "research", "explain", "why"],
    Domain.OPERATIONAL: ["deploy", "monitor", "alert", "backup", "scale", "migrate"],
}

_WEB_SIGNALS = frozenset({
    "latest", "current", "recent", "today", "news", "price",
    "version", "now", "2024", "2025", "2026", "live",
})

_IRREVERSIBLE_SIGNALS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "send email", "send sms", "post", "publish", "release", "deploy",
    "payment", "transfer", "charge",
})


class GoalClassifier:
    """Two-tier goal classifier: rules first, LLM if ambiguous."""

    def classify_fast(self, goal: str) -> GoalProperties:
        """Rule-based classification. < 1ms. Used for all goals."""
        g = goal.lower()
        words = set(g.split())

        # Risk
        risk = RiskLevel.HIGH if (words & _RISK_KEYWORDS) else RiskLevel.LOW
        if any(kw in g for kw in ("payment", "charge", "transfer funds", "delete all")):
            risk = RiskLevel.CRITICAL

        # Complexity
        complexity = Complexity.MEDIUM
        for level in (Complexity.EXPERT, Complexity.COMPLEX, Complexity.SIMPLE):
            if any(s in g for s in _COMPLEXITY_SIGNALS[level]):
                complexity = level
                break

        # Domain
        domain = Domain.TECHNICAL
        for d, signals in _DOMAIN_SIGNALS.items():
            if any(s in g for s in signals):
                domain = d
                break

        return GoalProperties(
            complexity=complexity,
            domain=domain,
            risk=risk,
            multi_step=("and" in g or "then" in g or len(goal.split(".")) > 2),
            is_generative=any(w in g for w in ("write", "generate", "create", "draft", "code")),
            requires_web=bool(words & _WEB_SIGNALS),
            reversibility="irreversible" if (words & _IRREVERSIBLE_SIGNALS) else "reversible",
            estimated_steps=min(10, max(1, len(goal.split(".")) + 1)),
            confidence=0.7,  # rule-based: moderate confidence
        )

    async def classify_with_llm(
        self, goal: str, provider: Any, fast_props: GoalProperties
    ) -> GoalProperties:
        """LLM-based classification for complex/ambiguous goals. ~200ms."""
        # Only call LLM if fast classifier is uncertain (MEDIUM complexity)
        if fast_props.complexity != Complexity.MEDIUM and fast_props.confidence > 0.85:
            return fast_props  # trust the fast classifier
        
        try:
            from app.providers.base import CompletionRequest, Message
            resp = await provider.complete(CompletionRequest(
                messages=[
                    Message(role="system", content=_CLASSIFIER_SYSTEM),
                    Message(role="user", content=f"Goal: {goal}"),
                ],
                model="gpt-4o-mini",  # fast cheap model for classification
                max_tokens=150,
                response_schema=_CLASSIFIER_SCHEMA,
            ))
            import json
            data = json.loads(resp.content)
            # Merge with fast_props (LLM overrides uncertainty)
            return GoalProperties(
                complexity=Complexity(data.get("complexity", fast_props.complexity)),
                domain=Domain(data.get("domain", fast_props.domain)),
                risk=RiskLevel(data.get("risk", fast_props.risk)),
                time_sensitivity=data.get("time_sensitivity", "normal"),
                knowledge_requirement=data.get("knowledge_requirement", "kb_only"),
                reversibility=data.get("reversibility", fast_props.reversibility),
                multi_step=data.get("multi_step", fast_props.multi_step),
                is_generative=data.get("is_generative", fast_props.is_generative),
                requires_web=data.get("requires_web", fast_props.requires_web),
                estimated_steps=data.get("estimated_steps", fast_props.estimated_steps),
                confidence=0.95,  # LLM classification: high confidence
            )
        except Exception:
            return fast_props  # fall back to fast classifier on any error


# Module-level singleton
goal_classifier = GoalClassifier()
```

### 5.4 `app/agent/pattern_assembler.py`

```python
"""Pattern assembler — maps GoalProperties to PatternConfig."""
from __future__ import annotations
import time
from app.agent.pattern_config import GoalProperties, PatternConfig, Complexity, RiskLevel, Domain


class PatternAssembler:
    """
    Deterministic rule engine that maps GoalProperties to PatternConfig.
    
    Rules are evaluated in priority order:
    1. CRITICAL safety rules (override everything)
    2. Complexity rules
    3. RAG rules
    4. Multi-agent rules
    5. Optimisation rules (cost, speed)
    """

    def assemble(self, props: GoalProperties, agent_config: dict) -> PatternConfig:
        start = time.monotonic()
        config = PatternConfig(goal_properties=props)
        reasons: dict[str, str] = {}

        # ── CRITICAL SAFETY RULES (cannot be removed) ─────────────────────────
        if props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            config.safety_patterns += ["hitl", "rollback"]
            config.autonomy_mode = "supervised"
            reasons["hitl"] = f"risk={props.risk}"
            reasons["rollback"] = f"risk={props.risk}"

        if props.reversibility == "irreversible":
            config.safety_patterns += ["consensus_verification"]
            reasons["consensus"] = "irreversible action"

        if props.risk == RiskLevel.CRITICAL:
            config.safety_patterns += ["simulation_first"]
            reasons["simulation"] = "critical risk — dry-run required"

        # ── REASONING RULES ────────────────────────────────────────────────────
        if props.complexity in (Complexity.COMPLEX, Complexity.EXPERT):
            config.reasoning_patterns += ["cot"]
            reasons["cot"] = f"complexity={props.complexity}"

        if props.complexity == Complexity.EXPERT or props.risk != RiskLevel.LOW:
            config.reasoning_patterns += ["reflection"]
            reasons["reflection"] = "expert task or non-trivial risk"

        if props.is_generative and props.complexity != Complexity.SIMPLE:
            config.reasoning_patterns += ["self_refine"]
            reasons["self_refine"] = "generative output benefits from iteration"

        if props.complexity == Complexity.EXPERT and props.risk == RiskLevel.LOW:
            config.reasoning_patterns += ["self_consistency"]
            reasons["self_consistency"] = "expert domain, accuracy matters"

        # ── RAG RULES ──────────────────────────────────────────────────────────
        if props.requires_web or props.knowledge_requirement == "web_required":
            config.rag_patterns += ["web_rag"]
            config.web_auto_activate = True
            reasons["web_rag"] = "web data required"

        if props.complexity == Complexity.EXPERT and props.domain == Domain.ANALYTICAL:
            config.rag_patterns += ["multi_hop_rag", "graph_rag"]
            reasons["multi_hop_rag"] = "analytical expert task"

        if props.complexity in (Complexity.COMPLEX, Complexity.EXPERT):
            config.rag_patterns += ["agentic_rag"]
            reasons["agentic_rag"] = "complex task needs agent-driven retrieval"

        # ── MULTI-AGENT RULES ──────────────────────────────────────────────────
        if props.multi_step and props.complexity == Complexity.EXPERT:
            config.multi_agent_patterns = ["goal_tree"]
            reasons["goal_tree"] = "expert multi-step enables parallel execution"

        if props.risk == RiskLevel.CRITICAL and props.is_generative:
            config.multi_agent_patterns = ["debate"]
            reasons["debate"] = "critical generative output needs multiple proposals"

        if props.complexity == Complexity.EXPERT and props.domain in (Domain.ANALYTICAL, Domain.TECHNICAL):
            if "goal_tree" not in config.multi_agent_patterns:
                config.multi_agent_patterns = ["supervisor"]
                reasons["supervisor"] = "expert domain benefits from specialised sub-agents"

        # ── PERSISTENCE RULES ──────────────────────────────────────────────────
        if props.complexity == Complexity.EXPERT and props.multi_step:
            config.persistence_mode = True
            config.max_persistence_attempts = 5
            reasons["persistence"] = "expert multi-step may need strategy rotation"

        # ── OPTIMISATION RULES (override model/iteration choices) ─────────────
        if props.time_sensitivity == "realtime":
            config.reasoning_patterns = [p for p in config.reasoning_patterns
                                         if p not in ("cot", "debate", "self_consistency")]
            config.rag_patterns = ["hybrid_rag"]  # fastest strategy
            config.model_executor = "gpt-4o-mini"
            config.max_iterations = 3
            reasons["realtime_opt"] = "realtime: stripped non-essential patterns"

        if props.complexity == Complexity.SIMPLE:
            config.reasoning_patterns = []
            config.model_executor = "gpt-4o-mini"
            config.max_iterations = 5
            reasons["simple_opt"] = "simple goal: minimal overhead"

        # ── RESPECT AGENT-LEVEL OVERRIDES (agent config wins for non-safety) ──
        if agent_config.get("force_cot"):
            config.reasoning_patterns = list(set(config.reasoning_patterns + ["cot"]))
        if agent_config.get("max_iterations"):
            config.max_iterations = max(config.max_iterations, agent_config["max_iterations"])

        config.selection_reason = reasons
        config.assembly_latency_ms = (time.monotonic() - start) * 1000
        return config


# Module-level singleton
pattern_assembler = PatternAssembler()
```

### 5.5 Integration in `GoalService.submit_goal()`

```python
# In submit_goal(), BEFORE creating the GoalRecord:

# 1. Fast classify (always)
from app.agent.goal_classifier import goal_classifier
from app.agent.pattern_assembler import pattern_assembler

fast_props = goal_classifier.classify_fast(goal)

# 2. LLM classify only for complex/medium goals (not simple, not realtime)
if fast_props.complexity == Complexity.MEDIUM:
    provider = getattr(self._app_state, "_app_provider", None)
    if provider:
        fast_props = await goal_classifier.classify_with_llm(goal, provider, fast_props)

# 3. Assemble patterns
pattern_config = pattern_assembler.assemble(fast_props, _agent_config)

# 4. Store in execution_context for observability
execution_context = execution_context or {}
execution_context["pattern_config"] = {
    "reasoning": pattern_config.reasoning_patterns,
    "rag": pattern_config.rag_patterns,
    "multi_agent": pattern_config.multi_agent_patterns,
    "safety": pattern_config.safety_patterns,
    "complexity": fast_props.complexity,
    "risk": fast_props.risk,
    "selection_reasons": pattern_config.selection_reason,
}

# 5. Pass to agent loop builder
# The _make_agent_loop_for_tenant picks up pattern_config via execution_context
```

---

## 6. Pattern Activation Reference Card

```
GOAL SIGNAL              PATTERN ACTIVATED          REASON
────────────────────────────────────────────────────────────────────────
Contains "delete/drop"   HITL + Rollback + Guardrails  risk=high
Contains "deploy/prod"   HITL + Consensus + Simulation  risk=critical
Contains "design/arch"   CoT + Goal-Tree + Agentic RAG  complexity=expert
Contains "explain/why"   CoT + Multi-Hop RAG             analytical
Contains "write/create"  Self-Refine                    generative
Contains "latest/news"   Web RAG (auto-activate)        requires_web
Contains "analyse"       Hybrid RAG + Graph RAG         analytical
Multi-sentence goal      Persistence                    multi_step
Short/simple goal        No CoT, gpt-4o-mini executor   complexity=simple
Real-time goal           Strip CoT/Debate, fast model    time_sensitivity=realtime
Expert + irreversible    Debate + Consensus + HITL       risk=critical
```

---

## 7. Observability — Pattern Transparency

Every goal should emit its pattern configuration in the SSE stream:

```json
{
  "type": "pattern_assembled",
  "complexity": "expert",
  "risk": "low",
  "patterns_active": {
    "reasoning": ["cot", "reflection"],
    "rag": ["hybrid_rag", "agentic_rag", "graph_rag"],
    "multi_agent": ["goal_tree"],
    "safety": ["guardrails"]
  },
  "models": {
    "planner": "gpt-5.2",
    "executor": "gpt-5.2"
  },
  "selection_reasons": {
    "cot": "complexity=expert",
    "goal_tree": "expert multi-step enables parallel execution",
    "graph_rag": "analytical expert task"
  },
  "assembly_latency_ms": 0.8
}
```

This gives users full visibility into WHY each pattern was chosen.

---

## 8. Industry Patterns Not Yet in AgentVerse (Priority Order)

| Priority | Pattern | Value | Effort | When to implement |
|---|---|---|---|---|
| P1 | Self-Refine | High — improves code/writing quality | Low | Phase B |
| P1 | Speculative RAG | High — reduces latency | Medium | Phase C |
| P1 | GoalClassifier + PatternAssembler | CRITICAL — enables all dynamic routing | Medium | Phase A |
| P2 | Tree of Thoughts | High — better complex planning | High | Phase D |
| P2 | Self-RAG | Medium — avoids unnecessary retrieval | Medium | Phase C |
| P2 | Self-Consistency | Medium — better accuracy for factual goals | Low | Phase B |
| P2 | FLARE | Medium — mid-generation retrieval | High | Phase D |
| P2 | Agentic Chunking | Medium — better retrieval quality | Medium | Phase C |
| P3 | Mixture of Agents | High — cross-model diversity | High | Phase E |
| P3 | CAMEL | Low — niche use case | Medium | Phase E |
| P3 | LATS | High — best complex planning | Very High | Phase F |
| P3 | RAPTOR | Medium — large KB improvement | Medium | Phase D |
| P3 | Reflexion | Medium — persistent failure learning | Low | Phase B |
| P3 | Peer Review | Medium — writing quality | Medium | Phase D |
