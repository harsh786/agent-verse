"""AgentVerse CLI — submit goals, create agents, manage schedules."""
from __future__ import annotations

import json
import os
import sys

import httpx
import typer

app = typer.Typer(
    name="agentverse",
    help="AgentVerse CLI — autonomous agent operating system",
)


def _base_url() -> str:
    # Check config file first, then env var
    from pathlib import Path
    config_path = Path.home() / ".agentverse" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if cfg.get("base_url"):
                return cfg["base_url"]
        except Exception:
            pass
    return os.getenv("AGENTVERSE_URL", "http://localhost:8000")


def _api_key() -> str:
    # Check config file first, then env var
    from pathlib import Path
    config_path = Path.home() / ".agentverse" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if cfg.get("api_key"):
                return cfg["api_key"]
        except Exception:
            pass
    key = os.getenv("AGENTVERSE_API_KEY", "")
    if not key:
        typer.echo("Error: AGENTVERSE_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)
    return key


def _headers() -> dict[str, str]:
    return {"X-API-Key": _api_key(), "Content-Type": "application/json"}


def _get(url: str, api_key: str) -> dict:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, headers={"X-API-Key": api_key, "Content-Type": "application/json"})
        resp.raise_for_status()
        return resp.json()


def _post(url: str, api_key: str, body: dict) -> dict:
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            url,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


@app.command()
def login(
    api_key: str = typer.Option(..., "--key", "-k", prompt="API Key", hide_input=True,
                                help="Your AgentVerse API key"),
    base_url: str = typer.Option("http://localhost:8000", "--url", "-u",
                                 help="AgentVerse API base URL"),
) -> None:
    """Save API key to ~/.agentverse/config.json for CLI use."""
    from pathlib import Path
    config_dir = Path.home() / ".agentverse"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except Exception:
            pass
    config["api_key"] = api_key
    config["base_url"] = base_url
    config_path.write_text(json.dumps(config, indent=2))
    typer.echo(f"✓ Credentials saved to {config_path}")


@app.command()
def create(
    command: str = typer.Argument(..., help="Natural language command to create an agent"),
    autorun: bool = typer.Option(False, "--autorun", help="Auto-run the agent after creation"),
    output: str = typer.Option("json", "--output", "-o", help="Output format: json|text"),
) -> None:
    """Create an agent from a natural language command."""
    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        resp = client.post("/agents/create", json={"command": command, "autorun": autorun})
        resp.raise_for_status()
        data = resp.json()
        if output == "json":
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(f"Agent created: {data.get('agent_id', 'unknown')}")


@app.command()
def submit(
    goal: str = typer.Argument(..., help="Goal to submit"),
    priority: str = typer.Option("normal", "--priority", "-p"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Stream execution events in real time"),
) -> None:
    """Submit a goal for autonomous execution."""
    with httpx.Client(base_url=_base_url(), headers=_headers(), timeout=10.0) as client:
        resp = client.post("/goals", json={"goal": goal, "priority": priority, "dry_run": dry_run})
        resp.raise_for_status()
        data = resp.json()
        goal_id = data.get("goal_id", "")
        typer.echo(f"Goal submitted: {goal_id}")
        typer.echo(f"Status: {data.get('status', 'unknown')}")

        if watch and goal_id and not dry_run:
            typer.echo("\n--- Streaming execution events ---")
            _stream_goal(goal_id)


def _stream_goal(goal_id: str) -> None:
    """Stream SSE events for a goal to the terminal."""
    try:
        with httpx.Client(base_url=_base_url(), headers=_headers(), timeout=None) as client:
            with client.stream("GET", f"/goals/{goal_id}/stream") as resp:
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            event_type = event.get("type", "")
                            if event_type == "goal_started":
                                typer.echo(f"[START] Goal: {event.get('goal', '')}")
                            elif event_type == "plan_ready":
                                steps = event.get("steps", [])
                                typer.echo(f"[PLAN] {len(steps)} steps: {', '.join(steps[:3])}")
                            elif event_type == "step_started":
                                typer.echo(f"  > {event.get('step', '')}")
                            elif event_type == "step_complete":
                                output = event.get("output", "")[:100]
                                typer.echo(f"    + {output}")
                            elif event_type == "goal_complete":
                                typer.echo("[DONE] Goal completed successfully!")
                                break
                            elif event_type == "goal_failed":
                                typer.echo(f"[FAIL] {event.get('reason', 'Unknown error')}")
                                sys.exit(1)
                            elif event_type == "waiting_approval":
                                typer.echo(
                                    f"[WAIT] Approval needed for: {event.get('action', '')}"
                                )
                                typer.echo(f"       Request ID: {event.get('request_id', '')}")
                        except json.JSONDecodeError:
                            pass
    except KeyboardInterrupt:
        typer.echo("\n[INTERRUPTED] Streaming stopped. Goal continues in background.")


@app.command(name="watch")
def watch(
    goal_id: str = typer.Argument(..., help="Goal ID to stream events for"),
) -> None:
    """Stream execution events for an existing goal in real time."""
    _stream_goal(goal_id)


@app.command()
def status(
    goal_id: str = typer.Argument(..., help="Goal ID to check"),
) -> None:
    """Check the status of a goal."""
    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        resp = client.get(f"/goals/{goal_id}")
        resp.raise_for_status()
        typer.echo(json.dumps(resp.json(), indent=2))


@app.command()
def agents() -> None:
    """List all agents."""
    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        resp = client.get("/agents")
        resp.raise_for_status()
        data = resp.json()
        if not data:
            typer.echo("No agents found.")
        else:
            for agent in data:
                typer.echo(
                    f"  {agent.get('agent_id', '?')} | "
                    f"{agent.get('name', '?')} | "
                    f"{agent.get('autonomy_mode', '?')}"
                )


@app.command()
def schedule(
    command: str = typer.Argument(..., help="Natural language schedule command"),
    agent_id: str = typer.Option("", "--agent", "-a", help="Agent ID to schedule for"),
) -> None:
    """Create a schedule from natural language."""
    with httpx.Client(base_url=_base_url(), headers=_headers()) as client:
        resp = client.post("/nl/schedule", json={"command": command, "agent_id": agent_id})
        resp.raise_for_status()
        typer.echo(json.dumps(resp.json(), indent=2))


@app.command(name="goals")
def list_goals_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of goals to show"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
) -> None:
    """List recent goals."""
    data = _get(f"{_base_url()}/goals", _api_key())
    all_goals = data.get("goals") or []
    if status:
        all_goals = [g for g in all_goals if g.get("status") == status]
    for g in all_goals[:limit]:
        gid = str(g.get("goal_id", g.get("id", "??")))[:12]
        stat = str(g.get("status", "?")).ljust(14)
        goal_text = str(g.get("goal", ""))[:60]
        typer.echo(f"{gid}  {stat}  {goal_text}")


