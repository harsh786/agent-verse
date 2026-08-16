"""WorkflowClient — SDK methods for the Workflow Automation Engine API."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx


class WorkflowRun:
    """Represents a workflow run."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.run_id: str = data.get("run_id", "")
        self.workflow_id: str = data.get("workflow_id", "")
        self.status: str = data.get("status", "pending")
        self.inputs: dict[str, Any] = data.get("inputs", {})
        self.outputs: dict[str, Any] = data.get("outputs", {})
        self.error: str | None = data.get("error")
        self.cost_usd: float = float(data.get("cost_usd", 0.0))
        self._raw = data

    def __repr__(self) -> str:
        return f"WorkflowRun(run_id={self.run_id!r}, status={self.status!r})"


class WorkflowDefinition:
    """Represents a workflow definition returned by the API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data.get("id", "")
        self.name: str = data.get("name", "")
        self.status: str = data.get("status", "draft")
        self.version: str = data.get("version", "1")
        self._raw = data

    def __repr__(self) -> str:
        return f"WorkflowDefinition(id={self.id!r}, name={self.name!r}, status={self.status!r})"


_TERMINAL = {"complete", "failed", "cancelled", "timed_out"}
_POLL_INTERVAL = 2.0


class WorkflowClient:
    """Async client for the WorkflowEngine API.

    Usage::

        async with WorkflowClient(api_key="av-...") as wf:
            run = await wf.run("wf-123", inputs={"email": "test@example.com"})
            result = await wf.wait_for_run(run.run_id, timeout=120)
            print(result.outputs)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def __aenter__(self) -> "WorkflowClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": self._api_key, "Content-Type": "application/json"},
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── Workflow definitions ───────────────────────────────────────────────────

    async def create(self, name: str, definition: dict[str, Any], **kwargs: Any) -> WorkflowDefinition:
        """Create a new workflow definition."""
        payload = {"name": name, "definition": definition, **kwargs}
        data = await self._post("/api/v1/workflows", payload)
        return WorkflowDefinition(data)

    async def get(self, workflow_id: str) -> WorkflowDefinition:
        """Get a workflow definition by ID."""
        data = await self._get(f"/api/v1/workflows/{workflow_id}")
        return WorkflowDefinition(data)

    async def list(
        self,
        page: int = 1,
        per_page: int = 20,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List workflow definitions for the current tenant."""
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if status:
            params["status"] = status
        return await self._get("/api/v1/workflows", params=params)

    async def publish(self, workflow_id: str) -> WorkflowDefinition:
        """Publish a workflow definition."""
        data = await self._post(f"/api/v1/workflows/{workflow_id}/publish", {})
        return WorkflowDefinition(data)

    async def unpublish(self, workflow_id: str) -> WorkflowDefinition:
        """Unpublish a workflow definition."""
        data = await self._post(f"/api/v1/workflows/{workflow_id}/unpublish", {})
        return WorkflowDefinition(data)

    # ── Execution ─────────────────────────────────────────────────────────────

    async def run(
        self,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
        callback_url: str | None = None,
    ) -> WorkflowRun:
        """Trigger a workflow run. Returns immediately with a run_id."""
        payload: dict[str, Any] = {"inputs": inputs or {}}
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if dry_run:
            payload["dry_run"] = True
        if callback_url:
            payload["callback_url"] = callback_url
        data = await self._post(f"/api/v1/workflows/{workflow_id}/trigger", payload)
        return WorkflowRun(data)

    async def get_run(self, run_id: str) -> WorkflowRun:
        """Get a workflow run by ID."""
        data = await self._get(f"/api/v1/runs/{run_id}")
        return WorkflowRun(data)

    async def list_runs(
        self,
        workflow_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """List workflow runs."""
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if workflow_id:
            params["workflow_id"] = workflow_id
        if status:
            params["status"] = status
        return await self._get("/api/v1/runs", params=params)

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        """Cancel a running workflow."""
        return await self._post(f"/api/v1/runs/{run_id}/cancel", {})

    async def pause_run(self, run_id: str) -> dict[str, Any]:
        """Pause a running workflow."""
        return await self._post(f"/api/v1/runs/{run_id}/pause", {})

    async def resume_run(self, run_id: str) -> dict[str, Any]:
        """Resume a paused workflow."""
        return await self._post(f"/api/v1/runs/{run_id}/resume", {})

    async def wait_for_run(
        self,
        run_id: str,
        timeout: float = 300.0,
        poll_interval: float = _POLL_INTERVAL,
    ) -> WorkflowRun:
        """Poll until the run reaches a terminal status. Raises TimeoutError if exceeded."""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            run = await self.get_run(run_id)
            if run.status in _TERMINAL:
                return run
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Workflow run {run_id!r} did not complete within {timeout}s")
            await asyncio.sleep(min(poll_interval, remaining))

    async def stream_run(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Stream run events via SSE."""
        url = f"{self._base_url}/api/v1/runs/{run_id}/stream"
        headers = {"X-API-Key": self._api_key, "Accept": "text/event-stream"}
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, headers=headers, timeout=None) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            return
                        import json
                        yield json.loads(data)

    # ── HITL approvals ────────────────────────────────────────────────────────

    async def list_approvals(
        self, page: int = 1, per_page: int = 20
    ) -> dict[str, Any]:
        """List pending approval requests for the current user."""
        return await self._get("/api/v1/approvals", params={"page": page, "per_page": per_page})

    async def decide_approval(
        self, request_id: str, action: str, note: str = ""
    ) -> dict[str, Any]:
        """Submit a decision on an approval request."""
        return await self._post(
            f"/api/v1/approvals/{request_id}/decide",
            {"action": action, "note": note},
        )

    # ── Templates ─────────────────────────────────────────────────────────────

    async def list_templates(
        self, category: str | None = None, q: str | None = None
    ) -> dict[str, Any]:
        """List system workflow templates."""
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if q:
            params["q"] = q
        return await self._get("/api/v1/workflow-templates", params=params)

    async def fork_template(
        self, slug: str, name: str | None = None
    ) -> WorkflowDefinition:
        """Fork a system template into the current tenant's workspace."""
        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        data = await self._post(f"/api/v1/workflow-templates/{slug}/fork", payload)
        return WorkflowDefinition(data)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        client = self._get_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        client = self._get_client()
        resp = await client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("WorkflowClient must be used as an async context manager")
        return self._client
