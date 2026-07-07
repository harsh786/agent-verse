"""RuntimeSSEEmitter — creates structured SSE events for all orchestration decisions."""
from __future__ import annotations

from typing import Any


class SSEEventType:
    RUNTIME_PROFILE_SELECTED = "runtime_profile_selected"
    PATTERN_ASSEMBLED = "pattern_assembled"
    RAG_STRATEGY_SELECTED = "rag_strategy_selected"
    EMBEDDING_STRATEGY_SELECTED = "embedding_strategy_selected"
    CHUNKING_STRATEGY_SELECTED = "chunking_strategy_selected"
    MODEL_ROUTE_SELECTED = "model_route_selected"
    GUARDRAIL_PROFILE_SELECTED = "guardrail_profile_selected"
    EVAL_SCORE_RECORDED = "eval_score_recorded"
    SELF_IMPROVEMENT_SUGGESTED = "self_improvement_suggested"


class RuntimeSSEEmitter:
    def runtime_profile_selected(
        self,
        *,
        goal_id: str,
        profile_id: str,
        complexity: str,
        patterns: list[str],
        rag_strategy: str,
        assembly_latency_ms: float,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.RUNTIME_PROFILE_SELECTED,
            "goal_id": goal_id,
            "profile_id": profile_id,
            "complexity": complexity,
            "patterns": patterns,
            "rag_strategy": rag_strategy,
            "assembly_latency_ms": assembly_latency_ms,
        }

    def rag_strategy_selected(
        self,
        *,
        goal_id: str,
        strategy: str,
        sources: list[str],
        reranker: str,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.RAG_STRATEGY_SELECTED,
            "goal_id": goal_id,
            "strategy": strategy,
            "sources": sources,
            "reranker": reranker,
        }

    def model_route_selected(
        self,
        *,
        goal_id: str,
        planner: str,
        executor: str,
        verifier: str,
        cost_class: str,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.MODEL_ROUTE_SELECTED,
            "goal_id": goal_id,
            "planner": planner,
            "executor": executor,
            "verifier": verifier,
            "cost_class": cost_class,
        }

    def guardrail_profile_selected(
        self,
        *,
        goal_id: str,
        bundle: str,
        scanners: list[str],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.GUARDRAIL_PROFILE_SELECTED,
            "goal_id": goal_id,
            "bundle": bundle,
            "scanners": scanners,
        }

    def eval_score_recorded(
        self,
        *,
        goal_id: str,
        overall_score: float,
        scores: dict[str, float],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.EVAL_SCORE_RECORDED,
            "goal_id": goal_id,
            "overall_score": overall_score,
            "scores": scores,
        }

    def self_improvement_suggested(
        self,
        *,
        goal_id: str,
        suggestions: list[str],
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.SELF_IMPROVEMENT_SUGGESTED,
            "goal_id": goal_id,
            "suggestions": suggestions,
        }

    def pattern_assembled(
        self,
        *,
        goal_id: str,
        complexity: str,
        risk: str,
        patterns_active: dict[str, list[str]],
        models: dict[str, str],
        selection_reasons: dict[str, str],
        assembly_latency_ms: float,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.PATTERN_ASSEMBLED,
            "goal_id": goal_id,
            "complexity": complexity,
            "risk": risk,
            "patterns_active": patterns_active,
            "models": models,
            "selection_reasons": selection_reasons,
            "assembly_latency_ms": assembly_latency_ms,
        }

    def chunking_strategy_selected(
        self,
        *,
        goal_id: str,
        content_type: str,
        strategy: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.CHUNKING_STRATEGY_SELECTED,
            "goal_id": goal_id,
            "content_type": content_type,
            "strategy": strategy,
            "reason": reason,
        }

    def embedding_strategy_selected(
        self,
        *,
        goal_id: str,
        model_id: str,
        modality: str,
        dimension: int,
        cost_class: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return {
            "type": SSEEventType.EMBEDDING_STRATEGY_SELECTED,
            "goal_id": goal_id,
            "model_id": model_id,
            "modality": modality,
            "dimension": dimension,
            "cost_class": cost_class,
            "reason": reason,
        }
