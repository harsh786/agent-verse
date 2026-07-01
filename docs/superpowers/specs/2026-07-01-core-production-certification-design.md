# Core Production Certification Design

## Purpose

AgentVerse needs a repeatable way to prove that production-critical connectors work through the full agent path, not only through isolated unit tests. The first certification wave will focus on the platform and the highest-value connectors: Jira, GitHub, Slack, Google Workspace, Salesforce or HubSpot, Stripe, Datadog, Sentry, AWS, and Postgres.

This design also fixes only the agent execution and Agent Civilization blockers that directly affect connector-backed agent reliability.

## Goals

- Certify the top connector set with static, mocked, and live-ready checks.
- Prove a connector can be discovered, called, surfaced to an agent, executed by Celery, persisted to events, and rendered as a result artifact.
- Remove known worker-path failures: missing MCP client, missing connector IDs, cross-event-loop persistence failures, fake placeholder tool calls, and unsafe argument gaps.
- Make Agent Civilization safe enough for connector-backed spawned agents by addressing RLS, feature flag, spawn contract, tool inheritance, and control-action mismatches.
- Provide commands and manifests that can be run locally and in CI.

## Non-Goals

- Certify every one of the 227+ public catalog connectors in this phase.
- Build live sandboxes for third-party systems where credentials are unavailable.
- Redesign the full connector UI beyond what certification needs.
- Make all Agent Civilization autonomy features production-perfect; only connector-impacting blockers are in scope.

## Connector Certification Model

Each connector can reach three certification levels.

### Static Certification

Static certification validates that the connector definition is internally consistent.

Required checks:
- Catalog entry exists with category, auth type, default URL, and description.
- Registry wiring exists when a built-in server is expected.
- Required secret names are documented.
- Tool definitions have names, descriptions, schemas, and risk hints.
- Auth type is supported by `MCPClient` or by the connector-specific adapter.
- Connector has a health-check strategy.

### Mocked Certification

Mocked certification proves the AgentVerse platform can operate the connector without external credentials.

Required checks:
- Register connector in a tenant-scoped registry.
- Resolve secrets through `RedisConnectorSecretStore`.
- Discover tools through `MCPClient`.
- Execute one read-only tool call against mocked provider responses.
- Return actionable errors for auth failure and provider failure.
- Feed tool output into `ResultArtifact` generation.
- Run one agent goal through an in-process graph using the connector.

### Live-Ready Certification

Live-ready certification provides a safe command that runs only when credentials are present.

Required checks:
- Validate credentials with a non-destructive provider endpoint.
- Execute one read-only smoke operation.
- Enqueue one Celery-backed goal for the connector.
- Persist terminal status, `tool_call_complete`, `verification_done`, and `goal_complete` events.
- Emit a useful `result_artifact`.

Live tests must be opt-in and skipped when credentials are unavailable.

## Certification Manifest

Add a manifest file that becomes the source of truth for this certification wave.

Path:
`agent-verse-backend/app/mcp/certification_manifest.py`

Manifest shape:

```python
CONNECTOR_CERTIFICATION_TARGETS = {
    "jira": {
        "display_name": "Jira",
        "connector_ids": ["builtin-jira", "atlassian-rest"],
        "category": "project_management",
        "auth_modes": ["basic", "custom_header", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "jira_search_issues",
        "read_arguments": {
            "jql": "assignee = currentUser() AND created >= -26w ORDER BY created DESC",
            "max_results": 10,
        },
        "expected_artifact_kind": "table",
        "live_env": ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"],
    },
}
```

The manifest should include top connector entries for Jira, GitHub, Slack, Google Workspace, Salesforce or HubSpot, Stripe, Datadog, Sentry, AWS, and Postgres.

## Connector Harness

Add a certification harness under:
`agent-verse-backend/app/mcp/certification.py`

Responsibilities:
- Load target metadata from the manifest.
- Build a tenant-scoped test context.
- Register a connector in Redis or use an existing connector ID.
- Discover tools with `MCPClient`.
- Execute a read-only smoke call.
- Normalize result into a `ConnectorCertificationResult`.

Result shape:

```python
{
    "connector": "jira",
    "level": "mocked",
    "status": "passed",
    "checks": [
        {"name": "catalog", "status": "passed"},
        {"name": "tool_discovery", "status": "passed"},
        {"name": "read_call", "status": "passed"},
    ],
    "warnings": [],
    "duration_ms": 123,
}
```

## API And CLI Surface

Add backend support for running and listing certification results.

Endpoints:
- `GET /connectors/certification/targets`
- `POST /connectors/certification/run`
- `GET /connectors/certification/results`

CLI command:
- `uv run agentverse connectors certify --level static`
- `uv run agentverse connectors certify --connector jira --level mocked`
- `uv run agentverse connectors certify --connector jira --level live`

The live command must skip with a clear message if required credentials are missing.

## Agent Execution Fixes

Certification depends on the queued worker path working exactly like the in-process graph path.

