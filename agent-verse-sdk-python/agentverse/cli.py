"""AgentVerse CLI — submit goals, manage agents, run evals from the terminal."""
#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import click

from agentverse.client import AgentVerseClient


def _get_client() -> AgentVerseClient:
    base_url = os.environ.get("AGENTVERSE_URL", "http://localhost:8000")
    api_key = os.environ.get("AGENTVERSE_API_KEY", "")
    if not api_key:
        click.echo("Error: AGENTVERSE_API_KEY env var required", err=True)
        sys.exit(1)
    return AgentVerseClient(base_url=base_url, api_key=api_key)


@click.group()
@click.version_option("1.0.0")
def cli() -> None:
    """AgentVerse CLI — autonomous goal execution from the terminal."""


@cli.command()
@click.argument("goal")
@click.option("--agent", "-a", default=None, help="Agent ID to use (auto-routes if omitted)")
@click.option("--wait/--no-wait", default=True, help="Wait for goal completion")
@click.option("--dry-run", is_flag=True, default=False, help="Plan only, don't execute")
@click.option("--output", "-o", default="text", type=click.Choice(["text", "json"]))
def run(goal: str, agent: str | None, wait: bool, dry_run: bool, output: str) -> None:
    """Submit a goal for autonomous execution."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            result = await client.submit_goal(
                goal=goal,
                agent_id=agent,
                dry_run=dry_run,
            )
            goal_id = getattr(result, "goal_id", "") or result.goal_id if hasattr(result, "goal_id") else ""
            if output == "json":
                data = result.model_dump() if hasattr(result, "model_dump") else result
                click.echo(json.dumps(data, indent=2, default=str))
            else:
                click.echo(f"Goal submitted: {goal_id}")

            if wait and not dry_run and goal_id:
                click.echo(f"Waiting for goal {goal_id}...")
                async for event in client.stream_goal(goal_id):
                    etype = event.type if hasattr(event, "type") else event.get("type", "")
                    if etype in ("step_complete", "tool_call_complete"):
                        payload = event.data if hasattr(event, "data") else event.get("payload", {})
                        step_desc = payload.get("description", "") if isinstance(payload, dict) else ""
                        click.echo(f"  > {step_desc}")
                    elif etype in ("goal_complete", "goal_finished"):
                        click.echo("Goal completed!")
                        break
                    elif etype in ("goal_failed", "goal_error"):
                        payload = event.data if hasattr(event, "data") else event.get("payload", {})
                        reason = payload.get("reason", "unknown") if isinstance(payload, dict) else "unknown"
                        click.echo(f"Goal failed: {reason}", err=True)
                        sys.exit(1)

    asyncio.run(_execute())


@cli.command("agents")
@click.option("--output", "-o", default="text", type=click.Choice(["text", "json"]))
def list_agents(output: str) -> None:
    """List all agents for your tenant."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            agents = await client.list_agents()
            if output == "json":
                click.echo(json.dumps([a.model_dump() for a in agents], indent=2, default=str))
            else:
                if not agents:
                    click.echo("No agents found.")
                    return
                click.echo(f"{'ID':<32} {'Name':<30} {'Mode':<20}")
                click.echo("-" * 82)
                for a in agents:
                    agent_id = getattr(a, "agent_id", "") or ""
                    name = getattr(a, "name", "") or ""
                    mode = getattr(a, "autonomy_mode", "") or ""
                    click.echo(f"{agent_id:<32} {name:<30} {mode:<20}")

    asyncio.run(_execute())


@cli.command("approve")
@click.argument("request_id")
@click.option("--note", "-n", default="", help="Approval note")
def approve(request_id: str, note: str) -> None:
    """Approve a pending HITL request."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            # HITL approval via raw HTTP if not on the client
            http = client._client()
            resp = await http.post(
                f"/hitl/{request_id}/approve",
                content=json.dumps({"note": note}),
            )
            client._raise_for_status(resp)
            click.echo(f"Approved request {request_id}")

    asyncio.run(_execute())


@cli.command("replay")
@click.argument("goal_id")
def replay(goal_id: str) -> None:
    """Replay a completed goal's execution timeline."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            http = client._client()
            resp = await http.get(f"/goals/{goal_id}/replay")
            client._raise_for_status(resp)
            timeline = resp.json()
            steps = timeline.get("steps", [])
            click.echo(f"\nGoal: {timeline.get('goal_text', '')}")
            click.echo(f"Status: {timeline.get('status', '')}")
            click.echo(f"\nExecution timeline ({len(steps)} steps):\n")
            for i, step in enumerate(steps, 1):
                status_icon = "+" if step.get("status") == "complete" else "x"
                click.echo(f"  {i}. [{status_icon}] {step.get('description', '')}")
                if step.get("output"):
                    click.echo(f"     Output: {str(step['output'])[:100]}...")

    asyncio.run(_execute())


@cli.command("goals")
@click.option("--output", "-o", default="text", type=click.Choice(["text", "json"]))
def list_goals(output: str) -> None:
    """List recent goals."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            goals = await client.list_goals()
            if output == "json":
                click.echo(json.dumps([g.model_dump() for g in goals], indent=2, default=str))
            else:
                if not goals:
                    click.echo("No goals found.")
                    return
                click.echo(f"{'ID':<32} {'Status':<15} {'Goal':<50}")
                click.echo("-" * 97)
                for g in goals[:20]:
                    goal_id = getattr(g, "goal_id", "") or ""
                    status = getattr(g, "status", "")
                    status_str = status.value if hasattr(status, "value") else str(status)
                    goal_text = (getattr(g, "goal", "") or getattr(g, "goal_text", "") or "")[:47] + "..."
                    click.echo(f"{goal_id:<32} {status_str:<15} {goal_text:<50}")

    asyncio.run(_execute())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
