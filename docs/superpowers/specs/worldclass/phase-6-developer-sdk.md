# Phase 6: Developer SDK & Local Sandbox

**Status:** Not started  
**Priority:** High — unlocks external adoption and self-serve integrations  
**Acceptance gate:** `pytest agent-verse-sdk-python/tests/ -v` and `vitest run --project agent-verse-sdk-typescript` both green; `agentverse dev` starts API with FakeProvider in < 5s; `agentverse test tests/sample_goal_test.py` runs and reports PASS.

---

## 1. Current State

| Area | File | Current Behaviour |
|------|------|-------------------|
| Python packages | `agent-verse-backend/pyproject.toml` | No public SDK package; consumers must import internal `app.*` modules directly. |
| TypeScript client | `agent-verse-frontend/src/` | One-off `fetch()` calls scattered across features; no typed SDK. |
| Local dev bootstrap | `agent-verse-backend/app/cli/main.py` | No `dev` command; developer must wire Postgres, Redis, and LLM keys manually. |
| Testing | `agent-verse-backend/tests/` | No `AgentTestHarness`; integration tests call real HTTP endpoints. |
| FakeProvider | `agent-verse-backend/app/providers/fake.py` | Exists but is only used in internal test fixtures — not exposed to devs. |

---

## 2. Gap Description

External developers and internal teams that want to automate AgentVerse have no first-class SDK. The only path today is hand-crafting raw HTTP calls and reading source code. The `FakeProvider` exists but is hidden. There is no way to start a zero-config local dev environment or to write unit tests for agent goals without standing up Postgres and Redis.

---

## 3. Full Implementation

### 3.1 Python SDK — `agent-verse-sdk-python/`

#### 3.1.1 Directory layout

```
agent-verse-sdk-python/
├── agentverse/
│   ├── __init__.py
│   ├── client.py
│   ├── models.py
│   ├── streaming.py
│   └── exceptions.py
├── tests/
│   ├── conftest.py
│   ├── test_client.py
│   ├── test_streaming.py
│   └── test_models.py
├── pyproject.toml
└── README.md
```

#### 3.1.2 `agentverse/__init__.py`

```python
"""AgentVerse Python SDK — public surface area."""

from agentverse.client import AgentVerseClient
from agentverse.exceptions import AgentVerseError, AuthError, GoalFailedError
from agentverse.models import Agent, Connector, Goal, GoalEvent, GoalStatus

__all__ = [
    "AgentVerseClient",
    "Goal",
    "GoalEvent",
    "GoalStatus",
    "Agent",
    "Connector",
    "AgentVerseError",
    "AuthError",
    "GoalFailedError",
]
__version__ = "0.1.0"
```

#### 3.1.3 `agentverse/models.py`

```python
"""Pydantic v2 models mirroring the AgentVerse REST API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Goal(BaseModel):
    goal_id: str
    goal: str
    status: GoalStatus
    created_at: datetime
    updated_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    steps_total: int = 0
    steps_completed: int = 0
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalEvent(BaseModel):
    type: str
    goal_id: str
    ts: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class Agent(BaseModel):
    agent_id: str
    name: str
    autonomy_mode: str
    model: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class Connector(BaseModel):
    server_id: str
    name: str
    url: str
    status: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoalSubmitRequest(BaseModel):
    goal: str
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class AgentCreateRequest(BaseModel):
    name: str
    autonomy_mode: str = "supervised"
    model: str | None = None
    system_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorRegisterRequest(BaseModel):
    name: str
    url: str
    auth_token: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

#### 3.1.4 `agentverse/exceptions.py`

```python
"""Typed exceptions for the AgentVerse SDK."""

from __future__ import annotations


class AgentVerseError(Exception):
    """Base exception for all AgentVerse SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, status_code={self.status_code})"


class AuthError(AgentVerseError):
    """Raised when the API key is missing or invalid (HTTP 401/403)."""


class GoalFailedError(AgentVerseError):
    """Raised when `wait_for_goal` resolves to a FAILED status."""

    def __init__(self, goal_id: str, reason: str) -> None:
        super().__init__(f"Goal {goal_id} failed: {reason}")
        self.goal_id = goal_id
        self.reason = reason


class GoalTimeoutError(AgentVerseError):
    """Raised when `wait_for_goal` exceeds the specified timeout."""

    def __init__(self, goal_id: str, timeout: float) -> None:
        super().__init__(f"Goal {goal_id} did not complete within {timeout}s")
        self.goal_id = goal_id
        self.timeout = timeout


class RateLimitError(AgentVerseError):
    """Raised on HTTP 429."""


class NotFoundError(AgentVerseError):
    """Raised on HTTP 404."""
```

#### 3.1.5 `agentverse/streaming.py`

```python
"""Async SSE (Server-Sent Events) streaming client using httpx."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from agentverse.models import GoalEvent


async def stream_sse(
    url: str,
    headers: dict[str, str],
    timeout: float | None = None,
) -> AsyncIterator[GoalEvent]:
    """Yield GoalEvent objects from an SSE endpoint.

    Args:
        url: Full SSE endpoint URL.
        headers: HTTP headers (must include X-API-Key).
        timeout: Optional overall wall-clock timeout in seconds.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                payload_str = raw_line[6:].strip()
                if not payload_str or payload_str == "[DONE]":
                    break
                try:
                    payload: dict[str, Any] = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                yield GoalEvent(
                    type=payload.get("type", "unknown"),
                    goal_id=payload.get("goal_id", ""),
                    ts=datetime.now(UTC),
                    data=payload,
                )
```

#### 3.1.6 `agentverse/client.py`

```python
"""AgentVerseClient — main async client for the AgentVerse API."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agentverse.exceptions import (
    AgentVerseError,
    AuthError,
    GoalFailedError,
    GoalTimeoutError,
    NotFoundError,
    RateLimitError,
)
from agentverse.models import (
    Agent,
    AgentCreateRequest,
    Connector,
    ConnectorRegisterRequest,
    Goal,
    GoalEvent,
    GoalStatus,
    GoalSubmitRequest,
)
from agentverse.streaming import stream_sse

