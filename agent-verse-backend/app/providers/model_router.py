"""Intelligent model router: task_type + criticality → best model + provider.

Usage::

    from app.providers.model_router import get_model_router, TaskType, Criticality

    router = get_model_router()
    sel = router.select(TaskType.CODING, criticality=Criticality.HIGH)
    print(sel.primary)    # "deepseek/deepseek-v3"
    print(sel.provider)   # "openrouter"
"""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field


class TaskType(str, enum.Enum):
    REASONING = "reasoning"
    CODING = "coding"
    DRAFTING = "drafting"
    ANALYSIS = "analysis"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    VISION = "vision"
    OCR = "ocr"
    LONG_CONTEXT = "long_context"
    FUNCTION_CALL = "function_call"
    EMBEDDING = "embedding"
    RERANKING = "reranking"


class Criticality(str, enum.Enum):
    CRITICAL = "critical"  # P0 — always best-quality model
    HIGH = "high"          # P1 — premium tier
    MEDIUM = "medium"      # P2 — standard tier
    LOW = "low"            # P3 — economy / local tier


@dataclass
class ModelSelection:
    primary: str                       # e.g. "anthropic/claude-3-5-sonnet"
    fallbacks: list[str]               # ordered fallback list
    provider: str                      # e.g. "openrouter" | "ollama"
    task_type: TaskType
    criticality: Criticality
    estimated_cost_per_1k: float       # USD per 1 000 tokens (0 for local)
    reasoning: str                     # human-readable justification


# ---------------------------------------------------------------------------
# Routing tables
# ---------------------------------------------------------------------------

# Cloud routing — delivered via OpenRouter or direct provider APIs
CLOUD_TASK_ROUTING: dict[str, list[str]] = {
    "reasoning":      ["anthropic/claude-3-opus",  "openai/o3",                    "anthropic/claude-3-5-sonnet"],
    "coding":         ["deepseek/deepseek-v3",      "anthropic/claude-3-5-sonnet",  "openai/gpt-4o"],
    "drafting":       ["google/gemini-flash-1.5",   "meta-llama/llama-3.1-70b-instruct", "openai/gpt-4o-mini"],
    "analysis":       ["openai/gpt-4o",             "anthropic/claude-3-5-sonnet",  "google/gemini-pro-1.5"],
    "summarization":  ["google/gemini-flash-1.5",   "anthropic/claude-3-haiku",     "openai/gpt-4o-mini"],
    "classification": ["google/gemini-flash-1.5",   "meta-llama/llama-3.1-8b-instruct", "openai/gpt-4o-mini"],
    "extraction":     ["openai/gpt-4o-mini",        "qwen/qwen-2.5-72b-instruct",   "anthropic/claude-3-haiku"],
    "vision":         ["openai/gpt-4o",             "google/gemini-pro-1.5",        "anthropic/claude-3-5-sonnet"],
    "ocr":            ["openai/gpt-4o",             "google/gemini-pro-1.5"],
    "long_context":   ["google/gemini-pro-1.5",     "anthropic/claude-3-5-sonnet"],
    "function_call":  ["openai/gpt-4o",             "anthropic/claude-3-5-sonnet",  "google/gemini-pro-1.5"],
    "embedding":      ["voyage-3",                  "text-embedding-3-small"],
    "reranking":      ["cohere/rerank-v3.5"],
}

# Ollama routing — free, private, on-device
OLLAMA_TASK_ROUTING: dict[str, list[str]] = {
    "reasoning":      ["qwen3.8:latest",          "llama3.2:latest",      "qwen3:8b"],
    "coding":         ["qwen3.8:latest",           "llama3.2:latest",      "qwen2.5-coder:7b"],
    "drafting":       ["qwen3.8:latest",           "llama3.2:latest",      "qwen3:4b"],
    "analysis":       ["qwen3.8:latest",           "llama3.2:latest",      "qwen2.5:14b"],
    "summarization":  ["qwen3.8:latest",           "llama3.2:latest"],
    "classification": ["qwen3.8:latest",           "llama3.2:latest"],
    "extraction":     ["qwen3.8:latest",           "llama3.2:latest"],
    "vision":         ["glm-ocr:latest"],
    "ocr":            ["glm-ocr:latest"],
    "long_context":   ["qwen3.8:latest",           "gpt-oss:latest"],
    "function_call":  ["qwen3.8:latest"],
    "embedding":      ["qwen3-embedding:latest"],
    "reranking":      ["qwen3-embedding:latest"],
}

