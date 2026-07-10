# Platform Lifecycle and Core Architecture

**Date:** 2026-07-08

## Purpose

This document explains the full AgentVerse execution lifecycle: how a user goal becomes a queued worker task, how the `AgentGraph` executes it, how tools are discovered and invoked, how results are verified, and how the run feeds memory, evals, and observability.

## Platform Entry Points

AgentVerse exposes goals through the backend API. A goal submission carries:

- `goal`: natural-language objective
- `agent_id`: optional explicit agent binding
- `connector_ids`: derived from the selected agent when not provided directly
- `tenant_id`: derived from API key authentication
- `priority`: used by queues
- `workflow_mode`: single-agent or workflow execution
- `dry_run`: whether to execute real tools or simulate

The main orchestration path is:

```text
API request
  -> TenantMiddleware authenticates API key
  -> GoalService creates goal row
  -> GoalService emits goal_started / worker_started events
  -> GoalQueue / Celery enqueues run_goal
  -> Celery worker calls app.scaling.tasks.run_goal
  -> Worker builds AgentGraph + MCP tool context
  -> AgentGraph runs lifecycle
  -> Goal status persisted + SSE event stream updated
```

## Tenant Context

`TenantContext` in `app/tenancy/context.py` is passed into nearly every subsystem:

```python
TenantContext(
    tenant_id="...",
    plan=PlanTier.FREE | STARTER | PROFESSIONAL | ENTERPRISE,
    api_key_id="...",
    roles=(...),
)
```

It controls:

- tenant isolation
- plan limits
- rate limits
- DB row-level security
- policy evaluation
- cost tracking
- memory scoping
- knowledge collection scoping
- audit ownership

## Queue Routing

Celery queues are plan-aware:

```text
goals.free
goals.starter
goals.professional
goals.enterprise
```

This prevents noisy-neighbor behavior. Enterprise goals can run on isolated queue capacity.

## Worker Runtime

`app/scaling/tasks.py::run_goal` performs the worker-side setup:

1. Load tenant context.
2. Load agent config from DB:
   - `autonomy_mode`
   - `max_iterations`
   - `system_prompt`
3. Register builtin MCP handlers in the worker process.
4. Build `MCPClient` with Redis-backed vault secret resolver.
5. Discover tools for the agent's `connector_ids`.
6. Build `ToolContext`.
7. Construct `AgentGraph`.
8. Wrap graph with `_WorkerMCPAgentRunner` so tool context and agent system prompt are injected into graph context.
9. Execute with cancellation/pause polling via `_run_with_signals`.

## AgentGraph Lifecycle

The core graph is in `app/agent/graph.py`.

```text
initialize
  -> rag_retrieval
  -> plan
  -> execute
  -> verify
  -> route
```

The route node decides:

```text
verification_success=true -> complete
retry=false              -> failed
iterations>=max          -> failed
stagnation detected      -> failed
otherwise                -> replan
```

## State Model

`AgentState` carries the execution state:

```python
AgentState(
    goal="...",
    tenant_ctx=tenant_ctx,
    status=GoalStatus.PLANNING,
    plan=[...],
    steps=[StepResult(...)],
    iterations=0,
    verification_success=False,
    verification_feedback="",
    context={...},
)
```

Each step is represented as:

```python
StepResult(
    description="Step 1: Search Jira...",
    status=StepStatus.COMPLETE,
    output="...",
    tool_calls=[...],
    error=None,
)
```

## Tool Context

`ToolContext` contains connectors and tools available to the selected agent:

```python
ToolContext(
    connectors=[{"id": "builtin-jira", "name": "Jira Connector"}],
    tools=[ToolRef(name="jira_search_issues", input_schema={...}), ...]
)
```

The executor only receives tools from the agent's assigned connectors. Tool names are sanitized before sending to OpenAI because function names must match `^[a-zA-Z0-9_-]{1,64}$`.

## Tool Execution Path

```text
Executor LLM response
  -> tool_calls parsed
  -> validate_tool_name
  -> validate_tool_arguments
  -> policy + guardrail checks
  -> MCPClient.call_tool
  -> builtin handler or remote MCP HTTP
  -> output sanitized
  -> tool_call_complete event
  -> StepResult output
```

### Builtin Tool Dispatch

For builtin connectors like Jira:

```text
server_id=builtin-jira
  -> restore builtin handler from MCPRegistry
  -> extract auth_config
  -> resolve vault://connectors/... secret with tenant_ctx
  -> call app.mcp.servers.jira_server.call_tool
  -> HTTP request to Jira REST API
```

## Verification

The verifier receives:

- original goal
- executed steps
- outputs
- failures
- ungrounded markers

It responds with strict JSON:

```json
{"success": true, "reason": "Goal was achieved because..."}
```

or:

```json
{"success": false, "reason": "Goal not achieved...", "retry": true}
```

## Completion Effects

On successful completion:

```text
Goal status -> complete
worker_complete event emitted
ExecutionMemory records winning plan
LongTermMemory stores success pattern
EvalRunner scores the run
Audit trail records governed actions
Metrics increment counters/histograms
SSE stream updates frontend
```

On failure:

```text
Goal status -> failed
verification_feedback persisted
ExecutionMemory records failure
Reflexion/optimizer may store lesson
Audit/metrics record failure
```

## Real Example: Jira Goal

Goal:

```text
List the 5 most recently updated Jira issues and return their issue keys and summaries.
```

Execution path:

```text
GoalService -> Celery -> AgentGraph
Planner -> Step 1: call jira_search_issues
Executor -> tool_call: jira_search_issues({jql, max_results})
MCPClient -> builtin-jira -> vault secret resolved
Jira REST API -> HTTP 200
Verifier -> success=true
Goal -> complete in 1 iteration
```
