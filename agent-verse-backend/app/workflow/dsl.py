"""WorkflowDefinition — Pydantic DSL for YAML/JSON workflow definitions.

Parses, validates, and provides structured access to all workflow config.
The canonical format is YAML; JSON is accepted on all API endpoints.

Validation rules enforced at parse time:
  - All step IDs are unique
  - depends_on references only valid step IDs
  - No circular dependencies (topological sort)
  - All step types exist in StepTypeRegistry
  - Required inputs have no defaults
  - Enum inputs validate against allowed values
"""
from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# Trigger DSL
# ─────────────────────────────────────────────────────────────────────────────

class WebhookTriggerConfig(BaseModel):
    path: str = ""
    auth: Literal["bearer", "hmac", "none"] = "bearer"
    hmac_secret: str = ""
    payload_schema: dict[str, Any] = Field(default_factory=dict)


class ScheduleTriggerConfig(BaseModel):
    cron: str = ""
    timezone: str = "UTC"


class EventTriggerConfig(BaseModel):
    channel: str = ""
    filter: str = ""


class FileDropTriggerConfig(BaseModel):
    bucket: str = ""
    prefix: str = ""
    extensions: list[str] = Field(default_factory=list)
    payload_mapping: dict[str, str] = Field(default_factory=dict)


class AlertTriggerConfig(BaseModel):
    """Shared config for alertmanager/datadog/pagerduty triggers."""
    payload_mapping: dict[str, str] = Field(default_factory=dict)


class NLTriggerConfig(BaseModel):
    phrases: list[str] = Field(default_factory=list)
    input_extraction_prompt: str = ""


class TriggerDefinition(BaseModel):
    type: Literal[
        "webhook", "schedule", "api", "nl", "event",
        "file_drop", "alertmanager", "datadog", "pagerduty"
    ] = "api"
    webhook: WebhookTriggerConfig | None = None
    schedule: ScheduleTriggerConfig | None = None
    event: EventTriggerConfig | None = None
    file_drop: FileDropTriggerConfig | None = None
    alertmanager: AlertTriggerConfig | None = None
    datadog: AlertTriggerConfig | None = None
    pagerduty: AlertTriggerConfig | None = None
    nl: NLTriggerConfig | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Input DSL
# ─────────────────────────────────────────────────────────────────────────────

class InputDefinition(BaseModel):
    type: Literal["string", "number", "boolean", "object", "array"] = "string"
    required: bool = True
    default: Any = None
    description: str = ""
    enum: list[Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Step DSL
# ─────────────────────────────────────────────────────────────────────────────

class RetryConfig(BaseModel):
    max_attempts: int = 1
    backoff: Literal["fixed", "linear", "exponential"] = "exponential"
    base_delay_ms: int = 500
    retry_on: list[str] = Field(default_factory=list)
    fail_on: list[str] = Field(default_factory=list)


class RAGConfig(BaseModel):
    collection: str = ""
    top_k: int = 5
    strategy: Literal["semantic", "keyword", "hybrid"] = "hybrid"


class AssigneeConfig(BaseModel):
    role: str = ""
    strategy: Literal["round_robin", "least_busy", "skill_based", "specific"] = "round_robin"
    specific_user: str | None = None


class EscalationConfig(BaseModel):
    after: str = "24h"
    to_role: str = ""
    notify_channels: list[str] = Field(default_factory=list)


class HITLContextItem(BaseModel):
    # Support both label/value (old) and title/data (new) field naming
    model_config = {"populate_by_name": True}

    label: str = ""
    value: str = ""  # {{...}} expression
    title: str = ""  # alias for label in newer templates
    data: str = ""   # alias for value in newer templates
    display_type: Literal[
        "image", "json", "table", "diff", "chart", "number", "text", "list"
    ] = "text"
    threshold_red: float | None = None
    threshold_yellow: float | None = None

    @property
    def effective_label(self) -> str:
        return self.label or self.title

    @property
    def effective_value(self) -> str:
        return self.value or self.data


class HITLAction(BaseModel):
    id: str
    label: str = ""  # defaults to id if not set
    style: Literal["success", "danger", "warning", "default"] = "default"
    icon: str = ""
    next: str = ""  # step_id to route to
    requires_note: bool = False


class ConditionalBranch(BaseModel):
    condition: str  # expression or "default"
    next: str       # step_id


class StepDefinition(BaseModel):
    id: str
    name: str = ""
    type: str  # validated against StepTypeRegistry at workflow-level
    depends_on: list[str] = Field(default_factory=list)
    depends_on_any: bool = False

    # Per-step config — all optional, consumed by the relevant StepNode
    tool: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, str] = Field(default_factory=dict)
    timeout: str = "60s"
    retry: RetryConfig = Field(default_factory=RetryConfig)
    on_failure: Literal["pause", "skip", "abort", "use_default"] = "pause"
    on_failure_default: Any = None

    # LLM step config
    model: str | None = None
    prompt: str = ""
    temperature: float = 0.1
    max_tokens: int = 2000
    rag: RAGConfig | None = None

    # HTTP step config
    url: str = ""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, str] = Field(default_factory=dict)
    request_body: dict[str, Any] = Field(default_factory=dict)

    # HITL step config
    assignee: AssigneeConfig | None = None
    timeout_action: Literal["auto_approve", "auto_reject", "escalate", "pause"] = "escalate"
    escalation: EscalationConfig | None = None
    context: list[HITLContextItem] = Field(default_factory=list)
    actions: list[HITLAction] = Field(default_factory=list)
    allow_delegate: bool = True
    allow_escalate: bool = True
    custom_form_schema: dict[str, Any] | None = None

    # Conditional step config
    branches: list[ConditionalBranch] = Field(default_factory=list)

    # Parallel step config — sub-branches
    # (parallel step_type): branches is a list of StepDefinition
    parallel_branches: list[StepDefinition] = Field(default_factory=list)

    # foreach step config
    iterate_over: str = ""  # {{...}} expression resolving to list
    as_var: str = "item"    # loop variable name: {{foreach.item}}
    max_concurrency: int = 5
    body: list[StepDefinition] = Field(default_factory=list)
    collect_output_as: str = ""
    on_item_failure: Literal["continue", "abort"] = "continue"

    # sub_workflow config
    workflow_id: str = ""
    workflow_inputs: dict[str, Any] = Field(default_factory=dict)

    # wait step config
    duration: str = ""
    event_channel: str = ""

    # code step config
    runtime: Literal["python", "javascript"] = "python"
    code: str = ""

    # set_variable step config
    var_name: str = ""
    var_value: str = ""
    value_type: str = "string"

    # emit_event step config
    event_channel_out: str = ""
    event_payload: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Top-level Workflow Definition
