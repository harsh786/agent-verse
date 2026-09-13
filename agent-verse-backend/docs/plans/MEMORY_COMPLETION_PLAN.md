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

## Phased TDD tasks
### Phase 1 — Working memory (short-term scratchpad) [START HERE]
- T1.1 `tests/memory/test_working_memory_persistence.py` — `WorkingMemory` writes/reads
  through `PostgresMemoryRepository` with `memory_kind="working"`, TTL/retention,
  tenant+goal scoping, eviction by capacity + salience. → add repo-backed path to `working_memory.py`.
- T1.2 `tests/agent/test_working_memory_wired.py` — within a goal run, step outputs are
  written to working memory and the next step's executor prompt includes a
  `[Working memory]` block (bounded, salience-ranked). → wire `executor_mixin` + `rag_mixin`.

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
