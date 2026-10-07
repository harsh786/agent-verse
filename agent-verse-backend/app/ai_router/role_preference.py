"""The operator's saved REASONING order, as a per-role model choice.

The Model Registry's preference order for reasoning
(``PUT /models/preferences/text_generation``) is explicit operator intent. It
used to decide only the failover chain: both role routers (``ModelRouter`` and
``ModelOrchestratorAdapter``) returned the deployment's automatic role map
first — which every NVIDIA / on-prem deployment has (planning on NVIDIA,
execution and verification on Qwen) — so the model ranked first never ran.

Precedence (both routers and every single call) is ONE resolver,
:func:`app.ai_router.resolve.resolve_reasoning`: per-agent / per-goal override >
the tenant's own routing-policy pin (``PUT /models/routing-policies``) > saved
reasoning order > per-role env pin (``DEFAULT_*_MODEL``) > deployment role map >
env default model > the registry's configured models > the provider default
(empty registry only) > ``ModelNotConfiguredError``.

**Every** LLM role of the agent/goal runtime and its adjacent subsystems resolves
its model through it (:func:`resolve_role_model`): :data:`ROLE_TASK_TYPES` maps
each charged role label (``role_calls[].role``) to the routing task type that
decides it — the agent graph, multi-agent patterns, routers, judges,
guardrails, RAG strategy LLMs, chat, org, knowledge graph, skills, triggers and
workflows. ``complete_decision`` fills any request sent with no model from it.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Task types both role routers (``ModelRouter`` / ``ModelOrchestratorAdapter``)
# resolve with the full precedence (override > tenant pin > saved order > role
# map > env pin > registry). ``supervisor`` / ``reflection`` / ``think`` alias a
# base role (``deployment_roles.ROLE_ALIASES``) for tenant pins and the role map.
ROUTED_TASK_TYPES = frozenset(
    {"planning", "execution", "verification", "classification", "reflection", "think",
     "thinking", "supervisor", "judge"}
)

# Roles that run on a reasoning model.
REASONING_ROLES = ROUTED_TASK_TYPES

# Per-task env pins (explicit operator intent, below the saved order).
ROLE_PIN_ENV: dict[str, str] = {
    "planning": "DEFAULT_PLANNING_MODEL",
    "reflection": "DEFAULT_PLANNING_MODEL",
    "think": "DEFAULT_PLANNING_MODEL",
    "thinking": "DEFAULT_PLANNING_MODEL",
    "supervisor": "DEFAULT_PLANNING_MODEL",
    "execution": "DEFAULT_EXECUTION_MODEL",
    "classification": "DEFAULT_EXECUTION_MODEL",
    "verification": "DEFAULT_VERIFICATION_MODEL",
}

# Every LLM role label (as charged: ``role_calls[].role``) → its routing task type.
# The guard test (tests/ai_router/test_all_roles_follow_registry.py) asserts each
# one resolves to the model ranked first; a new role must be added here.
ROLE_TASK_TYPES: dict[str, str] = {
    # ── the agent graph's own roles ──
    "planner": "planning",
    "planning": "planning",
    "executor": "execution",
    "execution": "execution",
    "verifier": "verification",
    "verification": "verification",
    "think": "think",
    "reflection": "reflection",
    "refine": "execution",
    "execute": "execution",
    "judge": "judge",
    "critique": "verification",
    "synthesis": "planning",
    "summarization": "classification",
    "extraction": "classification",
    "goal_tree": "planning",
    "goal_tree_synthesis": "planning",
    # ── multi-agent / reasoning patterns ──
    "supervisor": "supervisor",
    "debate": "planning",
    "debate_propose": "planning",
    "debate_critique": "planning",
    "debate_vote": "judge",
    "consensus_verifier": "verification",
    "consensus_judge": "judge",
    "self_consistency": "execution",
    "tree_of_thoughts": "planning",
    "peer_review": "verification",
    "self_refine": "execution",
    "few_shot_cot": "planning",
    "strategy": "planning",
    "coordination": "planning",
    "grounding": "verification",
    # ── routing / classification ──
    "agent_router": "classification",
    "goal_classifier": "classification",
    # ── answer synthesis + workflows ──
    "answer_synthesis": "planning",
    "workflow_planner": "planning",
    "workflow_step": "execution",
    "workflow_decision": "classification",
    # ── eval judges ──
    "eval_judge": "judge",
    "eval_accuracy": "judge",
    "eval_coherence": "judge",
    "eval_multi_turn": "judge",
    "ai_ops_judge": "judge",
    # ── memory ──
    "memory_consolidation": "classification",
    # Structured field extraction from OCR'd text (app/ocr/extractors).
    "ocr_extract": "classification",
    # ── guardrails / LLM classifiers ──
    "guardrail_judge": "judge",
    "guardrail_toxicity": "classification",
    "claim_decomposer": "classification",
    "nli_checker": "verification",
    "self_optimizer": "planning",
    # ── RAG strategy LLMs (query rewrite / HyDE / verifier / synthesis) ──
    "rag_strategy": "classification",
    "rag_hyde": "classification",
    "rag_query_expand": "classification",
    "rag_query_reformulate": "classification",
    "rag_query_transform": "classification",
    "rag_multi_hop": "classification",
    "rag_rerank": "classification",
    "rag_citation_verify": "verification",
    "rag_corrective": "classification",
    "rag_self_rag": "verification",
    "rag_flare": "classification",
    "rag_speculative": "classification",
    "rag_raptor": "classification",
    "rag_agentic_chunking": "classification",
    "rag_synthesis": "planning",
    "rag_raft_inference": "planning",
    "rag_proposition": "classification",
    "rag_indexing": "classification",
    # ── chat ──
    "chat": "planning",
    "chat_qa": "planning",
    "chat_summary": "classification",
    "chat_intent": "classification",
    "chat_understanding": "classification",
    # ── organisation runtime ──
    "org_goal_analysis": "planning",
    "org_decompose_mission": "planning",
    "org_compose_departments": "planning",
    "org_strategic_brief": "planning",
    "org_team_formation": "planning",
    "org_collaboration": "planning",
    "org_quality_gate": "judge",
    "org_meta_orchestrator": "planning",
    "collab_insights": "planning",
    "collab_consensus": "judge",
    # ── knowledge graph / memory / skills / tools ──
    "kg_entity_extraction": "classification",
    "kg_relationship_extraction": "classification",
    "skill": "planning",
    "skill_test": "planning",
    "tool_self_heal": "classification",
    "tool_intelligence": "classification",
    "simulation": "classification",
    # ── triggers / schedules / meta ──
    "nl_trigger": "classification",
    "nl_scheduler": "classification",
    "schedule_suggest": "classification",
    "meta_agent": "planning",
    "proactive_planner": "planning",
    "insights_nl_query": "planning",
    "insights_failure_analysis": "planning",
    "workflow_llm_step": "planning",
    "worker_job": "execution",
}

# Families of role labels built at runtime (``coordination_<pattern>_<step>``, ...).
_ROLE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("coordination_", "planning"),
    ("debate_", "planning"),
    ("eval_", "judge"),
    ("rag_", "classification"),
    ("guardrail_", "classification"),
    ("org_", "planning"),
    ("chat_", "classification"),
    ("kg_", "classification"),
    ("collab_", "planning"),
)


def role_task_type(role: str) -> str:
    """The routing task type that decides *role*'s model (``""``: not a known role)."""
    r = str(role or "").strip().lower()
    if r in ROLE_TASK_TYPES:
        return ROLE_TASK_TYPES[r]
    for prefix, task in _ROLE_PREFIXES:
        if r.startswith(prefix):
            return task
    return ""