Required fixes:
- `GoalService.submit_goal()` passes `connector_ids` to `CeleryGoalTaskQueue`.
- `CeleryGoalTaskQueue.enqueue_goal()` forwards `connector_ids` to `run_goal`.
- `run_goal()` accepts `connector_ids` and builds `ToolContext` inside the worker execution event loop.
- Worker-created `AgentGraph` gets an `MCPClient` and connector `ToolContext`.
- Worker DB persistence uses fresh async DB resources per event loop or a single loop-safe execution wrapper.
- Tool-call parser ignores placeholders such as `server_name.tool_name`, `server_name.*`, and `python.datetime`.
- Jira argument repair handles safe, goal-derived JQL for common assigned-to-me cases.
- Result artifact generation uses the latest successful relevant tool call.

## Result Artifact Integration

Certification verifies not just execution, but presentation readiness.

Required artifact behavior:
- Jira returns a table of keys and summaries.
- GitHub issues/PRs return a table or cards.
- Slack messages return cards with author/channel/time.
- Stripe objects return tables with amount/status/date.
- Datadog/Sentry/AWS/Postgres return structured cards or tables.
- Failed tool calls produce failed artifacts with actionable evidence.

## Agent Civilization Fixes

Only connector-impacting Agent Civilization blockers are in scope.

### RLS And Persistence

Add `sqlalchemy_rls_context` to civilization DB operations that read or write tenant-scoped tables.

Affected areas:
- `app/api/civilization.py`
- `app/civilization/governor.py`
- `app/civilization/society.py`
- `app/civilization/blackboard.py`
- `app/civilization/bus.py`
- `app/civilization/events.py`
- `app/civilization/learning.py`

### Feature Flag Gating

Celery tasks must no-op when `CIVILIZATION_ENABLED` is false:
- `civilization_tick`
- `civilization_learning_step`
- `discover_and_tick_civilizations`

### Spawn Contract

Align `AgentGraph` civilization spawn calls with `execute_spawn_tool()`.

Required inputs:
- `capability`
- `goal`
- `requester_agent_id`
- `depth`
- `parent_budget_usd`
- `parent_policy_ids`
- `civilization_id`

### Connector Inheritance

Spawned agents must inherit or explicitly receive connector/tool context when the parent task requires connector-backed execution.

### Control Action Alignment

Align frontend and backend civilization controls:
- Frontend should call `adjust_budget`, not `set_budget`.
- Backend should return consistent status payloads for pause, resume, throttle, adjust budget, and kill.

## Testing Strategy

### Backend Static/Mocked Tests

Commands:

```bash
cd agent-verse-backend
uv run pytest tests/mcp/test_connector_certification.py
uv run pytest tests/mcp/test_mcp_client.py
uv run pytest tests/services/test_goal_service.py
uv run pytest tests/scaling/test_celery_tasks_coverage.py
```

### Live Opt-In Tests

Commands:

```bash
cd agent-verse-backend
uv run pytest tests/integration/test_certified_connectors_live.py -m integration
```

Live tests must skip when credentials are missing.

### Agent Civilization Tests

Commands:

```bash
cd agent-verse-backend
uv run pytest tests/civilization
uv run pytest tests/api/test_civilization_api.py
uv run pytest tests/e2e/test_civilization_autonomy.py
```

### Frontend Tests

Commands:

```bash
cd agent-verse-frontend
npm run test -- src/features/connectors src/features/civilization src/features/goals
npm run typecheck
```

## Rollout Plan

### Phase 1: Certification Foundation

- Add certification manifest.
- Add certification harness.
- Add static certification tests.
- Add connector result schema.

### Phase 2: Top Connector Mocked Certification

- Certify Jira first.
- Add GitHub, Slack, Postgres next.
- Add Stripe, Datadog, Sentry, AWS, Google Workspace.
- Add Salesforce or HubSpot based on available credentials.

### Phase 3: Worker And Goal E2E Hardening

- Ensure Celery worker path passes connector IDs and tool context.
- Ensure terminal events persist reliably.
- Ensure result artifacts render for connector-backed goals.

### Phase 4: Agent Civilization Blockers

- Add RLS contexts.
- Gate Celery tasks.
- Fix spawn contract.
- Add connector inheritance for spawned agents.
- Align controls.

### Phase 5: Live Certification And Runbook

- Add live test command.
- Add credentials documentation.
- Add troubleshooting table for auth, tool discovery, rate limits, and result artifacts.

## Acceptance Criteria

- Jira UI-submitted goal completes through Celery and shows a structured result artifact.
- Static certification passes for all top connectors.
- Mocked certification passes for all top connectors.
- Live certification passes or cleanly skips for connectors without credentials.
- Celery worker does not emit `MCP client unavailable` for certified connectors.
- Placeholder tool calls do not fail goals.
- Connector failures create actionable result artifacts.
- Civilization DB operations respect RLS.
- Civilization Celery tasks no-op when disabled.
- Spawned agents can inherit connector context when required.

## Risks

- Live provider tests require credentials and may hit rate limits.
- Some built-in connector modules are shallow wrappers and may need per-connector hardening.
- Agent Civilization has broad surface area; scope must stay limited to connector-impacting blockers in this phase.
- Existing Celery async DB loop issues may require a broader worker loop refactor if fresh-resource fixes are insufficient.

## Design Decisions

- HubSpot will be the first CRM certified connector because its auth and read APIs are simpler for the first certification wave. Salesforce can be added after the harness is stable.
- Certification results will return through API/CLI responses in this phase. Persisted certification dashboards are out of scope for this phase and can be added after static, mocked, and live-ready checks are stable.
