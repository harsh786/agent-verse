# Core Production Certification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a certification system that proves top production connectors work through discovery, tool calls, agent execution, result artifacts, and the Celery worker path, while fixing connector-impacting Agent Civilization blockers.

**Architecture:** Add a connector certification manifest and harness, then certify the first production connector set with static and mocked checks. Harden the Celery goal path so certified connectors work in queued execution. Add narrowly scoped Agent Civilization fixes for RLS, task gating, spawn contract, and connector inheritance.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Redis, Celery, pytest, React/Vite/TanStack Query for impacted UI verification.

---

## File Structure

**New backend files**
- `agent-verse-backend/app/mcp/certification_manifest.py`: Static metadata for the first certified connector wave.
- `agent-verse-backend/app/mcp/certification.py`: Certification harness and result models.
- `agent-verse-backend/tests/mcp/test_connector_certification_manifest.py`: Manifest shape and top connector coverage tests.
- `agent-verse-backend/tests/mcp/test_connector_certification.py`: Harness static/mocked behavior tests.
- `agent-verse-backend/tests/integration/test_certified_connectors_live.py`: Opt-in live connector smoke tests.
- `agent-verse-backend/tests/scaling/test_worker_connector_context.py`: Focused worker connector context tests.

**Modified backend files**
- `agent-verse-backend/app/api/connectors.py`: Certification endpoints.
- `agent-verse-backend/app/cli/main.py`: Certification CLI command.
- `agent-verse-backend/app/services/goal_queue.py`: Ensure connector IDs are in Celery payload.
- `agent-verse-backend/app/services/goal_service.py`: Ensure API-submitted goals pass connector IDs to queue and return result artifacts.
- `agent-verse-backend/app/scaling/tasks.py`: Worker context, feature gates, loop-safe persistence.
- `agent-verse-backend/app/agent/graph.py`: Spawn tool contract and connector context inheritance.
- `agent-verse-backend/app/agent/tool_calls.py`: Placeholder filtering and safe Jira argument repair.
- `agent-verse-backend/app/agent/tool_context.py`: Connector alias resolution.
- `agent-verse-backend/app/api/civilization.py`: RLS and control action alignment.
- `agent-verse-backend/app/civilization/*.py`: Targeted RLS/spawn/budget/control fixes.

---

## Task 1: Certification Manifest

**Files:**
- Create: `agent-verse-backend/app/mcp/certification_manifest.py`
- Test: `agent-verse-backend/tests/mcp/test_connector_certification_manifest.py`

- [ ] **Step 1: Write failing manifest test**

Create `tests/mcp/test_connector_certification_manifest.py`:

```python
from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS


def test_manifest_contains_core_connector_wave() -> None:
    required = {
        "jira",
        "github",
        "slack",
        "google_workspace",
        "hubspot",
        "stripe",
        "datadog",
        "sentry",
        "aws",
        "postgres",
    }

    assert required.issubset(CONNECTOR_CERTIFICATION_TARGETS)


def test_manifest_entries_have_required_fields() -> None:
    for key, entry in CONNECTOR_CERTIFICATION_TARGETS.items():
        assert entry["display_name"]
        assert entry["category"]
        assert entry["auth_modes"]
        assert entry["read_tool"]
        assert isinstance(entry["read_arguments"], dict)
        assert entry["expected_artifact_kind"] in {"table", "cards", "json", "text"}
        assert isinstance(entry["live_env"], list), key
```

- [ ] **Step 2: Run test to confirm failure**

Run: `cd agent-verse-backend && uv run pytest tests/mcp/test_connector_certification_manifest.py -q`

Expected: import error for `app.mcp.certification_manifest`.

- [ ] **Step 3: Implement manifest**

Create `app/mcp/certification_manifest.py`:

