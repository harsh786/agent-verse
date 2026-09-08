# Community 217

> 28 nodes · cohesion 0.09

## Key Concepts

- **ContextPipeline (7-step context assembly)** (8 connections) — `agent-verse-backend/app/context/context_pipeline.py`
- **LongTermMemoryStore (cross-session learnings)** (8 connections) — `agent-verse-backend/app/memory/long_term.py`
- **PromptBudget (exact-token fit with immutable blocks)** (7 connections) — `agent-verse-backend/app/context/prompt_budget.py`
- **PromptBuilder (planner/executor/verifier context)** (7 connections) — `agent-verse-backend/app/context/prompt_builder.py`
- **PromptContextBundle (9 context sources)** (7 connections) — `agent-verse-backend/app/context/prompt_builder.py`
- **EpisodicMemoryStore (past goal experiences)** (6 connections) — `agent-verse-backend/app/memory/episodic.py`
- **ProceduralMemoryStore (learned tool sequences)** (5 connections) — `agent-verse-backend/app/memory/procedural.py`
- **PromptPartition (memory-tier prompt partitions)** (4 connections) — `agent-verse-backend/app/context/prompt_budget.py`
- **RerankPolicy (dedup/score/MMR/RRF/cross-encoder)** (4 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **rrf_fuse (reciprocal rank fusion)** (4 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **DepartmentMemory (dept-scoped tier-4 store)** (4 connections) — `agent-verse-backend/app/memory/dept_memory.py`
- **ContextBudget (char/4 token cap on chunks)** (3 connections) — `agent-verse-backend/app/context/context_budget.py`
- **_diversity_rerank (MMR with embedding/Jaccard fallback)** (3 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **6-tier memory model (working/session/agent/dept/org/long-term)** (3 connections) — `agent-verse-backend/app/memory/dept_memory.py`
- **ExecutionMemory (winning plans and failures)** (3 connections) — `agent-verse-backend/app/memory/execution.py`
- **_auto_compress (85% window compression guard)** (2 connections) — `agent-verse-backend/app/context/prompt_builder.py`
- **max_per_source cap (source diversity enforcement)** (2 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **ToolPromptBuilder (per-step tool-use prompt)** (2 connections) — `agent-verse-backend/app/context/tool_prompt_builder.py`
- **store_rpa_extraction (RPA/vision page memory chunking)** (2 connections) — `agent-verse-backend/app/memory/long_term.py`
- **PipelineResult (per-role contexts + citations)** (1 connections) — `agent-verse-backend/app/context/context_pipeline.py`
- **OutputContractBuilder (goal-aware output format)** (1 connections) — `agent-verse-backend/app/context/output_contract_builder.py`
- **PromptBlock (partitioned prompt segment)** (1 connections) — `agent-verse-backend/app/context/prompt_budget.py`
- **fidelity guard (required_fact_ids/source_references preserved on compress)** (1 connections) — `agent-verse-backend/app/context/prompt_budget.py`
- **Reciprocal Rank Fusion — 1/(k+rank) per Cormack 2009 (1-indexed ranks).** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **MemoryEntry (dept memory entry with corrections)** (1 connections) — `agent-verse-backend/app/memory/dept_memory.py`
- *... and 3 more nodes in this community*

## Relationships

- [Community 283](Community_283.md) (5 shared connections)
- [Community 464](Community_464.md) (2 shared connections)
- [Community 467](Community_467.md) (2 shared connections)
- [Community 266](Community_266.md) (2 shared connections)
- [Community 619](Community_619.md) (2 shared connections)
- [Community 341](Community_341.md) (2 shared connections)
- [Community 278](Community_278.md) (2 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (1 shared connections)
- [Community 138](Community_138.md) (1 shared connections)
- [Community 300](Community_300.md) (1 shared connections)
- [Community 430](Community_430.md) (1 shared connections)
- [Community 358](Community_358.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/context/context_budget.py`
- `agent-verse-backend/app/context/context_pipeline.py`
- `agent-verse-backend/app/context/output_contract_builder.py`
- `agent-verse-backend/app/context/prompt_budget.py`
- `agent-verse-backend/app/context/prompt_builder.py`
- `agent-verse-backend/app/context/rerank_policy.py`
- `agent-verse-backend/app/context/tool_prompt_builder.py`
- `agent-verse-backend/app/memory/dept_memory.py`
- `agent-verse-backend/app/memory/episodic.py`
- `agent-verse-backend/app/memory/execution.py`
- `agent-verse-backend/app/memory/long_term.py`
- `agent-verse-backend/app/memory/procedural.py`
- `agent-verse-backend/app/memory/working_memory.py`

## Audit Trail

- EXTRACTED: 30 (52%)
- INFERRED: 25 (43%)
- AMBIGUOUS: 3 (5%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*