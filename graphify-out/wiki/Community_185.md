# Community 185

> 31 nodes · cohesion 0.12

## Key Concepts

- **SelfOptimizerV2** (25 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Any** (14 connections)
- **._maybe_start_experiment()** (7 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **.on_goal_completed()** (7 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **.apply_suggestion()** (6 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._read_current_agent_config()** (6 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **.get_arm_config()** (5 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._maybe_conclude_experiment()** (5 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._apply_suggestion_to_config()** (4 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._get_arm_for_goal()** (4 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._read_current_agent_config_with_session()** (4 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._bayesian_prob_better()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._compute_delta()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._create_experiment()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._get_min_goals()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._get_recent_metrics()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **.list_experiments()** (3 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **._record_result()** (2 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **.rollback()** (2 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Production-grade self-improvement engine with Bayesian A/B testing. Fix…** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Called after every goal completion. Drives the optimization loop.** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Fix 1: Apply candidate_config to the agent via a direct DB UPDATE. Was: called…** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Roll back to the control config from the experiment.** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Return the agent config for a specific goal (control or candidate arm).** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- **Fix 2: Read actual agent config from DB. Was: before_prompt = "before" —…** (1 connections) — `agent-verse-backend/app/intelligence/self_optimizer_v2.py`
- *... and 6 more nodes in this community*

## Relationships

- [Community 575](Community_575.md) (6 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 158](Community_158.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/self_optimizer_v2.py`

## Audit Trail

- EXTRACTED: 67 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*