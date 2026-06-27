"""Local AgentVerse mock server for SDK testing without a real backend.

Usage:
    agentverse dev start  # starts mock server on port 8001

Or programmatically::

    from agentverse.mock_server import MockServer
    server = MockServer(port=8001)
    await server.start()
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any


class MockServer:
    """In-process mock server that accepts AgentVerse API calls and returns configurable fixtures."""

    def __init__(self, port: int = 8001) -> None:
        self.port = port
        self._goals: dict[str, dict] = {}
        self._agents: dict[str, dict] = {}
        self._api_key = f"mock_{uuid.uuid4().hex[:16]}"
        self._app = None

    def _make_app(self) -> Any:
        try:
            from fastapi import FastAPI, HTTPException
            from fastapi.responses import JSONResponse  # noqa: F401
        except ImportError:
            raise ImportError("Install fastapi+uvicorn: pip install 'agentverse[dev]'")

        app = FastAPI(title="AgentVerse Mock Server")

        @app.post("/tenants/signup")
        async def signup(body: dict) -> dict:
            return {
                "tenant_id": uuid.uuid4().hex,
                "api_key": self._api_key,
                "plan": "professional",
                "name": body.get("name", "Mock Tenant"),
            }

        @app.post("/goals")
        async def submit_goal(body: dict) -> dict:
            goal_id = uuid.uuid4().hex
            goal = {
                "goal_id": goal_id,
                "goal": body.get("goal", ""),
                "status": "complete" if body.get("dry_run") else "executing",
                "plan": {"steps": [f"Step 1: {body.get('goal', '')[:50]}"]},
                "created_at": datetime.now(UTC).isoformat(),
                "result": "",
            }
            self._goals[goal_id] = goal

            # Auto-complete after 0.5s for testing (non-dry-run goals)
            if not body.get("dry_run"):
                async def _auto_complete() -> None:
                    await asyncio.sleep(0.5)
                    if goal_id in self._goals:
                        self._goals[goal_id]["status"] = "complete"
                        self._goals[goal_id]["result"] = (
                            f"Mock: {body.get('goal', '')} completed successfully"
                        )

                asyncio.create_task(_auto_complete())

            return goal

        @app.get("/goals/{goal_id}")
        async def get_goal(goal_id: str) -> dict:
            goal = self._goals.get(goal_id)
            if not goal:
                raise HTTPException(404, "Goal not found")
            return goal

        @app.get("/goals")
        async def list_goals() -> list:
            return list(self._goals.values())

        @app.post("/agents")
        async def create_agent(body: dict) -> dict:
            agent_id = uuid.uuid4().hex
            agent = {"agent_id": agent_id, **body, "created_at": datetime.now(UTC).isoformat()}
            self._agents[agent_id] = agent
            return agent

        @app.get("/agents")
        async def list_agents() -> list:
            return list(self._agents.values())

        @app.get("/goals/{goal_id}/stream")
        async def stream_goal(goal_id: str):  # type: ignore[return]
            from fastapi.responses import StreamingResponse

            async def generate():
                events = [
                    {"type": "plan_created", "payload": {"steps": ["Step 1"]}},
                    {"type": "step_complete", "payload": {"description": "Step 1 done", "output": "OK"}},
                    {"type": "goal_complete", "payload": {"result": f"Goal {goal_id} completed successfully"}},
                ]
                for evt in events:
                    yield f"data: {json.dumps(evt)}\n\n"
                    await asyncio.sleep(0.1)

            return StreamingResponse(generate(), media_type="text/event-stream")

        return app

    async def start(self) -> Any:
        """Start the mock server as an asyncio task."""
        try:
            import uvicorn
        except ImportError:
            raise ImportError("Install uvicorn: pip install uvicorn")

        self._app = self._make_app()
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        await asyncio.sleep(0.5)  # Let server start
        print(f"Mock AgentVerse server started on http://127.0.0.1:{self.port}")
        print(f"API Key: {self._api_key}")
        return server

    def get_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def get_api_key(self) -> str:
        return self._api_key
