# AgentVerse Core Platform Architecture Documentation

**Date:** 2026-07-08

**Scope:** This documentation captures the complete AgentVerse platform architecture discussed in the 2026-07-08 session: agent patterns, RAG patterns, memories, knowledge and knowledge graph, ingestion, retrieval, embeddings, prompt building, agent improvement, evals, observability, guardrails, governance, scopes, multimodal ingestion, model routing, chunking strategies, and real-world workflows.

AgentVerse is a vendor-agnostic, multi-tenant operating system for autonomous AI agents. An agent receives a natural-language goal, builds an execution plan, retrieves context, calls real-world tools through MCP, verifies the outcome, learns from the run, and improves future execution through memory, evals, and prompt/model optimization.

## Document Set

1. [Platform Lifecycle and Core Architecture](./01-platform-lifecycle-and-core-architecture.md)
2. [Agent Patterns and Execution Workflows](./02-agent-patterns-and-execution-workflows.md)
3. [RAG, Knowledge, Ingestion, Retrieval, and Chunking](./03-rag-knowledge-ingestion-retrieval-and-chunking.md)
4. [Memory, Embeddings, and Prompt Builder](./04-memory-embeddings-and-prompt-builder.md)
5. [Multimodal and Multi-Model Routing](./05-multimodal-and-multi-model-routing.md)
6. [Agent Improvement, Evals, and Observability](./06-agent-improvement-evals-and-observability.md)
7. [Guardrails, Governance, Scopes, and Safety](./07-guardrails-governance-scopes-and-safety.md)
8. [Real-World Use Cases and End-to-End Workflows](./08-real-world-use-cases-and-end-to-end-workflows.md)

## System Mental Model

```text
User Goal
  -> Tenant/Auth/Scope Resolution
  -> GoalService + Queue Routing
  -> AgentGraph
      -> Initialize
      -> RAG Retrieval
      -> Plan
      -> Execute Tools
      -> Verify
      -> Replan or Complete
  -> Memory + Evals + Observability
  -> Self-Improvement Loop
```

## Main Code Areas

| Area | Primary Files |
|---|---|
| Agent runtime | `app/agent/graph.py`, `app/agent/state.py`, `app/agent/prompts.py` |
| Agent patterns | `app/agent/patterns/`, `app/agent/supervisor.py`, `app/agent/debate.py` |
| RAG engine | `app/rag/engine.py`, `app/rag/store.py`, `app/rag/semantic_cache.py` |
| Agentic RAG | `app/rag/agentic/patterns/` |
| Knowledge ingestion | `app/knowledge/ingestors/`, `app/ingestion/` |
| Embeddings | `app/providers/base.py`, `app/embedding/`, `app/providers/*_provider.py` |
| Memory | `app/memory/execution.py`, `app/memory/long_term.py` |
| Multimodal | `app/multimodal/`, `app/perception/`, `app/ingestion/parsers/` |
| Model routing | `app/agent/model_router.py`, `app/ai_router/` |
| Governance | `app/governance/`, `app/tenancy/` |
| Guardrails | `app/intelligence/guardrails.py`, `app/agent/grounding.py` |
| Observability | `app/observability/` |
| Worker execution | `app/scaling/tasks.py`, `app/scaling/celery_app.py` |

## Core Design Principles

- **Tenant isolation first:** every request resolves to `TenantContext`; DB row-level security backs app-level checks.
- **Planner, executor, verifier are separate roles:** each can use a different model, prompt, cost policy, and eval path.
- **Tools are schema-driven:** MCP tool schemas are injected into prompts and validated before dispatch.
- **Retrieval is multi-layered:** semantic cache, hybrid KB retrieval, long-term memory, execution memory, graph/web fallbacks.
- **Multimodal becomes normalized knowledge:** images, PDFs, audio, video, code, CSV, and JSON become spans, chunks, metadata, and embeddings.
- **Safety is layered:** scopes, RBAC, policy engine, permission matrix, HITL, tool-risk classification, guardrails, grounding, audit.
- **Improvement is continuous:** every run produces traces, evals, memory records, and prompt/model optimization signals.

## Status Notes from Session

The 2026-07-08 session also confirmed a full Jira end-to-end execution path:

```text
Goal -> Planner -> Executor tool_call -> builtin-jira -> vault credential resolution
     -> Jira REST API -> real issues returned -> Verifier -> complete in 1 iteration
```

Observed successful result:

```text
status: complete
iterations: 1
tool: jira_search_issues
Jira API: HTTP 200 OK
verifier: "Goal was achieved because the 5 most recently updated Jira issues were successfully retrieved with their issue keys and summaries."
```
