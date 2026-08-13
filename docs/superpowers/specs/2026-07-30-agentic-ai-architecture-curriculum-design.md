# Agentic AI Architecture Curriculum Design

**Date:** 2026-07-30
**Status:** Approved
**Audience:** Solution architects with limited hands-on AI experience
**Learning depth:** Architect mastery
**Delivery mode:** Hybrid interactive lessons and repository handbook

## Purpose

Create a world-class, code-grounded curriculum that teaches agentic AI architecture from first
principles through production design. The curriculum uses AgentVerse as the reference
implementation and treats security, reliability, cost, observability, governance, and
multi-tenancy as core architecture concerns rather than optional appendices.

## Learning Model

The curriculum uses a spiral case-study model. Concepts are introduced when they become
necessary in a working system, then revisited at full depth in dedicated chapters.

Three systems provide recurring architectural context:

1. **Enterprise incident-response agent:** planning, tool use, retrieval, memory, routing,
   observability, recovery, and multi-agent coordination.
2. **Regulated financial-operations agent:** human approval, consensus, tenant isolation,
   auditability, PII controls, policy enforcement, and fail-closed behavior.
3. **Software-engineering agent:** code retrieval, GitHub ingestion, code embeddings, tool
   validation, workflow execution, rollback, and evaluation-driven improvement.

## Learning Progression

Every concept progresses through five levels:

1. **First principles:** the problem, constraints, and underlying AI mechanics.
2. **Pattern mechanics:** algorithms, state, prompts, data contracts, and control flow.
3. **AgentVerse trace:** real entry points, classes, functions, call chains, and source links.
4. **Production architecture:** scale, security, reliability, cost, latency, tenancy, and
   operability.
5. **Architect practice:** code lab, ADR exercise, failure investigation, and design review.

## Chapter Contract

Each concept chapter contains:

1. Architect's brief
2. Mental model and first-principles derivation
3. Mechanics, state transitions, pseudocode, and contracts
4. Architecture, sequence, and decision diagrams
5. AgentVerse implementation trace with GitHub citations
6. Real-world case studies and a contrasting domain
7. Decision framework and pattern-combination guidance
8. Security, reliability, scalability, latency, cost, and operability analysis
9. Failure catalogue with detection, mitigation, and fallback behavior
10. Focused code lab with validation
11. Architecture or ADR exercise
12. Principal-architect review checklist
13. Scenario-based knowledge check
14. Cross-links to related concepts

## Handbook Structure

The handbook lives at `docs/learning/agentic-ai-architecture/` and contains:

- `README.md`: curriculum map, prerequisites, case studies, and learning path
- `00-foundations/`: vocabulary, AI execution model, and architecture notation
- `01-agent-patterns/`
- `02-rag-patterns/`
- `03-agent-memory/`
- `04-knowledge-and-graphs/`
- `05-ingestion/`
- `06-retrieval-strategies/`
- `07-embeddings/`
- `08-prompt-building/`
- `09-agent-improvement/`
- `10-evaluations/`
- `11-observability/`
- `12-guardrails/`
- `13-governance/`
- `14-scopes/`
- `15-multimodal/`
- `16-model-routing/`
- `17-chunking/`
- `18-hallucination-handling/`
- `19-platform-workflows/`
- `20-platform-concepts/`
- `labs/`: cross-module implementation and architecture labs
- `adrs/`: learner-authored architecture decision records
- `glossary.md`: shared terminology
- `source-map.md`: concept-to-AgentVerse implementation index

Each numbered module has an overview page and focused concept pages. Large concepts such as
ReAct, Reflexion, RAPTOR, ingestion orchestration, model routing, and platform lifecycle get
their own pages rather than being compressed into module summaries.

## Coverage Requirements

The handbook must cover all 20 requested domains and every named subtopic. A traceability
matrix in the root README maps each requested subtopic to exactly one primary chapter and any
supporting chapters. No subtopic may be silently omitted because the implementation is absent.
When AgentVerse does not implement a pattern, the chapter must explicitly distinguish:

- industry-standard concept and reference architecture;
- current AgentVerse implementation status;
- verified gaps or limitations;
- a production-ready extension design.

## Evidence Standard

- Treat `https://github.com/harsh786/agent-verse` on `main` as the canonical source.
- Ground implementation claims in actual code paths, not filenames or architecture summaries.
- Use clickable GitHub line citations for non-trivial implementation claims.
- Mark facts, interpretations, and recommendations distinctly.
- Diagrams must map to verified code paths or be labeled as reference architecture.
- Every module must identify what was not traced or remains uncertain.

## Quality Standard

- Explain why before what and how.
- Compare adjacent patterns side by side.
- Include concrete production failure modes and recovery paths.
- Treat trust boundaries, data scopes, and tenant isolation explicitly.
- Quantify cost, latency, throughput, and reliability trade-offs where practical.
- Prefer structured tables and diagrams over dense prose.
- Keep terminology consistent through the shared glossary.
- Validate Markdown links, Mermaid syntax, source paths, and coverage matrix entries.

## Assessment Model

Each module ends with:

- scenario-based knowledge checks;
- an architecture review checklist;
- a focused code-reading or implementation lab;
- an ADR or trade-off exercise;
- a production incident or failure-analysis exercise.

Milestone assessments combine prior modules:

1. Design a governed single-agent system.
2. Add production RAG, ingestion, and memory.
3. Evolve it into a multi-agent system with recovery and observability.
4. Defend a regulated, multi-tenant architecture before an architecture review board.

## Delivery Strategy

The handbook is produced progressively, beginning with foundations and Agent Patterns. Each
module is independently useful and linked into the larger spiral. Interactive lessons use the
same chapter content, examples, labs, and knowledge checks so conversation and repository
documentation remain synchronized.

## Validation

Completion requires:

1. Every requested subtopic appears in the traceability matrix.
2. Every implementation claim has a valid source citation.
3. Every module contains the chapter-contract sections appropriate to its scope.
4. Internal Markdown links resolve.
5. Mermaid blocks pass syntax validation where tooling is available.
6. AgentVerse-specific claims are verified against current `main` code.
7. Missing or partial implementations are labeled accurately.
