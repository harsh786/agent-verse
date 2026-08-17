---
description: "Generate a LangGraph agent node with OTel, error handling, and retry"
---

# Generate LangGraph Agent Node

Generate a production-grade LangGraph node for the AgentVerse agent execution loop.

## Node Details
- **Node name**: {{node_name}}
- **Role**: {{role}}  <!-- planner | executor | verifier | specialist -->
- **Inputs from state**: {{state_inputs}}
- **Outputs to state**: {{state_outputs}}

## What to Generate

### 1. Node Function (`app/agent/nodes/{{node_name}}.py`)
```python
from __future__ import annotations
import structlog
from opentelemetry import trace
from app.agent.state import AgentState
from app.providers.base import LLMProvider

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

async def {{node_name}}_node(
    state: AgentState,
    provider: LLMProvider,
) -> AgentState:
    """{{description}}"""
    with tracer.start_as_current_span("agent.node.{{node_name}}") as span:
        span.set_attribute("tenant_id", state["tenant_id"])
        span.set_attribute("goal.id", state["goal_id"])
        span.set_attribute("iteration", state.get("iteration", 0))

        log.info("agent.node.{{node_name}}.start",
            goal_id=state["goal_id"],
            tenant_id=state["tenant_id"],
        )

        try:
            # LLM call via provider abstraction (NOT direct Anthropic/OpenAI)
            response = await provider.complete(
                system_prompt=SYSTEM_PROMPT,
                messages=build_messages(state),
                temperature=0.1,
                max_tokens=4096,
            )

            # Update state
            new_state = {**state, "{{output_key}}": parse_response(response)}

            log.info("agent.node.{{node_name}}.done", goal_id=state["goal_id"])
            span.set_attribute("result.success", True)
            return new_state

        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("result.success", False)
            log.error("agent.node.{{node_name}}.failed",
                error=str(exc),
                goal_id=state["goal_id"],
            )
            raise
```

### 2. Prompt (`app/agent/prompts.py` addition)
- System prompt for this node's role
- Clear output format specification
- Few-shot examples if needed
- Jinja2 template variables: `{goal}`, `{context}`, `{org_name}`

### 3. Unit Tests (`tests/agent/test_{{node_name}}_node.py`)
- Mock `FakeProvider` for deterministic tests
- Test: successful output parsing
- Test: malformed LLM response handling
- Test: state is updated correctly

## Constraints
- NEVER call Anthropic/OpenAI directly — use `LLMProvider` abstraction
- NEVER modify state in-place — return new dict: `{**state, "key": value}`
- ALWAYS add OTel span
- ALWAYS log start and done events
- ALWAYS handle and log exceptions before re-raising
- Provider supports: `FakeProvider` (tests), `AnthropicProvider`, `OpenAIProvider`