_TERMINAL_STATUSES = {GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED}
_POLL_INTERVAL = 2.0  # seconds between status polls


class AgentVerseClient:
    """Async client for the AgentVerse REST API.

    Usage::

        async with AgentVerseClient(api_key="av-...") as client:
            goal = await client.submit_goal("Summarise all open GitHub issues")
            result = await client.wait_for_goal(goal.goal_id, timeout=120)
            print(result.result)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise AuthError("api_key must not be empty.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AgentVerseClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._default_headers(),
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "AgentVerseClient must be used as an async context manager "
                "or _http must be set manually."
            )
        return self._http

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthError("Invalid or missing API key.", status_code=401)
        if response.status_code == 403:
            raise AuthError("Insufficient permissions.", status_code=403)
        if response.status_code == 404:
            raise NotFoundError(response.text, status_code=404)
        if response.status_code == 429:
            raise RateLimitError("Rate limit exceeded.", status_code=429)
        if response.status_code >= 400:
            raise AgentVerseError(response.text, status_code=response.status_code)

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    async def submit_goal(
        self,
        goal: str,
        priority: str = "normal",
        dry_run: bool = False,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Goal:
        """Submit a new goal for autonomous execution.

        Returns:
            A :class:`Goal` with status ``pending`` or ``planning``.
        """
        payload = GoalSubmitRequest(
            goal=goal,
            priority=priority,
            dry_run=dry_run,
            agent_id=agent_id,
            context=context or {},
        )
        resp = await self._client().post("/goals", content=payload.model_dump_json())
        self._raise_for_status(resp)
        return Goal.model_validate(resp.json())

    async def get_goal(self, goal_id: str) -> Goal:
        """Fetch current state of a goal by ID."""
        resp = await self._client().get(f"/goals/{goal_id}")
        self._raise_for_status(resp)
        return Goal.model_validate(resp.json())

    async def wait_for_goal(self, goal_id: str, timeout: float = 300.0) -> Goal:
        """Poll until goal reaches a terminal state (completed/failed/cancelled).

        Raises:
            GoalTimeoutError: if ``timeout`` seconds elapse before completion.
            GoalFailedError: if the goal reaches ``failed`` status.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            goal = await self.get_goal(goal_id)
            if goal.status in _TERMINAL_STATUSES:
                if goal.status == GoalStatus.FAILED:
                    raise GoalFailedError(goal_id, goal.error or "unknown error")
                return goal
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise GoalTimeoutError(goal_id, timeout)
            await asyncio.sleep(min(_POLL_INTERVAL, remaining))

    async def cancel_goal(self, goal_id: str) -> Goal:
        """Cancel a running goal."""
        resp = await self._client().post(f"/goals/{goal_id}/cancel", content="{}")
        self._raise_for_status(resp)
        return Goal.model_validate(resp.json())

    async def list_goals(
        self,
        status: GoalStatus | None = None,
        limit: int = 50,
    ) -> list[Goal]:
        """List recent goals, optionally filtered by status."""
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status.value
        resp = await self._client().get("/goals", params=params)
        self._raise_for_status(resp)
        data = resp.json()
        items = data.get("goals") or data if isinstance(data, list) else []
        return [Goal.model_validate(g) for g in items]

    def stream_goal(self, goal_id: str, timeout: float | None = None) -> AsyncIterator[GoalEvent]:
        """Stream SSE events for a goal as an async iterator.

        Usage::

            async for event in client.stream_goal(goal_id):
                print(event.type, event.data)
        """
        url = f"{self._base_url}/goals/{goal_id}/stream"
        return stream_sse(url, self._default_headers(), timeout=timeout)

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    async def create_agent(
        self,
        name: str,
        autonomy_mode: str = "supervised",
        model: str | None = None,
        system_prompt: str | None = None,
        **metadata: Any,
    ) -> Agent:
        """Create a new agent configuration."""
        payload = AgentCreateRequest(
            name=name,
            autonomy_mode=autonomy_mode,
            model=model,
            system_prompt=system_prompt,
            metadata=metadata,
        )
        resp = await self._client().post("/agents", content=payload.model_dump_json())
        self._raise_for_status(resp)
        return Agent.model_validate(resp.json())

    async def get_agent(self, agent_id: str) -> Agent:
        """Fetch an agent by ID."""
        resp = await self._client().get(f"/agents/{agent_id}")
        self._raise_for_status(resp)
        return Agent.model_validate(resp.json())

    async def list_agents(self) -> list[Agent]:
        """List all agents for the current tenant."""
        resp = await self._client().get("/agents")
        self._raise_for_status(resp)
        data = resp.json()
        items = data if isinstance(data, list) else data.get("agents", [])
        return [Agent.model_validate(a) for a in items]

    async def delete_agent(self, agent_id: str) -> None:
        """Delete an agent."""
        resp = await self._client().delete(f"/agents/{agent_id}")
        self._raise_for_status(resp)

    # ------------------------------------------------------------------
    # Connectors
    # ------------------------------------------------------------------

    async def register_connector(
        self,
        name: str,
        url: str,
        auth_token: str | None = None,
        **metadata: Any,
    ) -> Connector:
        """Register an MCP connector."""
        payload = ConnectorRegisterRequest(
            name=name,
            url=url,
            auth_token=auth_token,
            metadata=metadata,
        )
        resp = await self._client().post("/connectors", content=payload.model_dump_json())
        self._raise_for_status(resp)
        return Connector.model_validate(resp.json())

    async def list_connectors(self) -> list[Connector]:
        """List registered connectors."""
        resp = await self._client().get("/connectors")
        self._raise_for_status(resp)
        data = resp.json()
        items = data if isinstance(data, list) else data.get("connectors", [])
        return [Connector.model_validate(c) for c in items]

    async def delete_connector(self, server_id: str) -> None:
        """Delete / deregister a connector."""
        resp = await self._client().delete(f"/connectors/{server_id}")
        self._raise_for_status(resp)
