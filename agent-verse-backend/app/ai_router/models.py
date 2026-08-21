"""AI Router data models - Model Registry primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelCapability(str, Enum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    VISION = "vision"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    OCR = "ocr"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    VIDEO_UNDERSTANDING = "video_understanding"
    LLM_JUDGE = "llm_judge"


class RoutingMode(str, Enum):
    CHEAPEST = "cheapest"
    FASTEST = "fastest"
    HIGHEST_QUALITY = "highest_quality"
    COMPLIANCE_REQUIRED = "compliance_required"
    FALLBACK_CHAIN = "fallback_chain"
    TENANT_DEFAULT = "tenant_default"
    MODEL_PINNED = "model_pinned"


class TaskType(str, Enum):
    PLANNING = "planning"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    JUDGE = "judge"
    EMBEDDING = "embedding"
    OCR = "ocr"
    SPEECH = "speech"
    VIDEO = "video"
    TEXT_GENERATION = "text_generation"


@dataclass
class ModelEndpoint:
    """A specific model endpoint with its configuration."""

    provider: str
    model_id: str
    display_name: str
    capabilities: list[ModelCapability] = field(default_factory=list)
    context_window: int = 8192
    max_output_tokens: int = 4096
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    supports_streaming: bool = True
    supports_tools: bool = False
    supports_vision: bool = False
    supports_structured_output: bool = False
    is_available: bool = True
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    quality_score: float = 0.7
    compliance_ready: bool = False
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelRoutePolicy:
    """Routing policy for a specific task type."""

    task_type: TaskType
    routing_mode: RoutingMode = RoutingMode.TENANT_DEFAULT
    preferred_provider: str | None = None
    preferred_model: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    max_cost_per_1k: float | None = None
    require_compliance: bool = False
    require_tool_use: bool = False
    require_vision: bool = False
    tenant_id: str | None = None


# Public alias — RoutePolicy is the canonical name for external callers
RoutePolicy = ModelRoutePolicy


@dataclass
class ProviderHealth:
    """Real-time health metrics for a provider."""

    provider: str
    is_healthy: bool = True
    circuit_open: bool = False
    avg_latency_ms: float = 0.0
    error_rate_5m: float = 0.0
    last_error: str | None = None
    last_checked_at: str | None = None