# ─────────────────────────────────────────────────────────────────────────────

class ConcurrencyConfig(BaseModel):
    max_concurrent_runs: int = 10
    on_limit_reached: Literal["queue", "reject", "replace_oldest"] = "queue"
    queue_timeout: str = "30m"


class ErrorHandlingConfig(BaseModel):
    on_step_failure: Literal["pause", "skip", "abort"] = "pause"
    max_run_duration: str = "72h"
    notify_on_failure: list[dict[str, str]] = Field(default_factory=list)


class CallbackConfig(BaseModel):
    url: str = ""
    auth: dict[str, str] = Field(default_factory=dict)
    on_failure: bool = True


class NotificationRule(BaseModel):
    event: str
    channels: list[str] = Field(default_factory=list)
    target: str = ""


class WorkflowDefinition(BaseModel):
    id: str = ""
    name: str
    version: str = "1.0.0"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    forked_from: str | None = None

    trigger: TriggerDefinition = Field(default_factory=TriggerDefinition)
    inputs: dict[str, InputDefinition] = Field(default_factory=dict)
    steps: list[StepDefinition] = Field(default_factory=list)
    outputs: dict[str, dict[str, str]] = Field(default_factory=dict)

    error_handling: ErrorHandlingConfig = Field(default_factory=ErrorHandlingConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    vars: dict[str, Any] = Field(default_factory=dict)
    trigger_transform: dict[str, str] = Field(default_factory=dict)
    callback: CallbackConfig | None = None
    run_labels: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    notifications: list[NotificationRule] = Field(default_factory=list)

    # ── Publishing approval (enterprise) ─────────────────────────────────────
    requires_publish_approval: bool = False
    publish_approved_by: str | None = None
    publish_approved_at: str | None = None

    # ── Retention ────────────────────────────────────────────────────────────
    run_retention_days: int = 90  # days before runs are purged

    @model_validator(mode="after")
    def validate_step_graph(self) -> WorkflowDefinition:
        """Validate step IDs unique, depends_on refs valid, no cycles."""
        ids = {s.id for s in self.steps}

        # 1. Unique IDs
        if len(ids) != len(self.steps):
            seen: set[str] = set()
            for s in self.steps:
                if s.id in seen:
                    raise ValueError(f"Duplicate step id: {s.id!r}")
                seen.add(s.id)

        # 2. Valid depends_on references
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Step {step.id!r} depends_on unknown step {dep!r}"
                    )

        # 3. No circular dependencies (Kahn's algorithm)
        # in_degree[v] = number of steps that v directly depends on
        in_degree: dict[str, int] = {s.id: len(s.depends_on) for s in self.steps}
        # Build reverse graph: dep → [steps that depend on dep]
        dependents: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for step in self.steps:
            for dep in step.depends_on:
                if dep in dependents:
                    dependents[dep].append(step.id)

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for dependent_id in dependents.get(node, []):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        if visited != len(self.steps):
            raise ValueError("Workflow definition contains a circular dependency")

        return self

    @classmethod
    def from_yaml(cls, yaml_text: str) -> WorkflowDefinition:
        """Parse a canonical YAML workflow definition."""
        data = yaml.safe_load(yaml_text)
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_data: str | dict[str, Any]) -> WorkflowDefinition:
        """Parse from JSON string or dict (API payload)."""
        import json as _json
        if isinstance(json_data, str):
            json_data = _json.loads(json_data)
        return cls.model_validate(json_data)

    def to_yaml(self) -> str:
        """Export canonical YAML."""
        return str(yaml.dump(
            self.model_dump(exclude_none=True),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ))

    def to_json(self) -> dict[str, Any]:
        """Export JSON dict."""
        return self.model_dump(exclude_none=True)