```

#### 3.1.7 `pyproject.toml` (SDK package)

```toml
[project]
name = "agentverse-sdk"
version = "0.1.0"
description = "Official Python SDK for the AgentVerse autonomous agent platform"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
authors = [{ name = "AgentVerse", email = "sdk@agentverse.ai" }]
keywords = ["agents", "ai", "automation", "agentverse"]
classifiers = [
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: Apache Software License",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Typing :: Typed",
]

dependencies = [
    "httpx>=0.28.0",
    "pydantic>=2.9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "respx>=0.22.0",
    "pytest-cov>=6.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.hatch.build.targets.wheel]
packages = ["agentverse"]
```

#### 3.1.8 `tests/conftest.py`

```python
"""Shared fixtures for SDK unit tests using RESPX to mock HTTP."""
from __future__ import annotations

import pytest
import respx
from agentverse.client import AgentVerseClient

BASE_URL = "http://localhost:8000"
API_KEY = "test-key-123"


@pytest.fixture
def mock_http():
    """Activates a RESPX mock router scoped to the test."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(mock_http):
    """Yields a live AgentVerseClient backed by RESPX mocks."""
    async with AgentVerseClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c
```

#### 3.1.9 `tests/test_client.py`

```python
"""Unit tests for AgentVerseClient."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from agentverse.client import AgentVerseClient
from agentverse.exceptions import AuthError, GoalFailedError, GoalTimeoutError, NotFoundError
from agentverse.models import GoalStatus

BASE_URL = "http://localhost:8000"
API_KEY = "test-key"

_GOAL_PAYLOAD = {
    "goal_id": "goal-abc",
    "goal": "Run a report",
    "status": "pending",
    "created_at": datetime.now(UTC).isoformat(),
    "steps_total": 0,
    "steps_completed": 0,
    "cost_usd": 0.0,
}

_AGENT_PAYLOAD = {
    "agent_id": "agent-xyz",
    "name": "ReportBot",
    "autonomy_mode": "supervised",
    "created_at": datetime.now(UTC).isoformat(),
}

_CONNECTOR_PAYLOAD = {
    "server_id": "conn-1",
    "name": "Jira",
    "url": "http://jira-mcp:8080",
    "status": "active",
    "created_at": datetime.now(UTC).isoformat(),
}


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(mock_router):
    async with AgentVerseClient(api_key=API_KEY, base_url=BASE_URL) as c:
        yield c


# ---- Instantiation ----

def test_empty_api_key_raises():
    with pytest.raises(AuthError):
        AgentVerseClient(api_key="")


# ---- Goals ----

async def test_submit_goal(client, mock_router):
    mock_router.post("/goals").mock(return_value=httpx.Response(200, json=_GOAL_PAYLOAD))
    goal = await client.submit_goal("Run a report")
    assert goal.goal_id == "goal-abc"
    assert goal.status == GoalStatus.PENDING


async def test_submit_goal_401_raises_auth_error(client, mock_router):
    mock_router.post("/goals").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await client.submit_goal("Test")


async def test_get_goal(client, mock_router):
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=_GOAL_PAYLOAD))
    goal = await client.get_goal("goal-abc")
    assert goal.goal_id == "goal-abc"


async def test_get_goal_404_raises(client, mock_router):
    mock_router.get("/goals/missing").mock(return_value=httpx.Response(404, text="not found"))
    with pytest.raises(NotFoundError):
        await client.get_goal("missing")


async def test_wait_for_goal_completes(client, mock_router):
    completed = {**_GOAL_PAYLOAD, "status": "completed", "result": "Done!"}
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=completed))
    goal = await client.wait_for_goal("goal-abc", timeout=10.0)
    assert goal.status == GoalStatus.COMPLETED
    assert goal.result == "Done!"


async def test_wait_for_goal_failed_raises(client, mock_router):
    failed = {**_GOAL_PAYLOAD, "status": "failed", "error": "LLM quota exceeded"}
    mock_router.get("/goals/goal-abc").mock(return_value=httpx.Response(200, json=failed))
    with pytest.raises(GoalFailedError) as exc_info:
        await client.wait_for_goal("goal-abc", timeout=10.0)
    assert "quota exceeded" in str(exc_info.value)


async def test_cancel_goal(client, mock_router):
    cancelled = {**_GOAL_PAYLOAD, "status": "cancelled"}
    mock_router.post("/goals/goal-abc/cancel").mock(return_value=httpx.Response(200, json=cancelled))
    goal = await client.cancel_goal("goal-abc")
    assert goal.status == GoalStatus.CANCELLED


async def test_list_goals(client, mock_router):
    mock_router.get("/goals").mock(return_value=httpx.Response(200, json={"goals": [_GOAL_PAYLOAD]}))
    goals = await client.list_goals()
    assert len(goals) == 1
    assert goals[0].goal_id == "goal-abc"


# ---- Agents ----

async def test_create_agent(client, mock_router):
    mock_router.post("/agents").mock(return_value=httpx.Response(200, json=_AGENT_PAYLOAD))
    agent = await client.create_agent("ReportBot")
    assert agent.name == "ReportBot"


async def test_list_agents(client, mock_router):
    mock_router.get("/agents").mock(return_value=httpx.Response(200, json=[_AGENT_PAYLOAD]))
    agents = await client.list_agents()
    assert len(agents) == 1


async def test_delete_agent(client, mock_router):
    mock_router.delete("/agents/agent-xyz").mock(return_value=httpx.Response(204))
    await client.delete_agent("agent-xyz")  # should not raise


# ---- Connectors ----