def rag_role_for_strategy(strategy: Any) -> str:
    """The role label of a RAG strategy's LLM (``rag_hyde``, ``rag_raptor``, ...).

    HyDE, query rewrite, RAPTOR, propositions, corrective / self-RAG grading,
    synthesis and citation verification each resolve as their own role.
    """
    value = str(getattr(strategy, "value", strategy) or "").strip().lower()
    return f"rag_{value}" if value else "rag_strategy"


def env_pin_model(task_type: str) -> str:
    """The per-role env pin (``DEFAULT_*_MODEL``) for *task_type*, or ``""``."""
    import os

    name = ROLE_PIN_ENV.get(task_type)
    return str(os.getenv(name) or "").strip() if name else ""


def preferred_role_model(task_type: str) -> str:
    """The first model of the saved reasoning order that can serve *task_type*.

    Respects the role's requirements (execution needs tool use) and skips
    models whose provider has no credentials. ``""`` when no order is saved or
    none of its models qualifies — the caller then keeps its existing choice.
    """
    if task_type not in REASONING_ROLES:
        return ""
    try:
        from app.ai_router.models import ModelCapability
        from app.ai_router.registry import model_registry
        from app.ai_router.selection import (
            _ensure_seeded,
            model_key,
            ordered_configured_models,
        )

        _ensure_seeded(model_registry)
        preferred = set(model_registry.preference_order(ModelCapability.TEXT_GENERATION))
        if not preferred:
            return ""
        ordered = ordered_configured_models(task_type)
        # ordered_configured_models puts ranked models first, so the head is a
        # ranked model exactly when one of them qualifies for this role.
        if ordered and model_key(ordered[0]) in preferred:
            return str(ordered[0].model_id)
    except Exception as exc:  # pragma: no cover - never block routing
        logger.warning("role_preference_lookup_failed role=%s error=%s", task_type, exc)
    return ""


