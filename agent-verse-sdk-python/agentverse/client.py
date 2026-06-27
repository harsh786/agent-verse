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
        """Submit a new goal for autonomous execution."""
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

    async def wait_for_goal(
        self, goal_id: str, timeout: float = 300.0
    ) -> Goal:
        """Wait for a goal to complete using SSE streaming for efficiency.

        Falls back to polling via ``_wait_for_goal_polling`` when the SSE
        stream ends without a terminal event.

        Raises:
            GoalTimeoutError: if ``timeout`` seconds elapse before completion.
            GoalFailedError: if the goal reaches ``failed`` status.
        """
        try:
            async for event in self.stream_goal(goal_id, timeout=timeout):
                etype = event.type if hasattr(event, "type") else event.get("type", "")
                if etype in ("goal_complete", "goal_finished"):
                    return await self.get_goal(goal_id)
                elif etype in ("goal_failed", "goal_error"):
                    goal = await self.get_goal(goal_id)
                    reason = (
                        event.data.get("reason", "unknown")
                        if hasattr(event, "data")
                        else event.get("reason", "unknown")
                    )
                    raise GoalFailedError(goal_id, reason)
        except (TimeoutError, asyncio.TimeoutError):
            goal = await self.get_goal(goal_id)
            if goal.status in _TERMINAL_STATUSES:
                if goal.status == GoalStatus.FAILED:
                    raise GoalFailedError(goal_id, goal.error or "unknown error")
                return goal
            raise GoalTimeoutError(goal_id, timeout)
        except (GoalFailedError, GoalTimeoutError):
            raise
        except Exception:
            # SSE not available or connection error — fall back to polling
            return await self._wait_for_goal_polling(goal_id, timeout=timeout)
        # Stream ended without a terminal event — check final status
        return await self.get_goal(goal_id)

    async def _wait_for_goal_polling(
        self, goal_id: str, timeout: float = 300.0
    ) -> Goal:
        """Poll until goal reaches a terminal state (completed/failed/cancelled).

        Kept as a fallback for environments where SSE is not available.

        Raises:
            GoalTimeoutError: if ``timeout`` seconds elapse before completion.
            GoalFailedError: if the goal reaches ``failed`` status.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            goal = await self.get_goal(goal_id)
            if goal.status in _TERMINAL_STATUSES:
                if goal.status == GoalStatus.FAILED:
                    raise GoalFailedError(goal_id, goal.error or "unknown error")
                return goal
            remaining = deadline - loop.time()
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
        items = data if isinstance(data, list) else data.get("goals", [])
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