async def test_register_connector(client, mock_router):
    mock_router.post("/connectors").mock(
        return_value=httpx.Response(200, json=_CONNECTOR_PAYLOAD)
    )
    conn = await client.register_connector("Jira", "http://jira-mcp:8080")
    assert conn.server_id == "conn-1"


async def test_list_connectors(client, mock_router):
    mock_router.get("/connectors").mock(
        return_value=httpx.Response(200, json=[_CONNECTOR_PAYLOAD])
    )
    conns = await client.list_connectors()
    assert len(conns) == 1
    assert conns[0].name == "Jira"
```

#### 3.1.10 `tests/test_streaming.py`

```python
"""Tests for SSE streaming client."""
from __future__ import annotations

import pytest
import respx
import httpx

from agentverse.streaming import stream_sse

BASE_URL = "http://localhost:8000"
HEADERS = {"X-API-Key": "test-key"}


async def test_stream_sse_yields_events():
    sse_body = (
        "data: {\"type\": \"goal_started\", \"goal_id\": \"g1\", \"goal\": \"Test\"}\n\n"
        "data: {\"type\": \"goal_complete\", \"goal_id\": \"g1\"}\n\n"
        "data: [DONE]\n\n"
    )
    with respx.mock():
        respx.get(f"{BASE_URL}/goals/g1/stream").mock(
            return_value=httpx.Response(200, text=sse_body)
        )
        events = []
        async for evt in stream_sse(f"{BASE_URL}/goals/g1/stream", HEADERS):
            events.append(evt)
    assert len(events) == 2
    assert events[0].type == "goal_started"
    assert events[1].type == "goal_complete"


async def test_stream_sse_skips_malformed_json():
    sse_body = (
        "data: not-json\n\n"
        "data: {\"type\": \"ok\", \"goal_id\": \"g2\"}\n\n"
        "data: [DONE]\n\n"
    )
    with respx.mock():
        respx.get(f"{BASE_URL}/goals/g2/stream").mock(
            return_value=httpx.Response(200, text=sse_body)
        )
        events = []
        async for evt in stream_sse(f"{BASE_URL}/goals/g2/stream", HEADERS):
            events.append(evt)
    assert len(events) == 1
    assert events[0].type == "ok"
```

---

### 3.2 TypeScript SDK — `agent-verse-sdk-typescript/`

#### 3.2.1 Directory layout

```
agent-verse-sdk-typescript/
├── src/
│   ├── index.ts
│   ├── client.ts
│   ├── types.ts
│   ├── streaming.ts
│   └── errors.ts
├── tests/
│   ├── client.test.ts
│   └── streaming.test.ts
├── package.json
├── tsconfig.json
└── vitest.config.ts
```

#### 3.2.2 `src/types.ts`

```typescript
// TypeScript interfaces mirroring the AgentVerse REST API schemas.

export type GoalStatus =
  | "pending"
  | "planning"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled";

export interface Goal {
  goal_id: string;
  goal: string;
  status: GoalStatus;
  created_at: string;
  updated_at?: string;
  result?: string;
  error?: string;
  steps_total: number;
  steps_completed: number;
  cost_usd: number;
  metadata: Record<string, unknown>;
}

export interface GoalEvent {
  type: string;
  goal_id: string;
  ts: string;
  data: Record<string, unknown>;
}

export interface Agent {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  model?: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface Connector {
  server_id: string;
  name: string;
  url: string;
  status: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface GoalSubmitOptions {
  priority?: "low" | "normal" | "high";
  dryRun?: boolean;
  agentId?: string;
  context?: Record<string, unknown>;
}

export interface AgentCreateOptions {
  autonomyMode?: string;
  model?: string;
  systemPrompt?: string;
  metadata?: Record<string, unknown>;
}

export interface ConnectorRegisterOptions {
  authToken?: string;
  metadata?: Record<string, unknown>;
}

export interface ListGoalsOptions {
  status?: GoalStatus;
  limit?: number;
}
```

#### 3.2.3 `src/errors.ts`

```typescript
// Typed error classes for the AgentVerse TypeScript SDK.

export class AgentVerseError extends Error {
  readonly statusCode?: number;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = "AgentVerseError";
    this.statusCode = statusCode;
  }
}

export class AuthError extends AgentVerseError {
  constructor(message = "Invalid or missing API key.") {
    super(message, 401);
    this.name = "AuthError";
  }
}

export class GoalFailedError extends AgentVerseError {
  readonly goalId: string;
  readonly reason: string;

  constructor(goalId: string, reason: string) {
    super(`Goal ${goalId} failed: ${reason}`);
    this.name = "GoalFailedError";
    this.goalId = goalId;
    this.reason = reason;
  }
}

export class GoalTimeoutError extends AgentVerseError {
  readonly goalId: string;
  readonly timeout: number;