@app.command()
def cancel(goal_id: str = typer.Argument(..., help="Goal ID to cancel")) -> None:
    """Cancel a running goal."""
    result = _post(f"{_base_url()}/goals/{goal_id}/cancel", _api_key(), {})
    typer.echo(f"Cancelled goal {goal_id}: {result.get('status', 'unknown')}")


@app.command()
def approve(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    note: str = typer.Option("", "--note", "-n", help="Optional approval note"),
) -> None:
    """Approve a pending HITL approval request."""
    result = _post(
        f"{_base_url()}/governance/approvals/{request_id}/approve",
        _api_key(),
        {"approver": "cli-user", "note": note},
    )
    typer.echo(f"Approved request {request_id}: {result}")


@app.command()
def reject(
    request_id: str = typer.Argument(..., help="Approval request ID"),
    note: str = typer.Option("Required: explain rejection", "--note", "-n"),
) -> None:
    """Reject a pending HITL approval request."""
    result = _post(
        f"{_base_url()}/governance/approvals/{request_id}/reject",
        _api_key(),
        {"approver": "cli-user", "note": note},
    )
    typer.echo(f"Rejected request {request_id}: {result}")


@app.command(name="connectors")
def list_connectors() -> None:
    """List registered MCP connectors."""
    data = _get(f"{_base_url()}/connectors", _api_key())
    connectors = data if isinstance(data, list) else []
    if not connectors:
        typer.echo("No connectors registered.")
        return
    for c in connectors:
        sid = str(c.get("server_id", "??"))[:16].ljust(18)
        name = str(c.get("name", "??")).ljust(20)
        status_str = str(c.get("status", "unknown"))
        typer.echo(f"{sid}  {name}  {status_str}")


@app.command(name="eval")
def eval_goal(goal_id: str = typer.Argument(..., help="Goal ID to evaluate")) -> None:
    """Show eval scorecard for a completed goal."""
    data = _get(f"{_base_url()}/goals/{goal_id}/eval", _api_key())
    if "scores" not in data:
        typer.echo(f"No eval data for goal {goal_id}")
        return
    scores = data["scores"]
    typer.echo(f"\nEval scores for goal {goal_id}:")
    for dim, score in scores.items():
        bar = "█" * int((score or 0) * 20)
        typer.echo(f"  {dim:22} {bar:<20} {score:.2f}")
    avg = data.get("average_score", sum(scores.values()) / max(len(scores), 1))
    status_str = "✓ PASS" if avg >= 0.7 else "✗ FAIL"
    typer.echo(f"\n  Average: {avg:.2f}  {status_str}\n")


@app.command()
def logs(
    goal_id: str = typer.Argument(..., help="Goal ID"),
    tail: int = typer.Option(50, "--tail", "-n", help="Number of events to show"),
) -> None:
    """Show recent execution events for a goal."""
    data = _get(f"{_base_url()}/goals/{goal_id}/events", _api_key())
    events = data if isinstance(data, list) else []
    for evt in events[-tail:]:
        ts = str(evt.get("ts", ""))[:19]
        etype = str(evt.get("type", "event")).ljust(26)
        detail = str(evt.get("step") or evt.get("output") or evt.get("tool_name") or "")[:60]
        typer.echo(f"[{ts}]  {etype}  {detail}")


@app.command(name="manifest")
def manifest_cmd(
    action: str = typer.Argument("validate", help="Action: validate"),
    path: str = typer.Argument("agent.yaml", help="Path to manifest YAML file"),
) -> None:
    """Manage agent manifests. Usage: agentverse manifest validate agent.yaml"""
    if action == "validate":
        try:
            from app.sdk.manifest import AgentManifest
            manifest = AgentManifest.from_yaml(path)
            errors = manifest.validate()
            if errors:
                typer.echo("❌ Manifest validation failed:")
                for e in errors:
                    typer.echo(f"  • {e}")
                raise typer.Exit(1)
            else:
                typer.echo(f"✅ Manifest '{manifest.name}' v{manifest.version} is valid")
                typer.echo(f"   Autonomy: {manifest.autonomy_mode}")
                if manifest.connector_requirements:
                    typer.echo(f"   Connectors: {[c.type for c in manifest.connector_requirements]}")
        except FileNotFoundError:
            typer.echo(f"❌ File not found: {path}")
            raise typer.Exit(1)
        except Exception as exc:
            typer.echo(f"❌ Error: {exc}")
            raise typer.Exit(1)
    else:
        typer.echo(f"Unknown action: {action}. Use: validate")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
