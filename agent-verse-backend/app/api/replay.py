"""Goal execution replay API.

Provides step-by-step reconstruction of a completed goal's execution
timeline from persisted goal_events in the database.
"""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Request, Query
from app.observability.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/goals", tags=["replay"])


@router.get("/{goal_id}/replay")
async def replay_goal(
    request: Request,
    goal_id: str,
    include_raw_output: bool = Query(True, description="Include raw LLM output per step"),
    include_tool_calls: bool = Query(True, description="Include tool call details"),
) -> dict[str, Any]:
    """Reconstruct the full execution timeline of a completed goal.

    Returns a chronologically ordered list of events with:
    - Plan steps and their descriptions
    - Tool calls and their results
    - LLM prompts and completions (if include_raw_output=True)
    - Governance events (policy checks, HITL approvals)
    - Eval scores
    - Timing and cost per step
    """
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    if db is None:
        raise HTTPException(503, "Database not available for replay")

    try:
        from sqlalchemy import text
        async with db() as session:
            # Verify goal exists and belongs to tenant
            goal_row = (await session.execute(
                text("SELECT id, goal_text, status, created_at, completed_at FROM goals WHERE id=:gid AND tenant_id=:tid"),
                {"gid": goal_id, "tid": tenant_ctx.tenant_id}
            )).fetchone()

            if goal_row is None:
                raise HTTPException(404, f"Goal {goal_id} not found")

            # Load all goal events in chronological order
            events = (await session.execute(
                text("""
                    SELECT sequence, event_type, payload, created_at
                    FROM goal_events
                    WHERE goal_id=:gid AND tenant_id=:tid
                    ORDER BY sequence ASC
                """),
                {"gid": goal_id, "tid": tenant_ctx.tenant_id}
            )).fetchall()

            # Load goal steps
            steps = (await session.execute(
                text("""
                    SELECT step_index, description, status, output, error, tool_calls, created_at
                    FROM goal_steps
                    WHERE goal_id=:gid AND tenant_id=:tid
                    ORDER BY step_index ASC
                """),
                {"gid": goal_id, "tid": tenant_ctx.tenant_id}
            )).fetchall()

            # Load decision traces
            traces = (await session.execute(
                text("""
                    SELECT action, reasoning, confidence, evidence, created_at
                    FROM decision_traces
                    WHERE goal_id=:gid AND tenant_id=:tid
                    ORDER BY created_at ASC
                """),
                {"gid": goal_id, "tid": tenant_ctx.tenant_id}
            )).fetchall()

            # Load eval results
            evals = (await session.execute(
                text("SELECT scores, average_score, created_at FROM evaluations WHERE goal_id=:gid AND tenant_id=:tid"),
                {"gid": goal_id, "tid": tenant_ctx.tenant_id}
            )).fetchall()

        # Build timeline
        timeline = []

        # Add plan creation event
        timeline.append({
            "type": "goal_created",
            "ts": goal_row[3].isoformat() if goal_row[3] else "",
            "data": {"goal_text": goal_row[1], "status": goal_row[2]},
        })

        # Add goal events
        import json as _json
        for seq, etype, payload, created_at in events:
            event_data = payload if isinstance(payload, dict) else _json.loads(payload or "{}")

            # Filter sensitive data based on request params
            if not include_raw_output:
                event_data.pop("raw_output", None)
                event_data.pop("llm_prompt", None)
            if not include_tool_calls:
                event_data.pop("tool_calls", None)
                event_data.pop("tool_result", None)

            timeline.append({
                "type": etype,
                "sequence": seq,
                "ts": created_at.isoformat() if created_at else "",
                "data": event_data,
            })

        # Add step summaries
        step_summaries = []
        for step_idx, desc, status, output, error, tool_calls, created_at in steps:
            tc_list = tool_calls if isinstance(tool_calls, list) else []
            step_summary = {
                "step_index": step_idx,
                "description": desc,
                "status": status,
                "created_at": created_at.isoformat() if created_at else "",
            }
            if include_raw_output:
                step_summary["output"] = output or ""
            if include_tool_calls:
                step_summary["tool_calls"] = tc_list
            if error:
                step_summary["error"] = error
            step_summaries.append(step_summary)

        # Add decision traces
        trace_summaries = [
            {
                "action": t[0],
                "reasoning": t[1],
                "confidence": t[2],
                "ts": t[4].isoformat() if t[4] else "",
            }
            for t in traces
        ]

        # Add eval results
        eval_summaries = []
        for scores, avg_score, created_at in evals:
            eval_summaries.append({
                "scores": scores if isinstance(scores, dict) else _json.loads(scores or "{}"),
                "average_score": float(avg_score or 0),
                "ts": created_at.isoformat() if created_at else "",
            })

        return {
            "goal_id": goal_id,
            "goal_text": goal_row[1],
            "status": goal_row[2],
            "created_at": goal_row[3].isoformat() if goal_row[3] else "",
            "completed_at": goal_row[4].isoformat() if goal_row[4] else None,
            "event_count": len(events),
            "step_count": len(steps),
            "timeline": timeline,
            "steps": step_summaries,
            "decision_traces": trace_summaries,
            "evaluations": eval_summaries,
        }

    except HTTPException:
        raise
    except (RuntimeError, OSError) as exc:
        # Connection/event-loop errors (e.g. asyncpg pool on wrong loop) → 503
        logger.warning("goal_replay_service_unavailable", goal_id=goal_id, error=str(exc))
        raise HTTPException(503, "Replay service temporarily unavailable") from exc
    except Exception as exc:
        logger.warning("goal_replay_failed", goal_id=goal_id, error=str(exc))
        raise HTTPException(500, f"Replay failed: {exc}") from exc


@router.get("/{goal_id}/timeline")
async def goal_timeline(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Get a compact chronological timeline of goal events for visualization."""
    result = await replay_goal(request, goal_id, include_raw_output=False, include_tool_calls=False)
    return result["timeline"]


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _get_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        try:
            from app.db.session import get_session_factory
            db = get_session_factory()
        except Exception:
            pass
    return db
