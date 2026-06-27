"""AgentVerse CLI — submit goals, manage agents, run evals from the terminal."""
#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
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


# ── P2.9: New commands ─────────────────────────────────────────────────────────

@cli.group()
def connectors() -> None:
    """Manage connectors."""


@connectors.command("list")
@click.option("--output", "-o", default="text", type=click.Choice(["text", "json"]))
def list_connectors(output: str) -> None:
    """List registered connectors for your tenant."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            conns = await client.list_connectors()
            if output == "json":
                items = [c.model_dump() if hasattr(c, "model_dump") else c for c in conns]
                click.echo(json.dumps(items, indent=2, default=str))
            else:
                if not conns:
                    click.echo("No connectors registered.")
                    return
                click.echo(f"{'ID':<36} {'Name':<25} {'Status':<10}")
                click.echo("-" * 71)
                for c in conns:
                    if hasattr(c, "server_id"):
                        sid = c.server_id or ""
                        cname = c.name or ""
                        cstatus = c.status or "unknown"
                    else:
                        sid = c.get("server_id", "")
                        cname = c.get("name", "")
                        cstatus = c.get("health_status", c.get("status", "unknown"))
                    click.echo(f"{sid:<36} {cname:<25} {cstatus:<10}")

    asyncio.run(_execute())


@connectors.command("register")
@click.option("--name", required=True)
@click.option("--url", required=True)
@click.option("--type", "conn_type", default="rest")
def register_connector(name: str, url: str, conn_type: str) -> None:
    """Register a new connector."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            result = await client.register_connector(name=name, url=url, auth_type=conn_type)
            server_id = result.server_id if hasattr(result, "server_id") else result.get("server_id", "")
            click.echo(f"Connector registered: {server_id}")

    asyncio.run(_execute())


@cli.group()
def schedules() -> None:
    """Manage goal schedules."""


@schedules.command("list")
def list_schedules() -> None:
    """List all schedules."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            scheds = await client.list_schedules()
            if not scheds:
                click.echo("No schedules found.")
                return
            for s in scheds:
                sid = s.get("schedule_id", "")[:16] if isinstance(s, dict) else ""
                ttype = s.get("trigger_type", "") if isinstance(s, dict) else ""
                goal_text = s.get("goal_text", s.get("goal_template", ""))[:50] if isinstance(s, dict) else ""
                click.echo(f"  {sid} | {ttype} | {goal_text}")

    asyncio.run(_execute())


@schedules.command("create")
@click.option("--goal", required=True)
@click.option("--cron", default="")
@click.option("--interval", type=int, default=0, help="Interval in seconds")
def create_schedule(goal: str, cron: str, interval: int) -> None:
    """Create a new schedule."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            trigger_type = "cron" if cron else "interval"
            result = await client.create_schedule(
                goal=goal,
                trigger_type=trigger_type,
                cron_expression=cron,
                interval_seconds=interval,
            )
            schedule_id = result.get("schedule_id", "") if isinstance(result, dict) else ""
            click.echo(f"Schedule created: {schedule_id}")

    asyncio.run(_execute())


@cli.command("logs")
@click.argument("goal_id")
@click.option("--follow", "-f", is_flag=True, default=False)
def logs(goal_id: str, follow: bool) -> None:
    """Stream or show logs for a goal execution."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            if follow:
                click.echo(f"Streaming events for goal {goal_id}...")
                async for event in client.stream_goal(goal_id, timeout=300):
                    etype = event.type if hasattr(event, "type") else event.get("type", "")
                    payload = event.data if hasattr(event, "data") else event.get("payload", {})
                    ts = datetime.now().strftime("%H:%M:%S")
                    click.echo(f"[{ts}] {etype}: {json.dumps(payload)[:100]}")
                    if etype in ("goal_complete", "goal_failed"):
                        break
            else:
                try:
                    timeline = await client.get_goal_replay(goal_id)
                    for evt in timeline.get("timeline", []):
                        click.echo(f"  {evt.get('ts', '')[:19]} [{evt.get('type', '')}]")
                except Exception:
                    goal = await client.get_goal(goal_id)
                    data = goal.model_dump() if hasattr(goal, "model_dump") else goal
                    click.echo(json.dumps(data, indent=2, default=str))

    asyncio.run(_execute())


@cli.command("simulate")
@click.argument("goal")
@click.option("--agent-id", "-a", default=None)
def simulate(goal: str, agent_id: str | None) -> None:
    """Simulate goal execution without actually running tools."""
    async def _execute() -> None:
        client = _get_client()
        async with client:
            result = await client.submit_goal(goal=goal, agent_id=agent_id, dry_run=True)
            click.echo("Simulation complete")
            data: Any = result.model_dump() if hasattr(result, "model_dump") else result
            plan = data.get("plan", data.get("execution_context", {})) if isinstance(data, dict) else {}
            steps = plan.get("steps", []) if isinstance(plan, dict) else []
            if steps:
                click.echo(f"\nPlanned steps ({len(steps)}):")
                for i, step in enumerate(steps, 1):
                    click.echo(f"  {i}. {step}")
            else:
                click.echo(json.dumps(data, indent=2, default=str))

    asyncio.run(_execute())


@cli.command("dev")
@click.option("--port", default=8001, type=int)
def dev(port: int) -> None:
    """Start a local mock AgentVerse server for testing."""
    from agentverse.mock_server import MockServer

    server = MockServer(port=port)

    async def _run() -> None:
        await server.start()
        click.echo("\nMock server running. Press Ctrl+C to stop.")
        click.echo(f"Set: export AGENTVERSE_URL=http://127.0.0.1:{port}")
        click.echo(f"     export AGENTVERSE_API_KEY={server.get_api_key()}")
        try:
            await asyncio.sleep(86400)
        except KeyboardInterrupt:
            click.echo("\nStopping mock server.")

    asyncio.run(_run())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