# Rough cost estimates (USD per 1 000 tokens) for cloud models
_CLOUD_COST_PER_1K: dict[str, float] = {
    "anthropic/claude-3-opus": 0.015,
    "anthropic/claude-3-5-sonnet": 0.003,
    "anthropic/claude-3-haiku": 0.00025,
    "openai/gpt-4o": 0.005,
    "openai/gpt-4o-mini": 0.00015,
    "openai/o3": 0.06,
    "google/gemini-pro-1.5": 0.0035,
    "google/gemini-flash-1.5": 0.00035,
    "deepseek/deepseek-v3": 0.00027,
    "meta-llama/llama-3.1-70b-instruct": 0.00088,
    "meta-llama/llama-3.1-8b-instruct": 0.00018,
    "qwen/qwen-2.5-72b-instruct": 0.00090,
}


class ModelRouter:
    """Select the best model for a given task type and criticality level.

    Decision logic:
    - CRITICAL or HIGH criticality → always cloud (best-quality model)
    - LOW criticality + Ollama available → local Ollama (free)
    - MEDIUM criticality + prefer_local=True + Ollama available → local Ollama
    - Otherwise → cloud routing table
    """

    def __init__(self, use_ollama_when_available: bool = True) -> None:
        self._use_ollama = use_ollama_when_available
        self._ollama_available = bool(os.getenv("OLLAMA_BASE_URL"))

    def select(
        self,
        task_type: TaskType | str,
        criticality: Criticality | str = Criticality.MEDIUM,
        required_capabilities: list[str] | None = None,
        context_tokens: int = 0,
        prefer_local: bool = False,
        override_model: str | None = None,
    ) -> ModelSelection:
        """Select the best model for *task_type* at *criticality* level.

        Args:
            task_type: One of the TaskType enum values (or equivalent string).
            criticality: Criticality level; drives quality vs cost tradeoff.
            required_capabilities: List of required capability strings
                (e.g. ["vision", "tools"]). Not yet enforced — reserved for
                future capability-aware routing.
            context_tokens: Estimated context size; used to steer toward
                long-context models when large.
            prefer_local: Prefer local Ollama even for MEDIUM criticality.
            override_model: Bypass routing and use this model directly.

        Returns:
            ModelSelection with primary model, ordered fallbacks, and metadata.
        """
        task = TaskType(task_type) if isinstance(task_type, str) else task_type
        crit = Criticality(criticality) if isinstance(criticality, str) else criticality

        # Hard override — skip all routing logic
        if override_model:
            return ModelSelection(
                primary=override_model,
                fallbacks=[],
                provider="override",
                task_type=task,
                criticality=crit,
                estimated_cost_per_1k=0.0,
                reasoning=f"override model: {override_model}",
            )

        # Long-context steering
        _task = task
        if context_tokens > 100_000 and task not in (TaskType.LONG_CONTEXT,):
            _task = TaskType.LONG_CONTEXT

        # Determine whether to use local Ollama
        use_local = (
            self._use_ollama
            and self._ollama_available
            and crit not in (Criticality.CRITICAL, Criticality.HIGH)
            and (prefer_local or crit == Criticality.LOW)
        )

        if use_local:
            models = OLLAMA_TASK_ROUTING.get(
                _task.value,
                OLLAMA_TASK_ROUTING.get(task.value, OLLAMA_TASK_ROUTING["drafting"]),
            )
            return ModelSelection(
                primary=models[0],
                fallbacks=list(models[1:]),
                provider="ollama",
                task_type=task,
                criticality=crit,
                estimated_cost_per_1k=0.0,
                reasoning=(
                    f"Local Ollama selected for {task.value} at {crit.value} "
                    f"criticality (free, private)"
                ),
            )

        # Cloud routing
        models = CLOUD_TASK_ROUTING.get(
            _task.value,
            CLOUD_TASK_ROUTING.get(task.value, CLOUD_TASK_ROUTING["drafting"]),
        )

        # Criticality filtering
        if crit == Criticality.CRITICAL:
            selected = models[:1]           # always the best
        elif crit == Criticality.LOW:
            selected = models[-1:]          # always the cheapest
        elif crit == Criticality.HIGH:
            selected = models[:2]           # top two
        else:
            selected = list(models)         # full list (MEDIUM)

        primary = selected[0]
        cost = _CLOUD_COST_PER_1K.get(primary, 0.003)

        return ModelSelection(
            primary=primary,
            fallbacks=list(selected[1:]),
            provider="openrouter",
            task_type=task,
            criticality=crit,
            estimated_cost_per_1k=cost,
            reasoning=(
                f"Cloud model '{primary}' selected for {task.value} "
                f"at {crit.value} criticality"
            ),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_router: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """Return the module-level singleton ModelRouter."""
    global _default_router
    if _default_router is None:
        _default_router = ModelRouter()
    return _default_router