  constructor(goalId: string, timeout: number) {
    super(`Goal ${goalId} did not complete within ${timeout}s`);
    this.name = "GoalTimeoutError";
    this.goalId = goalId;
    this.timeout = timeout;
  }
}

export class RateLimitError extends AgentVerseError {
  constructor() {
    super("Rate limit exceeded.", 429);
    this.name = "RateLimitError";
  }
}

export class NotFoundError extends AgentVerseError {
  constructor(message = "Resource not found.") {
    super(message, 404);
    this.name = "NotFoundError";
  }
}
```

#### 3.2.4 `src/streaming.ts`

```typescript
// Fetch-based SSE streaming for AgentVerse goal events.

import type { GoalEvent } from "./types.js";

export async function* streamGoalEvents(
  url: string,
  headers: Record<string, string>,
  signal?: AbortSignal
): AsyncGenerator<GoalEvent> {
  const response = await fetch(url, { headers, signal });

  if (!response.ok) {
    throw new Error(`SSE request failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("Response body is null");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (!payload || payload === "[DONE]") return;
        try {
          const parsed = JSON.parse(payload) as Record<string, unknown>;
          yield {
            type: (parsed.type as string) ?? "unknown",
            goal_id: (parsed.goal_id as string) ?? "",
            ts: new Date().toISOString(),
            data: parsed,
          };
        } catch {
          // skip malformed lines
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
```

#### 3.2.5 `src/client.ts`

```typescript
// AgentVerseClient — main TypeScript client for the AgentVerse API.

import {
  AgentVerseError,
  AuthError,
  GoalFailedError,
  GoalTimeoutError,
  NotFoundError,
  RateLimitError,
} from "./errors.js";
import { streamGoalEvents } from "./streaming.js";
import type {
  Agent,
  AgentCreateOptions,
  Connector,
  ConnectorRegisterOptions,
  Goal,
  GoalEvent,
  GoalSubmitOptions,
  ListGoalsOptions,
} from "./types.js";

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);
const POLL_INTERVAL_MS = 2000;

export class AgentVerseClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;

  constructor(
    apiKey: string,
    baseUrl: string = "http://localhost:8000"
  ) {
    if (!apiKey) throw new AuthError("apiKey must not be empty.");
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  // ── Internal helpers ────────────────────────────────────────────────

  private defaultHeaders(): Record<string, string> {
    return {
      "X-API-Key": this.apiKey,
      "Content-Type": "application/json",
      Accept: "application/json",
    };
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: this.defaultHeaders(),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (response.status === 401 || response.status === 403) {
      throw new AuthError();
    }
    if (response.status === 404) {
      throw new NotFoundError(await response.text());
    }
    if (response.status === 429) {
      throw new RateLimitError();
    }
    if (!response.ok) {
      throw new AgentVerseError(await response.text(), response.status);
    }

    const text = await response.text();
    return text ? (JSON.parse(text) as T) : ({} as T);
  }

  // ── Goals ───────────────────────────────────────────────────────────

  async submitGoal(goal: string, options: GoalSubmitOptions = {}): Promise<Goal> {
    return this.request<Goal>("POST", "/goals", {
      goal,
      priority: options.priority ?? "normal",
      dry_run: options.dryRun ?? false,
      agent_id: options.agentId,
      context: options.context ?? {},
    });
  }

  async getGoal(goalId: string): Promise<Goal> {
    return this.request<Goal>("GET", `/goals/${goalId}`);
  }

  async waitForGoal(goalId: string, timeoutSeconds = 300): Promise<Goal> {
    const deadline = Date.now() + timeoutSeconds * 1000;

    while (true) {
      const goal = await this.getGoal(goalId);

      if (TERMINAL_STATUSES.has(goal.status)) {
        if (goal.status === "failed") {
          throw new GoalFailedError(goalId, goal.error ?? "unknown error");
        }
        return goal;
      }

      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new GoalTimeoutError(goalId, timeoutSeconds);

      await new Promise((r) => setTimeout(r, Math.min(POLL_INTERVAL_MS, remaining)));
    }
  }

  async cancelGoal(goalId: string): Promise<Goal> {
    return this.request<Goal>("POST", `/goals/${goalId}/cancel`, {});
  }

  async listGoals(options: ListGoalsOptions = {}): Promise<Goal[]> {
    const params = new URLSearchParams();
    if (options.status) params.set("status", options.status);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.toString() ? `?${params}` : "";
    const data = await this.request<{ goals?: Goal[] } | Goal[]>(
      "GET",
      `/goals${query}`
    );
    return Array.isArray(data) ? data : (data as { goals?: Goal[] }).goals ?? [];
  }

  streamGoal(goalId: string, signal?: AbortSignal): AsyncGenerator<GoalEvent> {
    return streamGoalEvents(
      `${this.baseUrl}/goals/${goalId}/stream`,
      this.defaultHeaders(),
      signal
    );
  }

  // ── Agents ──────────────────────────────────────────────────────────

  async createAgent(name: string, options: AgentCreateOptions = {}): Promise<Agent> {
    return this.request<Agent>("POST", "/agents", {
      name,
      autonomy_mode: options.autonomyMode ?? "supervised",
      model: options.model,
      system_prompt: options.systemPrompt,
      metadata: options.metadata ?? {},
    });
  }

  async getAgent(agentId: string): Promise<Agent> {
    return this.request<Agent>("GET", `/agents/${agentId}`);
  }

  async listAgents(): Promise<Agent[]> {
    const data = await this.request<Agent[] | { agents?: Agent[] }>("GET", "/agents");
    return Array.isArray(data) ? data : (data as { agents?: Agent[] }).agents ?? [];
  }

  async deleteAgent(agentId: string): Promise<void> {
    await this.request<void>("DELETE", `/agents/${agentId}`);
  }

  // ── Connectors ──────────────────────────────────────────────────────

  async registerConnector(
    name: string,
    url: string,
    options: ConnectorRegisterOptions = {}
  ): Promise<Connector> {
    return this.request<Connector>("POST", "/connectors", {
      name,
      url,
      auth_token: options.authToken,
      metadata: options.metadata ?? {},
    });
  }

  async listConnectors(): Promise<Connector[]> {
    const data = await this.request<Connector[] | { connectors?: Connector[] }>(
      "GET",
      "/connectors"
    );
    return Array.isArray(data)
      ? data
      : (data as { connectors?: Connector[] }).connectors ?? [];
  }

  async deleteConnector(serverId: string): Promise<void> {
    await this.request<void>("DELETE", `/connectors/${serverId}`);
  }
}
```

#### 3.2.6 `src/index.ts`

```typescript
export { AgentVerseClient } from "./client.js";
export * from "./types.js";
export * from "./errors.js";
```

#### 3.2.7 `package.json`

```json
{
  "name": "@agentverse/sdk",
  "version": "0.1.0",
  "description": "Official TypeScript SDK for the AgentVerse autonomous agent platform",
  "type": "module",
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": {
    ".": {
      "import": "./dist/index.js",
      "types": "./dist/index.d.ts"
    }
  },
  "scripts": {
    "build": "tsc --project tsconfig.json",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "tsc --noEmit"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "vitest": "^1.6.0",
    "@types/node": "^20.0.0"
  },
  "files": ["dist"],
  "keywords": ["agentverse", "ai", "agents", "sdk"],
  "license": "Apache-2.0"
}
```

#### 3.2.8 `tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "lib": ["ES2022"],
    "outDir": "dist",
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "skipLibCheck": true
  },
  "include": ["src"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

#### 3.2.9 `vitest.config.ts`

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json"],
    },
  },
});
```

#### 3.2.10 `tests/client.test.ts`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AgentVerseClient } from "../src/client.js";
import { AuthError, GoalFailedError, GoalTimeoutError, NotFoundError } from "../src/errors.js";

const BASE_URL = "http://localhost:8000";
const GOAL = {
  goal_id: "g1",
  goal: "Test",
  status: "pending" as const,
  created_at: new Date().toISOString(),
  steps_total: 0,
  steps_completed: 0,
  cost_usd: 0,
  metadata: {},
};

function mockFetch(response: object, status = 200) {
  global.fetch = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(response),
    body: null,
  });
}

describe("AgentVerseClient", () => {
  it("throws AuthError for empty API key", () => {
    expect(() => new AgentVerseClient("")).toThrow(AuthError);
  });

  describe("submitGoal", () => {
    it("returns a Goal on success", async () => {
      mockFetch(GOAL);
      const client = new AgentVerseClient("key", BASE_URL);
      const goal = await client.submitGoal("Test");
      expect(goal.goal_id).toBe("g1");
    });

    it("throws AuthError on 401", async () => {
      mockFetch({}, 401);
      const client = new AgentVerseClient("key", BASE_URL);
      await expect(client.submitGoal("Test")).rejects.toThrow(AuthError);
    });
  });

  describe("waitForGoal", () => {
    it("resolves immediately when goal is already completed", async () => {
      const completed = { ...GOAL, status: "completed" as const, result: "ok" };
      mockFetch(completed);
      const client = new AgentVerseClient("key", BASE_URL);
      const result = await client.waitForGoal("g1", 10);
      expect(result.status).toBe("completed");
    });

    it("throws GoalFailedError when goal fails", async () => {
      const failed = { ...GOAL, status: "failed" as const, error: "LLM error" };
      mockFetch(failed);
      const client = new AgentVerseClient("key", BASE_URL);
      await expect(client.waitForGoal("g1", 10)).rejects.toThrow(GoalFailedError);
    });

    it("throws GoalTimeoutError when timeout expires", async () => {
      const running = { ...GOAL, status: "running" as const };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(running),
        body: null,
      });
      const client = new AgentVerseClient("key", BASE_URL);
      // timeout = 0 means immediate timeout after first poll
      await expect(client.waitForGoal("g1", 0)).rejects.toThrow(GoalTimeoutError);
    });
  });

  describe("getGoal", () => {
    it("throws NotFoundError on 404", async () => {
      mockFetch({ detail: "not found" }, 404);
      const client = new AgentVerseClient("key", BASE_URL);
      await expect(client.getGoal("missing")).rejects.toThrow(NotFoundError);
    });
  });

  describe("listGoals", () => {
    it("returns array from goals wrapper", async () => {
      mockFetch({ goals: [GOAL] });
      const client = new AgentVerseClient("key", BASE_URL);
      const goals = await client.listGoals();
      expect(goals).toHaveLength(1);
    });

    it("returns array directly", async () => {
      mockFetch([GOAL]);
      const client = new AgentVerseClient("key", BASE_URL);
      const goals = await client.listGoals();
      expect(goals).toHaveLength(1);
    });
  });

  describe("createAgent", () => {
    it("returns an Agent", async () => {
      const agent = {
        agent_id: "a1",
        name: "Bot",
        autonomy_mode: "supervised",
        created_at: new Date().toISOString(),
        metadata: {},
      };
      mockFetch(agent);
      const client = new AgentVerseClient("key", BASE_URL);
      const result = await client.createAgent("Bot");
      expect(result.agent_id).toBe("a1");
    });
  });

  describe("registerConnector", () => {
    it("returns a Connector", async () => {
      const conn = {
        server_id: "c1",
        name: "Jira",
        url: "http://jira:8080",
        status: "active",
        created_at: new Date().toISOString(),
        metadata: {},
      };
      mockFetch(conn);
      const client = new AgentVerseClient("key", BASE_URL);
      const result = await client.registerConnector("Jira", "http://jira:8080");
      expect(result.server_id).toBe("c1");
    });
  });
});
```