```python
from __future__ import annotations

from typing import Any

ConnectorTarget = dict[str, Any]

CONNECTOR_CERTIFICATION_TARGETS: dict[str, ConnectorTarget] = {
    "jira": {
        "display_name": "Jira",
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
    "github": {
        "display_name": "GitHub",
        "category": "devtools",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "github_search_issues",
        "read_arguments": {"query": "is:issue is:open"},
        "expected_artifact_kind": "table",
        "live_env": ["GITHUB_TOKEN"],
    },
    "slack": {
        "display_name": "Slack",
        "category": "communication",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "slack_search_messages",
        "read_arguments": {"query": "from:me"},
        "expected_artifact_kind": "cards",
        "live_env": ["SLACK_BOT_TOKEN"],
    },
    "google_workspace": {
        "display_name": "Google Workspace",
        "category": "productivity",
        "auth_modes": ["oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "google_drive_search",
        "read_arguments": {"query": "modifiedTime > '2026-01-01'"},
        "expected_artifact_kind": "table",
        "live_env": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    },
    "hubspot": {
        "display_name": "HubSpot",
        "category": "crm",
        "auth_modes": ["bearer", "oauth_ac"],
        "required_secrets": ["Authorization"],
        "read_tool": "hubspot_search_contacts",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["HUBSPOT_ACCESS_TOKEN"],
    },
    "stripe": {
        "display_name": "Stripe",
        "category": "finance",
        "auth_modes": ["bearer"],
        "required_secrets": ["Authorization"],
        "read_tool": "stripe_list_customers",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["STRIPE_API_KEY"],
    },
    "datadog": {
        "display_name": "Datadog",
        "category": "observability",
        "auth_modes": ["custom_header"],
        "required_secrets": ["DD-API-KEY", "DD-APPLICATION-KEY"],
        "read_tool": "datadog_list_monitors",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["DATADOG_API_KEY", "DATADOG_APP_KEY"],
    },
    "sentry": {
        "display_name": "Sentry",
        "category": "observability",
        "auth_modes": ["bearer"],
        "required_secrets": ["Authorization"],
        "read_tool": "sentry_list_issues",
        "read_arguments": {"limit": 10},
        "expected_artifact_kind": "table",
        "live_env": ["SENTRY_AUTH_TOKEN", "SENTRY_ORG"],
    },
    "aws": {
        "display_name": "AWS",
        "category": "cloud",
        "auth_modes": ["custom_header"],
        "required_secrets": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "read_tool": "aws_list_buckets",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    },
    "postgres": {
        "display_name": "Postgres",
        "category": "database",
        "auth_modes": ["connection_string"],
        "required_secrets": ["DATABASE_URL"],
        "read_tool": "postgres_list_tables",
        "read_arguments": {},
        "expected_artifact_kind": "table",
        "live_env": ["POSTGRES_MCP_URL"],
    },
}
```

- [ ] **Step 4: Verify test passes**

Run: `cd agent-verse-backend && uv run pytest tests/mcp/test_connector_certification_manifest.py -q`

Expected: `2 passed`.

---

## Task 2: Certification Harness

**Files:**
- Create: `agent-verse-backend/app/mcp/certification.py`
- Test: `agent-verse-backend/tests/mcp/test_connector_certification.py`

- [ ] **Step 1: Write failing harness tests**

```python
import pytest

from app.mcp.certification import run_static_certification, run_mocked_certification


def test_static_certification_passes_for_jira_manifest() -> None:
    result = run_static_certification("jira")

    assert result["connector"] == "jira"
    assert result["level"] == "static"
    assert result["status"] == "passed"
    assert {check["name"] for check in result["checks"]} >= {"manifest", "auth", "read_tool"}


@pytest.mark.asyncio
async def test_mocked_certification_uses_mcp_client_tool_call() -> None:
    class FakeClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            return [type("Tool", (), {"name": "jira_search_issues"})()]

        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            return type("Result", (), {"success": True, "output": {"issues": []}, "error": ""})()

    result = await run_mocked_certification(
        "jira",
        mcp_client=FakeClient(),
        server_id="jira-1",
        tenant_ctx=type("Tenant", (), {"tenant_id": "tenant-1"})(),
    )

    assert result["status"] == "passed"
    assert result["checks"][-1]["name"] == "read_call"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd agent-verse-backend && uv run pytest tests/mcp/test_connector_certification.py -q`

Expected: import error for `app.mcp.certification`.

- [ ] **Step 3: Implement harness**

