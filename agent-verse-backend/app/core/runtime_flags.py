"""Runtime feature flags loaded from environment variables.
All new orchestration behaviours are off by default.
Set env var to "true" (case-insensitive) to enable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool_env(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name, "").lower()
    return val in ("true", "1", "yes", "on") if val else default


def _env_set(name: str) -> frozenset[str]:
    return frozenset(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


@dataclass
class RuntimeFlags:
    # P0 flags
    dynamic_orchestration: bool = True
    agentic_rag: bool = True
    plan_verification: bool = False
    data_classification: bool = True
    capability_registry: bool = False
    policy_compiler: bool = False
    # P1 flags
    ingestion_orchestrator: bool = True
    embedding_orchestrator: bool = True
    runtime_scorecard: bool = False
    tool_trust: bool = True
    provenance_ledger: bool = True
    recovery_classifier: bool = True
    qos_scheduler: bool = True
    # Safety
    guardrail_profile: bool = True
    readiness_gate: bool = True
    # Granular flags — each can be enabled independently
    # OR set via the master dynamic_orchestration=True
    enable_runtime_scorecard: bool = False  # RuntimeScorecard 9-dim scoring
    # ON by default: gates the SelfImprovementEngine action dispatch in the verify
    # node. Turning it on (together with the self-improvement services now wired
    # onto the Celery worker graph) is half of closing the self-tuning loop for
    # goals executed by the worker — the path that actually runs production goals.
    enable_self_improvement: bool = True  # SelfImprovementEngine action dispatch
    # Closes the self-improvement loop: when a candidate config wins its A/B
    # experiment, autonomously write it back to the agent. Now ON by default so
    # agents auto-apply their own optimized configs end-to-end (both in-process
    # and in the Celery worker). When off, a winning experiment is concluded and
    # left pending a manual apply via the API. This is the flag SelfOptimizerV2
    # reads (main.py:677) to arm its closed-loop control; the worker graph wiring
    # in app/scaling/tasks.py sources the same flag, so this default finishes
    # closing the self-tuning loop on the worker.
    enable_self_improvement_auto_apply: bool = True
    enable_rag_strategy_routing: bool = True  # Profile-based RAG strategy selection
    enable_pattern_sse_events: bool = False  # pattern_assembled, eval_score_recorded SSEs
    enable_guardrail_profile: bool = True  # Profile-based GuardrailEnforcer

    # --- Isolated Agent Execution Environment ---
    # Mirror of config.py Settings fields so the Celery worker (which has no
    # access to app.state) can read these from env without constructing a full
    # Settings object.  Default False keeps existing behaviour unchanged.
    isolated_agent_execution: bool = False
    isolated_execution_required: bool = False
    isolated_execution_local_runner: bool = False
    isolated_execution_kubernetes_runner: bool = False
    strategy_runtime_v2_shadow: bool = False
    strategy_runtime_v2_tenant_allowlist: frozenset[str] = frozenset()
    strategy_runtime_v2_kill_switch: bool = False

    @classmethod
    def from_env(cls) -> RuntimeFlags:
        return cls(
            dynamic_orchestration=_bool_env("DYNAMIC_ORCHESTRATION", True),
            agentic_rag=_bool_env("AGENTIC_RAG", True),
            plan_verification=_bool_env("PLAN_VERIFICATION"),
            data_classification=_bool_env("DATA_CLASSIFICATION", True),
            capability_registry=_bool_env("CAPABILITY_REGISTRY"),
            policy_compiler=_bool_env("POLICY_COMPILER"),
            ingestion_orchestrator=_bool_env("INGESTION_ORCHESTRATOR", True),
            embedding_orchestrator=_bool_env("EMBEDDING_ORCHESTRATOR", True),
            runtime_scorecard=_bool_env("RUNTIME_SCORECARD"),
            tool_trust=_bool_env("TOOL_TRUST", True),
            provenance_ledger=_bool_env("PROVENANCE_LEDGER", True),
            recovery_classifier=_bool_env("RECOVERY_CLASSIFIER", True),
            qos_scheduler=_bool_env("QOS_SCHEDULER", True),
            guardrail_profile=_bool_env("GUARDRAIL_PROFILE", True),
            readiness_gate=_bool_env("READINESS_GATE", True),
            enable_runtime_scorecard=_bool_env("ENABLE_RUNTIME_SCORECARD"),
            enable_self_improvement=_bool_env("ENABLE_SELF_IMPROVEMENT", True),
            enable_self_improvement_auto_apply=_bool_env(
                "ENABLE_SELF_IMPROVEMENT_AUTO_APPLY", True
            ),
            enable_rag_strategy_routing=_bool_env("ENABLE_RAG_STRATEGY_ROUTING", True),
            enable_pattern_sse_events=_bool_env("ENABLE_PATTERN_SSE_EVENTS"),
            enable_guardrail_profile=_bool_env("ENABLE_GUARDRAIL_PROFILE", True),
            isolated_agent_execution=_bool_env("ISOLATED_AGENT_EXECUTION"),
            isolated_execution_required=_bool_env("ISOLATED_EXECUTION_REQUIRED"),
            isolated_execution_local_runner=_bool_env("ISOLATED_EXECUTION_LOCAL_RUNNER"),
            isolated_execution_kubernetes_runner=_bool_env("ISOLATED_EXECUTION_KUBERNETES_RUNNER"),
            strategy_runtime_v2_shadow=_bool_env("STRATEGY_RUNTIME_V2_SHADOW"),
            strategy_runtime_v2_tenant_allowlist=_env_set("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST"),
            strategy_runtime_v2_kill_switch=_bool_env("STRATEGY_RUNTIME_V2_KILL_SWITCH"),
        )


@lru_cache(maxsize=1)
def get_runtime_flags() -> RuntimeFlags:
    """Return cached RuntimeFlags loaded from env once at startup."""
    flags = RuntimeFlags(
        dynamic_orchestration=_env_bool("DYNAMIC_ORCHESTRATION", True),
        agentic_rag=_env_bool("AGENTIC_RAG", True),
        plan_verification=_env_bool("PLAN_VERIFICATION"),
        data_classification=_env_bool("DATA_CLASSIFICATION", True),
        capability_registry=_env_bool("CAPABILITY_REGISTRY"),
        policy_compiler=_env_bool("POLICY_COMPILER"),
        ingestion_orchestrator=_env_bool("INGESTION_ORCHESTRATOR", True),
        embedding_orchestrator=_env_bool("EMBEDDING_ORCHESTRATOR", True),
        runtime_scorecard=_env_bool("RUNTIME_SCORECARD"),
        tool_trust=_env_bool("TOOL_TRUST", True),
        provenance_ledger=_env_bool("PROVENANCE_LEDGER", True),
        recovery_classifier=_env_bool("RECOVERY_CLASSIFIER", True),
        qos_scheduler=_env_bool("QOS_SCHEDULER", True),
        guardrail_profile=_env_bool("GUARDRAIL_PROFILE", True),
        readiness_gate=_env_bool("READINESS_GATE", True),
        enable_runtime_scorecard=_env_bool("ENABLE_RUNTIME_SCORECARD"),
        enable_self_improvement=_env_bool("ENABLE_SELF_IMPROVEMENT", True),
        enable_self_improvement_auto_apply=_env_bool("ENABLE_SELF_IMPROVEMENT_AUTO_APPLY", True),
        enable_rag_strategy_routing=_env_bool("ENABLE_RAG_STRATEGY_ROUTING", True),
        enable_pattern_sse_events=_env_bool("ENABLE_PATTERN_SSE_EVENTS"),
        enable_guardrail_profile=_env_bool("ENABLE_GUARDRAIL_PROFILE", True),
        isolated_agent_execution=_env_bool("ISOLATED_AGENT_EXECUTION"),
        isolated_execution_required=_env_bool("ISOLATED_EXECUTION_REQUIRED"),
        isolated_execution_local_runner=_env_bool("ISOLATED_EXECUTION_LOCAL_RUNNER"),
        isolated_execution_kubernetes_runner=_env_bool("ISOLATED_EXECUTION_KUBERNETES_RUNNER"),
        strategy_runtime_v2_shadow=_env_bool("STRATEGY_RUNTIME_V2_SHADOW"),
        strategy_runtime_v2_tenant_allowlist=_env_set("STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST"),
        strategy_runtime_v2_kill_switch=_env_bool("STRATEGY_RUNTIME_V2_KILL_SWITCH"),
    )
    # Master flag enables all granular flags
    if flags.dynamic_orchestration:
        flags.enable_runtime_scorecard = True
        flags.enable_self_improvement = True
        flags.enable_rag_strategy_routing = True
        flags.enable_pattern_sse_events = True
        flags.enable_guardrail_profile = True
    return flags