---

### 3.3 Local Development Sandbox — `agentverse dev` CLI command

**File:** `agent-verse-backend/app/cli/main.py` (add after existing commands)

```python
@app.command(name="dev")
def dev_server(
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Enable hot-reload"),
) -> None:
    """Start a zero-config local dev server with FakeProvider (no API keys needed).

    Uses:
      - FakeProvider for LLM (deterministic, free)
      - SQLite at /tmp/agentverse-dev.db
      - In-memory Redis stub via fakeredis
      - Auto hot-reload on code changes
    """
    import subprocess
    import sys

    typer.echo(typer.style("\n  AgentVerse Dev Server", bold=True, fg=typer.colors.CYAN))
    typer.echo(typer.style("  ─────────────────────────────────────────", fg=typer.colors.BRIGHT_BLACK))
    typer.echo(f"  URL:       http://localhost:{port}")
    typer.echo("  LLM:       FakeProvider (no API key required)")
    typer.echo("  Database:  SQLite  /tmp/agentverse-dev.db")
    typer.echo("  Cache:     fakeredis (in-memory)")
    typer.echo(f"  Reload:    {'enabled' if reload else 'disabled'}")
    typer.echo(typer.style("  ─────────────────────────────────────────\n", fg=typer.colors.BRIGHT_BLACK))

    env = {
        **os.environ,
        "ENVIRONMENT": "development",
        "DATABASE_URL": "sqlite+aiosqlite:////tmp/agentverse-dev.db",
        "REDIS_URL": "fakeredis://",
        "DEFAULT_LLM_PROVIDER": "fake",
        "DEBUG": "true",
    }

    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:create_app",
        "--factory",
        f"--port={port}",
        "--log-level=debug",
    ]
    if reload:
        cmd.append("--reload")

    try:
        subprocess.run(cmd, env=env, check=True)
    except KeyboardInterrupt:
        typer.echo("\n  Dev server stopped.")
    except subprocess.CalledProcessError as exc:
        typer.echo(typer.style(f"\n  Server exited with code {exc.returncode}", fg=typer.colors.RED), err=True)
        raise typer.Exit(exc.returncode)
```

