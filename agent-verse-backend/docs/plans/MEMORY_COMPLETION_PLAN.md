# Agent Memory Completion Plan (wire + persist the orphaned memory types)

Status: PLAN → IN PROGRESS · Branch: `feat/agent-memory-governance` · TDD, no stubs.

## Finding (verified)
AgentVerse already ships ~22 memory modules + a `memory_v2/` package + a **canonical
persisted layer** (`app/memory/postgres_repository.py::PostgresMemoryRepository` →
`CanonicalMemoryRecord`, with `memory_kind`, idempotency, retention, lifecycle,
feedback). Five memory types are **fully coded but in-memory only AND not wired into
the agent loop** — they don't persist and don't reach the planner/executor context:

| Type | File | State |
|---|---|---|
| Working / short-term (scratchpad) | `working_memory.py` | in-memory, unwired |
| Knowledge-graph / entity | `knowledge_graph_memory.py` + `app/knowledge_graph/store.py` | in-memory recall; `graph_source` unpopulated in loop |
| Prospective (future intents/reminders) | `prospective.py` | in-memory, unwired |
| Voyager skill-library | `voyager_skills.py` | in-memory, unwired |
| Salience scorer | `salience.py` | in-memory; not used for context ranking |

Goal: give each **real persistence through the existing canonical repo** and **wire
recall into the existing context slots** (`ContextPipeline` / prompt-builder sources
already have places for graph facts, memory records, etc.). Completion by composition.

## Design principles
- Reuse `MemoryWriteRequest`/`MemoryRecallRequest`/`PostgresMemoryRepository`
  (`memory_kind` discriminator) — do **not** invent parallel tables.
- Wire recall in `app/agent/nodes/rag_mixin.py` (pre-plan gather) + `planner_mixin.py`
  (labeled `extra_parts` block) + `executor_mixin.py` (per-step), reading/writing
  `AgentState.context` slots already threaded into `ContextPipeline`.
- Record on the same success/failure hooks the existing memories use
  (`verifier_mixin.py`).

## DESIGN CORRECTION (from `app/memory/contracts.py`, verified)
The canonical `MemoryKind` Literal is
`{execution, reflexion, long_term, episodic, procedural, knowledge_graph, prospective}`
and every `MemoryRecord` REQUIRES a `memory-embedding-v1`/1536-d embedding, evidence
refs, and a classification. Consequences that change the plan:

- **`knowledge_graph` and `prospective` are ALREADY canonical kinds** → they are
  *persist-capable today*; the real gap is **recall-into-context wiring**, not storage.
- **Working memory is ephemeral by design** and must NOT be forced into the durable,
  evidence-backed canonical store (no natural embedding/evidence for a transient
  scratchpad line, and it should die with the run). Its "persistence" is the already
  checkpointed `AgentState.context` (survives crash/resume) with an optional Redis-TTL
  mirror for cross-replica reads. Phase 1 is therefore **wiring**, not durable persistence.
- **Voyager skills ≈ `procedural`** (skills are procedures). Rather than a parallel
  store, fold the Voyager skill-library semantics (self-growing, dedup, similarity
  recall) into the existing procedural memory kind unless a concrete gap justifies a
  separate kind — decide in Phase 4 after reading `procedural.py` + `voyager_skills.py`.
- **Salience** is a *ranking function*, not a store — wire `SalienceScorer` into
  `ContextBudget`/recall ordering (no new kind).

## Phased TDD tasks
### Phase 1 — Working memory (short-term scratchpad) [START HERE] — WIRING, not durable store
- T1.1 `tests/memory/test_working_memory_salience.py` — `WorkingMemory` gains
  salience-ranked eviction/recall (integrate `SalienceScorer`): when full, the
  lowest-salience item is dropped (not blind FIFO); `most_salient(n)` returns the
  top-ranked items. → extend `working_memory.py` (pure, no I/O).
- T1.2 `tests/agent/test_working_memory_wired.py` — within a goal run, each step output
  is pushed to a run-scoped `WorkingMemory` stored in `AgentState.context`
  (checkpointed; optional Redis-TTL mirror), and the next step's executor prompt
  includes a bounded, salience-ranked `[Working memory]` block. → wire `executor_mixin`.

### Phase 2 — Knowledge-graph / entity memory
- T2.1 `tests/memory/test_kg_memory_persistence.py` — entities/relations upserted to
  `KnowledgeGraphStore` (already persisted) from tool outputs; recall by entity.
- T2.2 `tests/agent/test_graph_facts_wired.py` — `KnowledgeGraphFactsSource.graph_source`
  is populated so `[Knowledge-graph facts]` reaches the planner context. → populate `graph_source` in loop wiring.

### Phase 3 — Prospective memory (reminders / deferred intents)
- T3.1 `tests/memory/test_prospective_persistence.py` — schedule a future intent, persist,
  recall when due; integrate with `app/triggers` scheduler for firing.
- T3.2 `tests/agent/test_prospective_wired.py` — due prospective items surface as a
  `[Pending intentions]` planner block.

### Phase 4 — Voyager skill-library + salience
- T4.1 `tests/memory/test_voyager_skill_persistence.py` — learned skills persisted;
  recall by goal similarity; dedup.
- T4.2 `tests/agent/test_skill_recall_wired.py` — top skills injected into planner as
  reusable procedures; recorded on success.
- T4.3 `tests/memory/test_salience_ranking.py` — `SalienceScorer` ranks working/episodic
  recall so budgeted context keeps the most salient items (used by `ContextBudget`).

### Phase 5 — Caches (net-new)
- T5.1 `tests/rag/test_embedding_cache.py` — persistent embedding cache (Redis + DB
  fallback) keyed by (model, normalized-text); hit avoids re-embed.
- T5.2 `tests/rag/test_tool_result_cache.py` — idempotent read-only tool-result cache
  (distinct from semantic dedup), TTL + invalidation, never caches errors/empties.
- T5.3 (optional) provider **prompt-cache** breakpoints for Anthropic (cache the stable
  system+tools prefix) — measured token savings, no behavior change.

## Acceptance
- Each memory type: persists across process restart (integration test) AND appears in
  the relevant prompt block during a goal run (agent test).
- No regression in existing memory tests (`tests/memory/test_all_memory_types.py`,
  `test_memory_learning_services.py`).
- `uv run mypy app` + `ruff` clean; new tests green.