```python
from __future__ import annotations

import time
from typing import Any

from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS


def _result(connector: str, level: str, status: str, checks: list[dict[str, str]], started: float) -> dict[str, Any]:
    return {
        "connector": connector,
        "level": level,
        "status": status,
        "checks": checks,
        "warnings": [],
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def run_static_certification(connector: str) -> dict[str, Any]:
    started = time.monotonic()
    target = CONNECTOR_CERTIFICATION_TARGETS[connector]
    checks = [
        {"name": "manifest", "status": "passed" if target.get("display_name") else "failed"},
        {"name": "auth", "status": "passed" if target.get("auth_modes") else "failed"},
        {"name": "read_tool", "status": "passed" if target.get("read_tool") else "failed"},
    ]
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return _result(connector, "static", status, checks, started)


async def run_mocked_certification(
    connector: str,
    *,
    mcp_client: Any,
    server_id: str,
    tenant_ctx: Any,
) -> dict[str, Any]:
    started = time.monotonic()
    target = CONNECTOR_CERTIFICATION_TARGETS[connector]
    tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
    tool_names = {str(getattr(tool, "name", "")) for tool in tools}
    checks = [
        {"name": "tool_discovery", "status": "passed" if target["read_tool"] in tool_names else "failed"}
    ]
    if checks[0]["status"] == "passed":
        result = await mcp_client.call_tool(
            server_id=server_id,
            tool_name=target["read_tool"],
            arguments=target["read_arguments"],
            tenant_ctx=tenant_ctx,
        )
        checks.append({"name": "read_call", "status": "passed" if result.success else "failed"})
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return _result(connector, "mocked", status, checks, started)
```

- [ ] **Step 4: Verify harness tests pass**

Run: `cd agent-verse-backend && uv run pytest tests/mcp/test_connector_certification.py -q`

Expected: `2 passed`.

---

## Task 3: Certification API And CLI

**Files:**
- Modify: `agent-verse-backend/app/api/connectors.py`
- Modify: `agent-verse-backend/app/cli/main.py`
- Test: `agent-verse-backend/tests/api/test_connectors_certification.py`
- Test: `agent-verse-backend/tests/cli/test_connector_certification.py`

- [ ] **Step 1: Write API tests**

```python
def test_list_certification_targets(client):
    response = client.get("/connectors/certification/targets", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert "jira" in response.json()
```

- [ ] **Step 2: Implement endpoints**

Add to `app/api/connectors.py`:

```python
@router.get("/certification/targets")
async def list_certification_targets(request: Request) -> dict[str, Any]:
    _require_tenant(request)
    from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS
    return CONNECTOR_CERTIFICATION_TARGETS
```

Add `POST /connectors/certification/run` for static level only in first implementation:

```python
@router.post("/certification/run")
async def run_connector_certification(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_tenant(request)
    from app.mcp.certification import run_static_certification
    connector = str(body.get("connector", "jira"))
    level = str(body.get("level", "static"))
    if level != "static":
        raise HTTPException(status_code=400, detail="Only static certification is available through API in this phase")
    return run_static_certification(connector)
```

- [ ] **Step 3: Add CLI static command**

Add a small branch in `app/cli/main.py` for:
`agentverse connectors certify --connector jira --level static`

The implementation should call `run_static_certification()` and print JSON.

- [ ] **Step 4: Run API/CLI tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/api/test_connectors_certification.py tests/cli/test_connector_certification.py -q
```

Expected: pass.

---

## Task 4: Worker Connector Context Hardening

**Files:**
- Modify: `agent-verse-backend/app/services/goal_queue.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py`
- Test: `agent-verse-backend/tests/services/test_goal_queue_comprehensive.py`
- Test: `agent-verse-backend/tests/scaling/test_worker_connector_context.py`

- [ ] **Step 1: Add queue payload test**

```python
def test_enqueue_goal_includes_connector_ids():
    from unittest.mock import MagicMock, patch
    from app.services.goal_queue import CeleryGoalTaskQueue

    mock_result = MagicMock(id="task-id")
    mock_task = MagicMock()
    mock_task.apply_async = MagicMock(return_value=mock_result)

    with patch("app.scaling.tasks.run_goal", mock_task):
        queue = CeleryGoalTaskQueue()
        queue.enqueue_goal(
            goal_id="goal-1",
            tenant_id="tenant-1",
            goal_text="Fetch Jira",
            priority="normal",
            dry_run=False,
            connector_ids=["jira-1"],
        )

    assert mock_task.apply_async.call_args.kwargs["kwargs"]["connector_ids"] == ["jira-1"]