def preferred_model_and_fallbacks(task_type: str, provider: Any = None) -> tuple[str, list[str]]:
    """``(model, fallback_models)`` for a single reasoning call that is not a graph
    role (answer synthesis, eval judges, …).

    With a saved reasoning order: its first eligible model for *task_type* and
    the rest of the order, then the provider's own default model as the last
    resort. Without one: ``("", [])`` — the call keeps the provider default
    exactly as before (cheapest-first is NOT applied here: on an NVIDIA
    deployment the cheapest text-capable model is the 11B vision model).
    """
    primary = preferred_role_model(task_type)
    if not primary:
        return "", []
    fallbacks: list[str] = []
    try:
        from app.ai_router.selection import resolve_fallback_models

        fallbacks = resolve_fallback_models(task_type, primary, limit=3)
    except Exception:  # pragma: no cover - never block the call
        fallbacks = []
    default = str(getattr(provider, "_default_model", "") or "")
    if default and default != primary and default not in fallbacks:
        fallbacks.append(default)
    return primary, fallbacks


def resolve_role_model(role: str, *, router: Any = None, provider: Any = None) -> str:
    """THE model for one LLM role — every role goes through here.

    *role* is a charged role label (``supervisor``, ``agent_router``,
    ``rag_hyde``, ...) or a task type. With the goal's *router* (``ModelRouter``
    / ``ModelOrchestratorAdapter``) a routed task type takes the router's
    ``model_for`` (which resolves through :func:`app.ai_router.resolve.resolve_reasoning`
    with the router's override, tenant pin and role map, then applies its
    plan / budget cap). Without one: :func:`~app.ai_router.resolve.resolve_reasoning`
    directly. ``""`` when nothing is configured (the provider reports the honest
    "no LLM configured" error).
    """
    from app.ai_router.resolve import reasoning_model, reasoning_task_type

    task = reasoning_task_type(role)
    if router is not None and task in ROUTED_TASK_TYPES:
        try:
            routed = router.model_for(task)
            if isinstance(routed, str) and routed:
                return routed
        except Exception as exc:  # pragma: no cover - never block a call
            logger.warning("role_router_lookup_failed role=%s error=%s", role, exc)
    return reasoning_model(role, router=router, provider=provider)


def servable_role_model(role: str, provider: Any, *, router: Any = None) -> str:
    """:func:`resolve_role_model`, restricted to a model *provider* can serve.

    For call sites that hand a model to a provider they did not choose (the RAG
    retrieval LLM resolvers): a tenant's own single-vendor provider is never
    handed the deployment's on-prem model — it keeps its own default.
    """
    model = resolve_role_model(role, router=router, provider=provider)
    default = getattr(provider, "_default_model", "") if provider is not None else ""
    default = default if isinstance(default, str) else ""
    if model and model != default:
        from app.providers.model_dispatch import can_serve_model

        if not can_serve_model(provider, model):
            return default
    return model


def explicit_role_model(router: Any, task_type: str) -> str:
    """The operator's EXPLICIT choice for *task_type* on *router*, or ``""``.

    Explicit = a per-agent override, the tenant's routing-policy pin, or the
    saved reasoning order. Automatic re-routing (Strategy C's "fastest model"
    verifier) must never replace one of these.
    """
    override = getattr(router, "_override", "") if router is not None else ""
    if isinstance(override, str) and override:
        return override
    from app.ai_router.deployment_roles import ROLE_ALIASES

    policy = getattr(router, "_policy_roles", None) if router is not None else None
    if isinstance(policy, dict):
        pinned = policy.get(ROLE_ALIASES.get(task_type, task_type))
        if isinstance(pinned, str) and pinned:
            return pinned
    return preferred_role_model(task_type) or env_pin_model(task_type)


def router_role_model_ids(router: Any) -> set[str]:
    """The models *router* routes its reasoning roles to (Strategy C may only
    pick among these — or a configured registry text model)."""
    out: set[str] = set()
    if router is None:
        return out
    for task in ("planning", "execution", "verification", "classification"):
        try:
            model = router.model_for(task)
        except Exception:  # pragma: no cover - never block a call
            continue
        if isinstance(model, str) and model:
            out.add(model)
    return out
