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


@dataclass
class RuntimeFlags:
    # P0 flags
    dynamic_orchestration: bool = False
    agentic_rag: bool = False
    plan_verification: bool = False
    data_classification: bool = False
    capability_registry: bool = False
    policy_compiler: bool = False
    # P1 flags
    ingestion_orchestrator: bool = False
    embedding_orchestrator: bool = False
    runtime_scorecard: bool = False
    tool_trust: bool = False
    provenance_ledger: bool = False
    recovery_classifier: bool = False
    qos_scheduler: bool = False
    # Safety
    guardrail_profile: bool = False
    readiness_gate: bool = False
    # Granular flags — each can be enabled independently
    # OR set via the master dynamic_orchestration=True
    enable_runtime_scorecard: bool = False    # RuntimeScorecard 9-dim scoring
    enable_self_improvement: bool = False     # SelfImprovementEngine action dispatch
    enable_rag_strategy_routing: bool = False # Profile-based RAG strategy selection
    enable_pattern_sse_events: bool = False   # pattern_assembled, eval_score_recorded SSEs
    enable_guardrail_profile: bool = False    # Profile-based GuardrailEnforcer

    # --- Isolated Agent Execution Environment ---
    # Mirror of config.py Settings fields so the Celery worker (which has no
    # access to app.state) can read these from env without constructing a full
    # Settings object.  Default False keeps existing behaviour unchanged.
    isolated_agent_execution: bool = False
    isolated_execution_required: bool = False
    isolated_execution_local_runner: bool = False
    isolated_execution_kubernetes_runner: bool = False

    @classmethod
    def from_env(cls) -> RuntimeFlags:
        return cls(
            dynamic_orchestration=_bool_env("DYNAMIC_ORCHESTRATION"),
            agentic_rag=_bool_env("AGENTIC_RAG"),
            plan_verification=_bool_env("PLAN_VERIFICATION"),
            data_classification=_bool_env("DATA_CLASSIFICATION"),
            capability_registry=_bool_env("CAPABILITY_REGISTRY"),
            policy_compiler=_bool_env("POLICY_COMPILER"),
            ingestion_orchestrator=_bool_env("INGESTION_ORCHESTRATOR"),
            embedding_orchestrator=_bool_env("EMBEDDING_ORCHESTRATOR"),
            runtime_scorecard=_bool_env("RUNTIME_SCORECARD"),
            tool_trust=_bool_env("TOOL_TRUST"),
            provenance_ledger=_bool_env("PROVENANCE_LEDGER"),
            recovery_classifier=_bool_env("RECOVERY_CLASSIFIER"),
            qos_scheduler=_bool_env("QOS_SCHEDULER"),
            guardrail_profile=_bool_env("GUARDRAIL_PROFILE"),
            readiness_gate=_bool_env("READINESS_GATE"),
            enable_runtime_scorecard=_bool_env("ENABLE_RUNTIME_SCORECARD"),
            enable_self_improvement=_bool_env("ENABLE_SELF_IMPROVEMENT"),
            enable_rag_strategy_routing=_bool_env("ENABLE_RAG_STRATEGY_ROUTING"),
            enable_pattern_sse_events=_bool_env("ENABLE_PATTERN_SSE_EVENTS"),
            enable_guardrail_profile=_bool_env("ENABLE_GUARDRAIL_PROFILE"),
            isolated_agent_execution=_bool_env("ISOLATED_AGENT_EXECUTION"),
            isolated_execution_required=_bool_env("ISOLATED_EXECUTION_REQUIRED"),
            isolated_execution_local_runner=_bool_env("ISOLATED_EXECUTION_LOCAL_RUNNER"),
            isolated_execution_kubernetes_runner=_bool_env("ISOLATED_EXECUTION_KUBERNETES_RUNNER"),
        )


@lru_cache(maxsize=1)
def get_runtime_flags() -> RuntimeFlags:
    """Return cached RuntimeFlags loaded from env once at startup."""
    flags = RuntimeFlags(
        dynamic_orchestration=_env_bool("DYNAMIC_ORCHESTRATION"),
        agentic_rag=_env_bool("AGENTIC_RAG"),
        plan_verification=_env_bool("PLAN_VERIFICATION"),
        data_classification=_env_bool("DATA_CLASSIFICATION"),
        capability_registry=_env_bool("CAPABILITY_REGISTRY"),
        policy_compiler=_env_bool("POLICY_COMPILER"),
        ingestion_orchestrator=_env_bool("INGESTION_ORCHESTRATOR"),
        embedding_orchestrator=_env_bool("EMBEDDING_ORCHESTRATOR"),
        runtime_scorecard=_env_bool("RUNTIME_SCORECARD"),
        tool_trust=_env_bool("TOOL_TRUST"),
        provenance_ledger=_env_bool("PROVENANCE_LEDGER"),
        recovery_classifier=_env_bool("RECOVERY_CLASSIFIER"),
        qos_scheduler=_env_bool("QOS_SCHEDULER"),
        guardrail_profile=_env_bool("GUARDRAIL_PROFILE"),
        readiness_gate=_env_bool("READINESS_GATE"),
        enable_runtime_scorecard=_env_bool("ENABLE_RUNTIME_SCORECARD"),
        enable_self_improvement=_env_bool("ENABLE_SELF_IMPROVEMENT"),
        enable_rag_strategy_routing=_env_bool("ENABLE_RAG_STRATEGY_ROUTING"),
        enable_pattern_sse_events=_env_bool("ENABLE_PATTERN_SSE_EVENTS"),
        enable_guardrail_profile=_env_bool("ENABLE_GUARDRAIL_PROFILE"),
        isolated_agent_execution=_env_bool("ISOLATED_AGENT_EXECUTION"),
        isolated_execution_required=_env_bool("ISOLATED_EXECUTION_REQUIRED"),
        isolated_execution_local_runner=_env_bool("ISOLATED_EXECUTION_LOCAL_RUNNER"),
        isolated_execution_kubernetes_runner=_env_bool("ISOLATED_EXECUTION_KUBERNETES_RUNNER"),
    )
    # Master flag enables all granular flags
    if flags.dynamic_orchestration:
        flags.enable_runtime_scorecard = True
        flags.enable_self_improvement = True
        flags.enable_rag_strategy_routing = True
        flags.enable_pattern_sse_events = True
        flags.enable_guardrail_profile = True
    return flags