**Required addition to `pyproject.toml` optional deps:**

```toml
[project.optional-dependencies]
dev-server = [
    "fakeredis>=2.26.0",
    "aiosqlite>=0.20.0",
]
```

---

### 3.4 Agent Testing Framework — `app/testing/`

#### 3.4.1 `app/testing/__init__.py`

```python
"""AgentVerse Testing Framework — isolated test harness for goal execution."""

from app.testing.harness import AgentTestHarness, TestResult

__all__ = ["AgentTestHarness", "TestResult"]
```

#### 3.4.2 `app/testing/harness.py`

```python
"""AgentTestHarness — run goals with mocked tools for unit-testing agent behaviour."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.agent.state import GoalStatus
from app.providers.fake import FakeProvider
from app.services.goal_service import GoalService
from app.governance.hitl import HITLGateway
from app.governance.audit import AuditLog
from app.agent.tool_context import ToolContext, ToolRef


@dataclass
class TestResult:
    """Outcome of a `AgentTestHarness.run_goal()` call."""

    goal_id: str
    status: GoalStatus
    result: str | None
    error: str | None
    events: list[dict[str, Any]]
    tool_calls: dict[str, list[dict[str, Any]]]  # tool_name -> list of call payloads
    plan_steps: list[str]
    cost_usd: float

    @property
    def succeeded(self) -> bool:
        return self.status == GoalStatus.COMPLETED

    @property
    def failed(self) -> bool:
        return self.status == GoalStatus.FAILED


class AgentTestHarness:
    """Synchronous test harness that runs goals with optional tool mocks.

    Usage::

        async def test_summarise():
            harness = AgentTestHarness()
            harness.mock_tool("read_file", returns={"content": "Hello world"})
            result = await harness.run_goal("Summarise the README")
            harness.assert_goal_completed()
            harness.assert_tool_called("read_file", times=1)
            harness.assert_output_contains("Hello")
    """

    def __init__(self) -> None:
        self._mock_tools: dict[str, Any] = {}
        self._last_result: TestResult | None = None

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def mock_tool(self, tool_name: str, returns: Any) -> "AgentTestHarness":
        """Register a mock return value for a tool by name."""
        self._mock_tools[tool_name] = returns
        return self

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run_goal(
        self,
        goal: str,
        mock_tools: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> TestResult:
        """Run ``goal`` inside a lightweight isolated environment.

        Args:
            goal: Natural-language goal string.
            mock_tools: Optional per-call tool mocks (merged with pre-registered mocks).
            timeout: Wall-clock timeout in seconds.

        Returns:
            :class:`TestResult` with full event trace and tool call log.
        """
        combined_mocks = {**self._mock_tools, **(mock_tools or {})}

        events: list[dict[str, Any]] = []
        tool_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)

        # Build a minimal ToolContext backed by mock tools only
        tool_refs: list[ToolRef] = [
            ToolRef(
                name=name,
                server_id="mock",
                description=f"Mock tool: {name}",
                input_schema={"type": "object"},
            )
            for name in combined_mocks
        ]

        async def mock_tool_executor(tool_name: str, arguments: dict[str, Any]) -> Any:
            tool_calls[tool_name].append(arguments)
            if tool_name in combined_mocks:
                result = combined_mocks[tool_name]
                return result() if callable(result) else result
            raise ValueError(f"No mock registered for tool '{tool_name}'")

        provider = FakeProvider()
        hitl = HITLGateway()
        audit = AuditLog()

        # Use a real GoalService with FakeProvider, intercept events
        service = GoalService(
            provider=provider,
            hitl_gateway=hitl,
            audit_log=audit,
        )
        service._tool_executor = mock_tool_executor  # type: ignore[attr-defined]

        # Build a mock ToolContext
        tool_ctx = ToolContext(
            tenant_id="test",
            tools=tool_refs,
            call_tool=mock_tool_executor,
        )

        goal_id = await service.submit_goal(
            goal=goal,
            tenant_context=_make_test_tenant(),
            tool_context=tool_ctx,
        )

        # Collect events by subscribing to the goal queue
        deadline = asyncio.get_event_loop().time() + timeout
        plan_steps: list[str] = []

        while True:
            state = service.get_goal_state(goal_id)
            if state is None:
                break
            # Drain any pending events
            async for evt in service._drain_events(goal_id):  # type: ignore[attr-defined]
                events.append(evt)
                if evt.get("type") == "plan_ready":
                    plan_steps = evt.get("steps", [])

            if state.status in (GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED):
                break
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.05)

        final_state = service.get_goal_state(goal_id)
        result = TestResult(
            goal_id=goal_id,
            status=final_state.status if final_state else GoalStatus.FAILED,
            result=final_state.result if final_state else None,
            error=final_state.error if final_state else "timeout",
            events=events,
            tool_calls=dict(tool_calls),
            plan_steps=plan_steps,
            cost_usd=final_state.cost_usd if final_state else 0.0,
        )
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # Assertion helpers
    # ------------------------------------------------------------------

    def _require_result(self) -> TestResult:
        if self._last_result is None:
            raise RuntimeError("No goal has been run yet. Call run_goal() first.")
        return self._last_result

    def assert_goal_completed(self) -> None:
        result = self._require_result()
        assert result.succeeded, (
            f"Expected goal to complete but got status={result.status!r}, "
            f"error={result.error!r}"
        )

    def assert_goal_failed(self) -> None:
        result = self._require_result()
        assert result.failed, (
            f"Expected goal to fail but got status={result.status!r}"
        )

    def assert_tool_called(self, tool: str, times: int = 1) -> None:
        result = self._require_result()
        actual = len(result.tool_calls.get(tool, []))
        assert actual == times, (
            f"Expected tool '{tool}' to be called {times} time(s), but was called {actual} time(s). "
            f"All tool calls: {list(result.tool_calls.keys())}"
        )

    def assert_tool_not_called(self, tool: str) -> None:
        self.assert_tool_called(tool, times=0)

    def assert_output_contains(self, text: str) -> None:
        result = self._require_result()
        output = (result.result or "").lower()
        assert text.lower() in output, (
            f"Expected output to contain {text!r} but got: {result.result!r}"
        )

    def assert_plan_has_step(self, step_fragment: str) -> None:
        result = self._require_result()
        matches = [s for s in result.plan_steps if step_fragment.lower() in s.lower()]
        assert matches, (
            f"Expected plan to contain step with '{step_fragment}' but plan was: {result.plan_steps}"
        )

    def get_events(self) -> list[dict[str, Any]]:
        return self._require_result().events

    def get_tool_calls(self, tool: str) -> list[dict[str, Any]]:
        return self._require_result().tool_calls.get(tool, [])


# ---------------------------------------------------------------------------
# Internal: minimal test tenant context
# ---------------------------------------------------------------------------

def _make_test_tenant():  # type: ignore[return]
    """Return a minimal TenantContext suitable for testing."""
    from app.tenancy.context import PlanTier, TenantContext
    return TenantContext(
        tenant_id="test-tenant",
        plan_tier=PlanTier.PRO,
        api_key="test-key",
        settings={},
    )
```

