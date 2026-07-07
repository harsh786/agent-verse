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


@dataclass(frozen=True)
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

    @classmethod
    def from_env(cls) -> "RuntimeFlags":
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
        )


@lru_cache(maxsize=1)
def get_runtime_flags() -> RuntimeFlags:
    """Return cached RuntimeFlags loaded from env once at startup."""
    return RuntimeFlags.from_env()
