# AgentVerse Python SDK

Official Python SDK for the AgentVerse Agentic OS.

## Installation

```bash
pip install agentverse-sdk
```

## Quick Start

```python
import asyncio
from agentverse import AgentVerseClient

async def main():
    async with AgentVerseClient(api_key="your-key", base_url="http://localhost:8000") as client:
        # Submit a goal and wait for completion
        goal = await client.submit_goal("List all open Jira issues in project BAU")
        completed = await client.wait_for_goal(goal.goal_id, timeout=120)
        print(f"Status: {completed.status}")

        # Stream events
        async for event in client.stream_goal(goal.goal_id):
            print(f"[{event.type}] {event.data}")

asyncio.run(main())
```

## Features

- **Goals** — submit, poll, stream, cancel autonomous agent goals
- **Agents** — create, list, delete agent configurations
- **Connectors** — register and list MCP connectors
- **Streaming** — real-time SSE event streaming
- **Typed errors** — `AuthError`, `GoalFailedError`, `GoalTimeoutError`, `NotFoundError`

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
