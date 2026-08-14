---
title: "Other Core Platform Concepts"
description: "Foundational infrastructure that makes the AgentVerse agent loop possible: MCP, provider abstraction, reliability patterns, queuing, persistence, and integrations."
outline: deep
---

# Other Core Platform Concepts

The agent loop gets all the attention, but the subsystems in this section are
what make 1M+ requests/day at production reliability actually work.  Without
these layers, the planner–executor–verifier state machine is just a clever
algorithm.  With them, it becomes a multi-tenant platform that enterprise
teams can depend on.

This section covers **six interconnected capability groups**:

| Group | What It Does | Files |
|-------|-------------|-------|
| [MCP & Provider Abstraction](./01-mcp-and-provider-abstraction.md) | Universal tool protocol + LLM provider interface | `app/mcp/`, `app/providers/` |
| [Reliability Patterns](./02-reliability-patterns.md) | Circuit breakers, bulkheads, dedup, rollback | `app/reliability/` |
| [Celery, PostgreSQL & Redis](./03-celery-postgres-redis.md) | Task queues, persistence, caching, fan-out | `app/scaling/`, `app/db/` |
| [Triggers, Schedules & Marketplace](./04-triggers-schedules-and-marketplace.md) | NL scheduling, template gallery, RPA | `app/triggers/`, `app/enterprise/`, `app/rpa/` |
| [SDKs & External Integrations](./05-sdks-and-external-integrations.md) | Python SDK, TypeScript SDK, GitHub Action | `agent-verse-sdk-python/`, `agent-verse-sdk-typescript/`, `agent-verse-github-action/` |

---

## Why These Concepts Matter as Much as the Agent Patterns

The RAG patterns, multi-agent patterns, and model router documentation describe
*what* agents do.  This section describes *how they do it at scale*:

- **MCP** is why an agent can call 200+ tools without any bespoke integration
  code — the same `MCPClient.call_tool()` path handles GitHub, Jira, Salesforce,
  and any MCP-compatible external API.
- **Provider abstraction** is why the same executor code works whether the tenant
  is running local Ollama, OpenAI, Anthropic, or a private Azure OpenAI endpoint.
- **Reliability patterns** are why a runaway tenant does not crash every other
  tenant, why a failing embedding API does not cascade into agent failures, and
  why a multi-step agent that fails mid-way can undo its side effects.
- **Celery + Redis + PostgreSQL** are why goal execution is durable across worker
  restarts, why SSE events reach every browser tab even across pod restarts, and
  why per-tenant cost tracking is accurate across all 100 Celery workers.
- **Triggers and marketplace** are why operations teams can deploy pre-built agent
  templates in 30 seconds and schedule them without writing code.
- **SDKs and GitHub Action** are why developers can integrate AgentVerse into CI
  pipelines, product backends, and data pipelines with a 10-line snippet.

---

## Architecture Overview

How all these components connect to the agent loop and to each other:

```mermaid
flowchart TD
    classDef core    fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    classDef infra   fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    classDef data    fill:#3a2e4a,stroke:#9a6acd,color:#e0e0e0
    classDef edge    fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    classDef amber   fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0

    subgraph EDGE["Edge / Ingress"]
        SDK["Python / TS SDK<br/>GitHub Action"]:::edge
        API["FastAPI<br/>(25 routers)"]:::edge
        MW["TenantMiddleware<br/>RateLimiter"]:::edge
    end

    subgraph AGENT["Agent Loop (LangGraph)"]
        PL["Planner"]:::core
        EX["Executor"]:::core
        VF["Verifier"]:::core
    end

    subgraph TOOLS["Tool Layer"]
        REG["MCPRegistry<br/>(per-tenant)"]:::amber
        CLI["MCPClient<br/>(HTTP / builtin)"]:::amber
        EXT["External APIs<br/>(GitHub, Jira, …)"]:::amber
    end

    subgraph PROVIDERS["Provider Abstraction"]
        ANT["Anthropic"]:::infra
        OAI["OpenAI / Azure"]:::infra
        GEM["Gemini"]:::infra
        FAKE["FakeProvider<br/>(tests)"]:::infra
    end

    subgraph RELIABILITY["Reliability"]
        CB["Circuit Breaker"]:::infra
        BH["Bulkhead<br/>(per-tenant semaphore)"]:::infra
        DD["Deduplication<br/>(Redis SET NX)"]:::infra
        RB["Rollback Engine<br/>(LIFO inverses)"]:::infra
    end

    subgraph INFRA["Infrastructure"]
        CEL["Celery<br/>(4 plan queues)"]:::data
        PG["PostgreSQL<br/>+ pgvector + RLS"]:::data
        REDIS["Redis<br/>(state, cost, SSE, cache)"]:::data
    end

    subgraph SCHEDULE["Triggers & Marketplace"]
        NLS["NLScheduler"]:::amber
        BEAT["Celery Beat"]:::amber
        MKT["Marketplace<br/>(templates)"]:::amber
        RPA["RPA / Browser"]:::amber
    end

    SDK -->|HTTP / SSE| API
    API --> MW --> CEL
    CEL --> AGENT
    AGENT -->|tool calls| REG --> CLI --> EXT
    AGENT -->|LLM calls| PROVIDERS
    PROVIDERS --> CB --> RELIABILITY
    BH --> AGENT
    DD --> CEL
    RB --> AGENT
    AGENT -->|checkpoint| REDIS
    AGENT -->|persist| PG
    NLS --> BEAT --> CEL
    MKT -->|instantiate template| API
    RPA -->|browser session| AGENT
```

---

## Navigation

Start with [01 — MCP & Provider Abstraction](./01-mcp-and-provider-abstraction.md)
to understand how agents communicate with tools and LLMs.  Then read
[02 — Reliability Patterns](./02-reliability-patterns.md) to understand how the
platform handles failure.  The infrastructure and integration pages can be read
in any order after that.