```

- [ ] **Step 2: Implement connector IDs in queue protocol**

Update `GoalTaskQueue.enqueue_goal()` and `CeleryGoalTaskQueue.enqueue_goal()` signatures with:

```python
connector_ids: list[str] | None = None
```

Include in Celery kwargs:

```python
"connector_ids": connector_ids or [],
```

- [ ] **Step 3: Pass connector IDs from GoalService**

In `GoalService.submit_goal()`, before `self._task_queue.enqueue_goal(...)`, load agent connector IDs from `_get_agent_store()` and pass them to queue.

- [ ] **Step 4: Add worker context test**

Create `tests/scaling/test_worker_connector_context.py` with a test that `_run_with_signals(..., initial_context=...)` forwards `initial_context` to `AgentGraph.run()`.

- [ ] **Step 5: Implement worker context**

Update `run_goal()`:
- Accept `connector_ids`.
- Build `MCPClient` and `ToolContext` inside the same async run loop that calls `AgentGraph.run()`.
- Avoid reusing async Redis/DB clients across `_run_async()` calls.

- [ ] **Step 6: Run tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/services/test_goal_queue_comprehensive.py::TestCeleryGoalTaskQueue::test_enqueue_goal_includes_connector_ids tests/scaling/test_worker_connector_context.py -q
```

Expected: pass.

---

## Task 5: Tool Call Safety And Jira Repair

**Files:**
- Modify: `agent-verse-backend/app/agent/tool_calls.py`
- Modify: `agent-verse-backend/app/agent/tool_context.py`
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/agent/test_tool_calls.py`
- Test: `agent-verse-backend/tests/agent/test_tool_context.py`

- [ ] **Step 1: Add parser tests**

Tests:
- `server_name.tool_name` ignored.
- `server_name.jira_jql` ignored.
- `python.datetime` ignored.
- Placeholder Jira JQL repaired from goal text.

- [ ] **Step 2: Add resolver tests**

Test `jira.jira_search_issues` resolves to `PineLabs JIRA` tool.

- [ ] **Step 3: Implement parser/resolver**

Update `extract_tool_call()` to ignore placeholder tools.

Update `repair_tool_call_arguments(call, step, goal="")` to replace placeholder JQL with:

```python
"assignee = currentUser() AND created >= -26w ORDER BY created DESC"
```

when goal mentions assigned-to-me and last 6 months.

Update `ToolContext.find_tool()` to treat server alias `jira` as matching server names containing `jira`.

Update `AgentGraph._execute_step()` to call repair with `goal=state.goal`.

- [ ] **Step 4: Run tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/agent/test_tool_calls.py tests/agent/test_tool_context.py -q
```

Expected: pass.

---

## Task 6: Result Artifact Coverage For Certified Connectors

**Files:**
- Modify: `agent-verse-backend/app/services/result_artifacts.py`
- Test: `agent-verse-backend/tests/services/test_result_artifacts.py`

- [ ] **Step 1: Add connector result tests**

Add tests for:
- GitHub issue table.
- Slack message cards.
- Stripe customer table.
- Postgres table list.
- Failed tool result artifact.

- [ ] **Step 2: Implement generic connector artifact extraction**

Add mappings for known tool names:

```python
CONNECTOR_TABLE_TOOLS = {
    "jira_search_issues": "Jira issues",
    "github_search_issues": "GitHub issues",
    "stripe_list_customers": "Stripe customers",
    "postgres_list_tables": "Postgres tables",
}
```

Use existing table generation for rows when the output contains `issues`, `items`, `customers`, `tables`, or `rows`.

- [ ] **Step 3: Run tests**

Run: `cd agent-verse-backend && uv run pytest tests/services/test_result_artifacts.py -q`

Expected: pass.

---

## Task 7: Agent Civilization Safety Fixes

**Files:**
- Modify: `agent-verse-backend/app/api/civilization.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py`
- Modify: `agent-verse-backend/app/agent/graph.py`
- Modify: `agent-verse-backend/app/civilization/spawn_tool.py`
- Test: `agent-verse-backend/tests/api/test_civilization_api.py`
- Test: `agent-verse-backend/tests/e2e/test_civilization_autonomy.py`

- [ ] **Step 1: Add feature flag task test**

Test civilization Celery tasks no-op when `CIVILIZATION_ENABLED` is false.

- [ ] **Step 2: Gate Celery civilization tasks**

In `civilization_tick`, `civilization_learning_step`, and `discover_and_tick_civilizations`, return:

```python
{"status": "disabled", "reason": "civilization feature flag disabled"}
```

