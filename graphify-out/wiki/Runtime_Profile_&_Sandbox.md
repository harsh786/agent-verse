# Runtime Profile & Sandbox

> 83 nodes · cohesion 0.04

## Key Concepts

- **GoalRuntimeProfile** (75 connections) — `agent-verse-backend/app/orchestration/runtime_profile.py`
- **GuardrailEnforcer** (14 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **policy_runtime/compiler.py** (12 connections) — `agent-verse-backend/app/policy_runtime/compiler.py`
- **_compute_policy_fields (shared deterministic policy dims)** (12 connections) — `agent-verse-backend/app/policy_runtime/compiler.py`
- **state_context.py** (12 connections) — `agent-verse-backend/app/state_runtime/state_context.py`
- **governance_profile.py** (11 connections) — `agent-verse-backend/app/security_runtime/governance_profile.py`
- **guardrail_enforcer.py** (10 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **.check_tool_args()** (10 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **guardrail_profile.py** (10 connections) — `agent-verse-backend/app/security_runtime/guardrail_profile.py`
- **.check_final_output()** (9 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **GuardrailProfileSelector** (9 connections) — `agent-verse-backend/app/security_runtime/guardrail_profile.py`
- **policy_bundle_selector.py** (8 connections) — `agent-verse-backend/app/security_runtime/policy_bundle_selector.py`
- **.build()** (8 connections) — `agent-verse-backend/app/state_runtime/state_context.py`
- **SemanticCacheBridge** (7 connections) — `agent-verse-backend/app/state_runtime/cache_bridge.py`
- **StateContextBuilder** (7 connections) — `agent-verse-backend/app/state_runtime/state_context.py`
- **PolicyCompiler.compile** (6 connections) — `agent-verse-backend/app/policy_runtime/compiler.py`
- **sandbox_runtime/executor.py** (6 connections) — `agent-verse-backend/app/sandbox_runtime/executor.py`
- **._check_with_engine()** (6 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **.compile()** (5 connections) — `agent-verse-backend/app/policy_runtime/compiler.py`
- **SandboxExecutor** (5 connections) — `agent-verse-backend/app/sandbox_runtime/executor.py`
- **SandboxRuntimeProfile** (5 connections) — `agent-verse-backend/app/sandbox_runtime/profile.py`
- **EnforcementResult** (5 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **._fallback_check()** (5 connections) — `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- **PolicyBundleSelector** (5 connections) — `agent-verse-backend/app/security_runtime/policy_bundle_selector.py`
- **.select()** (5 connections) — `agent-verse-backend/app/security_runtime/policy_bundle_selector.py`
- *... and 58 more nodes in this community*

## Relationships

- [Dynamic Graph Assembly](Dynamic_Graph_Assembly.md) (24 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (17 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (13 shared connections)
- [Eval Scoring](Eval_Scoring.md) (11 shared connections)
- [Community 390](Community_390.md) (5 shared connections)
- [Community 57](Community_57.md) (5 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (4 shared connections)
- [Community 292](Community_292.md) (4 shared connections)
- [Community 138](Community_138.md) (4 shared connections)
- [Community 425](Community_425.md) (3 shared connections)
- [Community 301](Community_301.md) (3 shared connections)
- [Community 99](Community_99.md) (3 shared connections)

## Source Files

- `agent-verse-backend/app/explainability_runtime/runtime_profile_explainer.py`
- `agent-verse-backend/app/orchestration/graph_factory.py`
- `agent-verse-backend/app/orchestration/runtime_profile.py`
- `agent-verse-backend/app/policy_runtime/__init__.py`
- `agent-verse-backend/app/policy_runtime/compiler.py`
- `agent-verse-backend/app/runtime_readiness/degraded_mode_policy.py`
- `agent-verse-backend/app/sandbox_runtime/executor.py`
- `agent-verse-backend/app/sandbox_runtime/profile.py`
- `agent-verse-backend/app/security_runtime/governance_profile.py`
- `agent-verse-backend/app/security_runtime/guardrail_enforcer.py`
- `agent-verse-backend/app/security_runtime/guardrail_profile.py`
- `agent-verse-backend/app/security_runtime/policy_bundle_selector.py`
- `agent-verse-backend/app/state_runtime/cache_bridge.py`
- `agent-verse-backend/app/state_runtime/cache_policy.py`
- `agent-verse-backend/app/state_runtime/memory_policy.py`
- `agent-verse-backend/app/state_runtime/state_context.py`

## Audit Trail

- EXTRACTED: 216 (87%)
- INFERRED: 32 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*