#### 3.4.3 `app/testing/fixtures.py`

```python
"""Common test fixtures for AgentVerse goals and connectors."""

from __future__ import annotations

from typing import Any

# ── Mock tool return values ──────────────────────────────────────────────────

MOCK_TOOLS: dict[str, Any] = {
    "read_file": {"content": "This is the file content.", "lines": 1},
    "write_file": {"success": True, "bytes_written": 42},
    "http_get": {"status_code": 200, "body": '{"ok": true}'},
    "http_post": {"status_code": 201, "body": '{"id": "new-123"}'},
    "search_web": {"results": [{"title": "Result 1", "url": "https://example.com", "snippet": "A result"}]},
    "run_sql": {"rows": [{"id": 1, "name": "Alice"}], "row_count": 1},
    "send_email": {"message_id": "msg-abc", "status": "sent"},
    "list_files": {"files": ["README.md", "main.py", "tests/test_main.py"]},
}

# ── Sample goals ─────────────────────────────────────────────────────────────

SAMPLE_GOALS = [
    "Read README.md and summarise the key points",
    "Search the web for the latest news on AI agents and write a brief report",
    "Run a SQL query to count all users and return the result",
    "Send an email to alice@example.com with subject 'Test' and body 'Hello'",
]

# ── Sample connector definitions ─────────────────────────────────────────────

SAMPLE_CONNECTORS = [
    {"name": "filesystem", "url": "http://localhost:9001", "tools": ["read_file", "write_file", "list_files"]},
    {"name": "web-search", "url": "http://localhost:9002", "tools": ["search_web"]},
    {"name": "database", "url": "http://localhost:9003", "tools": ["run_sql"]},
    {"name": "email", "url": "http://localhost:9004", "tools": ["send_email"]},
]
```

#### 3.4.4 `agentverse test` CLI command (addition to `app/cli/main.py`)

```python
@app.command(name="test")
def run_tests(
    test_file: str = typer.Argument(..., help="Path to a Python test file using AgentTestHarness"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run agent goal tests using the AgentTestHarness framework.

    Example test file::

        # my_agent_test.py
        from app.testing.harness import AgentTestHarness

        async def test_summarise_readme():
            h = AgentTestHarness()
            h.mock_tool("read_file", returns={"content": "Hello world"})
            await h.run_goal("Summarise README.md")
            h.assert_goal_completed()
            h.assert_tool_called("read_file")
    """
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "pytest", test_file, "-p", "no:warnings"]
    if verbose:
        cmd.append("-v")

    result = subprocess.run(cmd, check=False)
    raise typer.Exit(result.returncode)
```

---

## 4. pyproject.toml Changes

**File:** `agent-verse-backend/pyproject.toml`

Add to `[project.optional-dependencies]`:

```toml
dev-server = [
    "fakeredis>=2.26.0",
    "aiosqlite>=0.20.0",
]
testing = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "respx>=0.22.0",
]
```

---

## 5. Docker-Compose Changes

No changes required to the production compose. For the SDK package CI add a new service in `infra/docker-compose.ci.yml` (new file):

```yaml
name: agentverse-sdk-ci

services:
  sdk-python-tests:
    build:
      context: ../agent-verse-sdk-python
      dockerfile: Dockerfile.test
    command: pytest tests/ -v --tb=short

  sdk-typescript-tests:
    image: node:20-alpine
    working_dir: /app
    volumes:
      - ../agent-verse-sdk-typescript:/app
    command: sh -c "npm ci && npm test"
```

---

## 6. Acceptance Criteria

```bash
# Python SDK tests
cd agent-verse-sdk-python && pip install -e ".[dev]" && pytest tests/ -v

# TypeScript SDK tests
cd agent-verse-sdk-typescript && npm ci && npm test

# Dev server starts in < 5s
cd agent-verse-backend && agentverse dev --no-reload &
sleep 5 && curl -f http://localhost:8000/health && kill %1

# Agent testing framework
cd agent-verse-backend && agentverse test tests/sample_goal_test.py -v
```
