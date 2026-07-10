# Agent Patterns and Execution Workflows

**Date:** 2026-07-08

## Purpose

This document explains every agent pattern implemented or represented in AgentVerse and how those patterns collaborate with RAG, tools, memory, verification, governance, and multi-agent orchestration.

## Core Agent Roles

AgentVerse separates the LLM into three operational roles:

| Role | Responsibility | Typical Model |
|---|---|---|
| Planner | Convert goal + context into steps | strongest reasoning model, e.g. `gpt-5.2` |
| Executor | Execute one step using tools | cheaper tool model, e.g. `gpt-4o-mini` |
| Verifier | Decide whether the overall goal is achieved | cheap structured-output model |

These roles are intentionally separate because each has different cost, latency, and quality requirements.

## Pattern 1: Plan-and-Execute

Implemented in `AgentGraph` and represented by `app/agent/patterns/plan_execute.py`.

Workflow:

```text
Goal + context
  -> Planner LLM
  -> ordered plan
  -> Executor executes each step
  -> Verifier checks outcome
```

Example:

```text
Goal: "List 5 recent Jira issues"
Plan:
  Step 1: Call jira_search_issues with JQL and max_results=5
Execution:
  jira_search_issues -> Jira REST API -> issue list
Verification:
  success=true if keys and summaries are present
```

Best for:

- API automation
- ticket triage
- data gathering
- report generation
- CI/CD workflows

## Pattern 2: ReAct

Represented by `app/agent/patterns/react.py`; implemented in the executor path.

ReAct means reasoning and acting are interleaved:

```text
Step description
  -> Executor decides tool call
  -> Tool returns evidence
  -> Executor uses result for next action
```

In this system, ReAct is tool-call based rather than free-form. The executor receives `ToolDefinition` schemas and returns structured `tool_calls`.

Important implementation detail:

- When tools are present, OpenAI `tool_choice="required"` is used.
- Streaming is bypassed for tool calls because streaming tool deltas are harder to reconstruct safely.

Best for:

- calling Jira/GitHub/Slack/Stripe tools
- browser automation
- data lookup and transformation
- operational workflows with real-world side effects

## Pattern 3: Reflection

Represented by `app/agent/patterns/reflection.py`.

Workflow:

```text
Verifier says failure
  -> Reflect node diagnoses failure
  -> Feedback injected into next planner prompt
  -> Planner creates revised plan
```

Example:

```text
Failure: "JQL returned 0 issues"
Reflection: "Avoid bare ORDER BY; use project is not EMPTY ORDER BY updated DESC"
Next plan: uses corrected JQL
```

Best for:

- self-correcting broken API queries
- retrying failed tool arguments
- recovering from incomplete retrieval

## Pattern 4: Reflexion

Implemented in `app/agent/patterns/reflexion.py`.

Reflexion stores lessons from failures for future runs:

```text
Failure feedback
  -> store_lesson()
  -> ReflexionStore / Postgres
  -> future planner prompt includes lesson
```

Example lesson:

```text
For Jira goals, never use bare ORDER BY. Use project is not EMPTY ORDER BY updated DESC.
```

Best for:

- accumulating tenant-specific best practices
- avoiding repeated mistakes
- improving agents without retraining

## Pattern 5: Goal Tree

Implemented in `app/agent/goal_tree.py` and represented by `app/agent/patterns/goal_tree.py`.

Workflow:

```text
Large goal
  -> decomposed into sub-goals
  -> parallel sub-AgentGraph instances
  -> parent aggregates results
```

Example:

```text
Goal: "Audit all Jira projects for stale issues"
Sub-goals:
  Audit BAU
  Audit PCF
  Audit OPR
Parent merges stale-issue report
```

Best for:

- broad audits
- multi-project analysis
- parallel research
- large codebase reviews

## Pattern 6: Supervisor

Implemented in `app/agent/supervisor.py` and represented by `app/agent/patterns/supervisor.py`.

A supervisor agent delegates work to specialist agents:

```text
Supervisor receives complex goal
  -> assigns tasks to sub-agents
  -> collects outputs
  -> reconciles conflicts
  -> produces final answer
```

Best for:

- enterprise workflows with specialists
- research + execution separation
- multi-domain goals

## Pattern 7: Debate

Implemented in `app/agent/debate.py` and represented by `app/agent/patterns/debate.py`.

Workflow:

```text
Agent A argues one position
Agent B argues opposing position
Judge model evaluates evidence
Winner or synthesis selected
```

Best for:

- architecture decisions
- compliance interpretation
- risk assessment
- cases where overconfidence is dangerous

## Pattern 8: Consensus and Self-Consistency

Implemented in `app/agent/consensus.py` and `app/agent/patterns/self_consistency.py`.

Workflow:

```text
Generate N independent answers
  -> compare / vote
  -> return consensus answer
```

Best for:

- factual Q&A
- uncertain classification
- reducing random hallucinations

## Pattern 9: Tree of Thoughts

Represented by `app/agent/patterns/tree_of_thoughts.py`.

Workflow:

```text
Generate multiple reasoning branches
  -> score each branch
  -> prune low-scoring branches
  -> continue best branches
```

Best for:

- complex planning
- optimization
- multi-constraint decisions

## Pattern 10: Workflow Planning and Execution

Implemented in:

- `app/agent/workflow_planner.py`
- `app/agent/workflow_executor.py`
- `app/agent/workflow_nodes.py`

This supports explicit graph workflows beyond a single natural-language goal.

Example:

```text
Trigger -> Search Jira -> Summarize -> Create Confluence page -> Notify Slack
```

Best for:

- repeatable business workflows
- scheduled automation
- approval-driven pipelines

## Pattern Selection

Patterns can be selected manually or assembled dynamically using:

- `app/agent/pattern_config.py`
- `app/agent/pattern_assembler.py`
- `app/agent/dynamic_graph.py`

The system can choose pattern combinations based on goal properties such as:

- complexity
- risk
- modality
- need for retrieval
- need for human approval
- need for parallelism

## How Patterns Work Together

Example combined flow:

```text
Goal: "Prepare a compliance report from PDFs, Jira evidence, and meeting transcript"

Plan-and-Execute:
  Creates main plan

RAG:
  Retrieves policy PDF and meeting transcript chunks

Goal Tree:
  Parallelizes evidence gathering by source

ReAct:
  Calls Jira and Confluence tools

Reflection:
  Fixes failed tool calls

Verifier:
  Confirms report has required evidence

EvalRunner:
  Scores output

Memory:
  Stores successful plan for future compliance reports
```