when settings disable civilization.

- [ ] **Step 3: Add spawn contract test**

Test `AgentGraph` civilization spawn path calls `execute_spawn_tool()` with all required fields.

- [ ] **Step 4: Patch spawn call**

Ensure the tool-call arguments include:
- `capability`
- `goal`
- `requester_agent_id`
- `depth`
- `parent_budget_usd`
- `parent_policy_ids`
- `civilization_id`

- [ ] **Step 5: Align frontend/backend control action**

Ensure backend accepts `adjust_budget`, and frontend uses `adjust_budget` instead of `set_budget`.

- [ ] **Step 6: Run tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/api/test_civilization_api.py tests/e2e/test_civilization_autonomy.py -q
```

Expected: pass.

---

## Task 8: Live Certification Smoke Test

**Files:**
- Create: `agent-verse-backend/tests/integration/test_certified_connectors_live.py`

- [ ] **Step 1: Add opt-in live Jira smoke test**

```python
import os

import pytest


@pytest.mark.integration
@pytest.mark.skipif(
    not all(os.getenv(name) for name in ["JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN"]),
    reason="Jira live credentials are not configured",
)
async def test_jira_live_smoke() -> None:
    assert os.environ["JIRA_URL"].startswith("https://")
```

- [ ] **Step 2: Extend to actual read call**

Use `httpx.AsyncClient` with Basic auth and call `/rest/api/3/myself`, then `/rest/api/3/search/jql` with a read-only query.

- [ ] **Step 3: Run skip-safe test**

Run: `cd agent-verse-backend && uv run pytest tests/integration/test_certified_connectors_live.py -q`

Expected: skipped without env; passed with env.

---

## Task 9: Documentation And Runbook

**Files:**
- Create: `docs/connectors/certification-runbook.md`
- Modify: `docs/agent-creation-guide.md`

- [ ] **Step 1: Write connector certification runbook**

Include:
- Static certification command.
- Mocked certification command.
- Live certification command.
- Required env vars for each top connector.
- Troubleshooting table for auth failure, no tools, fake tools, missing artifact, Celery stale worker.

- [ ] **Step 2: Update agent creation guide**

Add a short section:

```markdown
## Verifying Connector-Backed Agents

Before assigning a connector to an agent, run static and mocked certification. For live production use, run live certification with provider credentials.
```

- [ ] **Step 3: Run doc whitespace check**

Run: `git diff --check docs/connectors/certification-runbook.md docs/agent-creation-guide.md`

Expected: no output.

---

## Task 10: Final Verification

- [ ] **Step 1: Backend certification tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/mcp/test_connector_certification_manifest.py tests/mcp/test_connector_certification.py tests/services/test_goal_queue_comprehensive.py tests/scaling/test_worker_connector_context.py tests/agent/test_tool_calls.py tests/agent/test_tool_context.py tests/services/test_result_artifacts.py -q
```

Expected: pass.

- [ ] **Step 2: Connector/API tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/api/test_connectors*.py tests/mcp/test_mcp_client.py -q
```

Expected: pass or report pre-existing failures separately.

- [ ] **Step 3: Civilization tests**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/civilization tests/api/test_civilization_api.py tests/e2e/test_civilization_autonomy.py -q
```

Expected: pass or report remaining out-of-scope failures.

- [ ] **Step 4: Frontend tests**

Run:

```bash
cd agent-verse-frontend
npm run test -- src/features/connectors src/features/civilization src/features/goals
npm run typecheck
```

Expected: pass.

- [ ] **Step 5: Running environment Jira E2E**

Restart Celery worker, submit a Jira goal, and poll until terminal.

Expected:
- Goal status `complete`.
- `tool_call_complete` for `jira_search_issues`.
- `goal_complete` and `worker_complete` events persisted.
- `result_artifact.kind == "table"`.

---

## Self-Review

**Spec coverage:** Covers certification manifest, harness, API/CLI, worker connector path, result artifacts, Agent Civilization blockers, live smoke tests, docs, and final verification.

**Placeholder scan:** No TODO/TBD placeholders remain. Commands, files, and expected results are explicit.

**Type consistency:** Manifest target names, certification result keys, queue `connector_ids`, and artifact names are consistent across tasks.

**Scope check:** This plan implements the approved recommended scope: core production certification first. It does not attempt live certification for all 227 connectors.
