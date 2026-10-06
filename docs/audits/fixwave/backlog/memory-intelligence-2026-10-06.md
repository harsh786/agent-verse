# Backlog: memory-intelligence (2026-10-06)

Source: `docs/audits/fixwave/pending-all-2026-10-05.json`, `pending` filtered by
`"area": "memory-intelligence"` (9 items, group `b4-knowledge-memory`).
Worktree `.claude/worktrees/bl-memory`, branch `backlog/bl-memory`, base `5ed25d973`.
Each item was re-checked against the current code and `git log` since 2026-10-05
before any change. No alembic migration was added.

**Totals:** 8 OPEN, all fixed. 1 OBSOLETE (by design, owner decision noted). 0 ALREADY-FIXED.

| Item | Feature | Status | Reason (one line) | Commit | Tests |
|---|---|---|---|---|---|
| a05-F081-03 | Canonical memory | OPEN, fixed | Lexical recall ordered by `similarity()` with no trigram predicate, so the GIN trgm index could not serve it. It now uses `%` / `<%` predicates (the MEM-36/40 shape). A blank query reads only the recency leg. | `f8e5c42cb` | `tests/memory/test_memory_lexical_recall_pg.py` (integration: EXPLAIN uses `ix_memory_records_safe_summary_trgm`, fails before the fix; app-role recall), `tests/memory/test_postgres_repository.py` |
| a05-F087-02 | Self-optimizer v2 | OPEN, fixed | `on_goal_completed` ran only on success, so a losing candidate was never penalised. `AgentGraph.run` now records a terminally FAILED goal of an experiment arm with `eval_score` 0.0. The call is awaited, bounded and recorded once per goal. | `0668e5cd2` | `tests/agent/test_graph_experiment_failed_goal.py` (fails before the fix) |
| a05-F087-03 | Self-optimizer v2 | OPEN, fixed (test gap) | The Postgres test ran as superuser, which bypasses RLS. The new test runs as a NOSUPERUSER/NOBYPASSRLS role and covers `apply_suggestion` (agents UPDATE, history, experiment bookkeeping), `rollback`, `apply_pending`, and cross-tenant refusal. No product bug found. | `39da0317a` | `tests/intelligence/test_self_optimizer_v2_app_role_pg.py` (integration, 6 tests) |
| a05-F089-01 | A/B testing engine | OPEN, fixed | `ABTestingEngine` had no caller (its last writer was removed in MEM-29). It duplicated SelfOptimizerV2 and PromptOptimizer. Removed: module, lifespan wiring, singleton entry, tests. `ab_test_results` RLS is still tested directly. | `f57cd0fba` | `tests/db/test_chat_billing_rls_integration.py::test_ab_results_are_tenant_scoped`, `tests/core/test_lifespan_singleton_isolation.py` |
| a05-F090-01 | Meta-agent | OBSOLETE (by design) | MEM-30 (`81cf6d68f`, 2026-10-01) decided that policy suggestions are advisory. The response says `policy_suggestions_applied: false` and explains why. Auto-applying them would create tenant-wide policies from LLM free text, because `agents.policy_ids` is not enforced per agent at runtime. Owner decision needed (see below). | (none) | `tests/api/test_agents_extra4.py` (asserts `policy_suggestions_applied` false) |
| a05-F092-01 | Verifier calibration | OPEN, fixed | The worker graph used the unbound `_default_calibration_store`, so worker verdicts were never persisted. `calibration_store_for(db_factory)` now feeds the worker and the GoalService. The verify node awaits the verdict INSERT. Feedback reports `calibration` = `recorded` / `no_verdict` / `failed` / `not_requested`. | `877c8ba86` | `tests/scaling/test_worker_calibration_store.py` (fails before the fix), `tests/api/test_goals_extra.py` (4 feedback tests) |
| a05-F092-02 | Learning experiments | OPEN, fixed | `LearningExperimentService` was only assigned onto `app.state`; nothing registered, assigned or read an experiment. Removed with its wiring and tests. The `learning_experiments*` tables and the `ExperimentSpec` contract are left in place. | `5670dbc46` | `tests/memory/test_memory_learning_services.py`, `tests/integration/test_memory_learning_production_path.py` |
| a05-F092-03 | Cost optimizer | OPEN, fixed | `false_confirm_rate` was already exposed. `app/intelligence/cost_optimizer.py` had no runtime caller. Registry `cost_optimisation` now points at `app.ai_router.selection:select_configured_model_id` (the cost-aware choice `ModelRouter.model_for` makes). The dead module and its tests are removed. Generated capability docs are unchanged (verified byte-identical). | `d3e9c5809` | `tests/orchestration/test_strategy_id_consistency.py::test_every_adapter_path_imports` |
| a05-F095-04 | Eval suites / rollout gate | OPEN, fixed | PUT on an agent already fully-autonomous skipped the gate when the behaviour config changed. It now re-runs the gate for the config being written (409 until the suite has run against it). `SelfOptimizerV2.apply_suggestion` refuses with reason `rollout_gate` and never writes the snapshot's `autonomy_mode`. Rollback also never writes it (follow-up). | `291843f39`, `80a186c95` | `tests/api/test_agent_rollout_gate_enforced.py` (4 new, 1 fails before the fix), `tests/intelligence/test_self_optimizer_v2_agent_columns.py` (4 new), app-role integration test above |

## Verification

- ruff and mypy (strict) are clean on every changed file.
- Unit tests: agent, api, intelligence, memory, orchestration, evals, db, core,
  persistence, services and scaling packages (markers `not integration and not slow`).
- Integration tests (testcontainers, one file at a time):
  - `tests/memory/test_memory_lexical_recall_pg.py`
  - `tests/integration/test_memory_repository_rls_integration.py`
  - `tests/intelligence/test_self_optimizer_v2_app_role_pg.py`
  - `tests/db/test_chat_billing_rls_integration.py`
- Failures that existed before these changes and are unrelated to them:
  - `tests/scaling/test_worker_memory_budget.py::test_helm_worker_pools_fit_their_memory_limit[legacy]`:
    a YAML parse error in Helm values.
  - `scripts/generate_agent_pattern_capabilities.py --check` already reports
    `docs/generated/agent-pattern-capabilities.json` as stale at the base commit.

## Owner decisions

1. **a05-F090-01**: should the meta-agent's policy suggestions ever be applied?
   This needs two things first: structured suggestions (`tools_pattern` + `deny|require_approval`)
   and agent-scoped policy enforcement (`agents.policy_ids` is stored but not enforced per agent),
   so that applying a suggestion cannot change governance for the whole tenant.
2. **Unused tables**: `ab_test_results`, `learning_experiments` and `learning_experiment_outcomes`
   no longer have a writer. Dropping them needs a new migration.
3. **a05-F095-04 workflow**: changing a fully-autonomous agent's behaviour config now takes
   four steps: demote, change, re-run the suite, promote. A self-optimizer winner for a
   fully-autonomous agent stays pending; `POST /intelligence/experiments/{id}/apply` returns 409
   with these instructions. Auto-demoting the agent on such a change would be an alternative.
