"""Fine-tuning data export endpoint.

Exports high-scoring goal executions as JSONL suitable for:
  - Anthropic Claude fine-tuning
  - OpenAI GPT fine-tuning
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.db.rls import sqlalchemy_rls_context

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _require_tenant(request: Request) -> Any:
    """Both export endpoints are tenant-scoped; neither used to resolve a tenant.

    The DB collector selected from `goals`/`evaluations` with NO tenant
    predicate, and the in-memory collector iterated every goal in the process
    cache — so this endpoint was cross-tenant by construction. RLS masked the
    DB half (zero rows with no GUC set, which is also why the export came back
    empty), but the in-memory fallback had nothing stopping it.
    """
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx

_MIN_EXPORT_SCORE = 0.8


def _db_factory(request: Request, goal_service: Any) -> Any:
    """The DB session factory, or None for no-DB builds.

    This used to read ``goal_service._db_session_factory`` — an attribute
    GoalService does not have (it stores the factory as ``_db``), so the DB
    path never ran and the export was always the (broken) in-memory fallback.
    """
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None and goal_service is not None:
        db = getattr(goal_service, "_db", None)
    return db


async def _collect(
    db: Any, goal_service: Any, min_score: float, limit: int, tenant_id: str
) -> list[dict[str, Any]]:
    if db is None:
        return _collect_training_examples_memory(goal_service, min_score, limit, tenant_id)
    try:
        return await _collect_training_examples_db(db, min_score, limit, tenant_id)
    except Exception as exc:
        # An export that silently comes back empty is indistinguishable from
        # "no qualifying goals"; fail loudly instead.
        raise HTTPException(503, "Training data store unavailable") from exc


@router.get("/export-training-data/preview")
async def preview_training_data(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    limit: int = Query(1000, ge=1, le=10000),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Preview training data stats without triggering a download.

    Returns count, score distribution, and up to 3 sample records so the
    operator can verify the filter settings before exporting.
    """
    tenant = _require_tenant(request)
    goal_service = getattr(request.app.state, "goal_service", None)
    db = _db_factory(request, goal_service)

    examples = await _collect(db, goal_service, min_score, limit, tenant.tenant_id)

    scores = [e["eval_score"] for e in examples]
    # Use ASCII hyphens in bucket keys (ruff RUF001)
    buckets: dict[str, int] = {
        "0.80-0.85": 0,
        "0.85-0.90": 0,
        "0.90-0.95": 0,
        "0.95-1.00": 0,
    }
    for s in scores:
        if s < 0.85:
            buckets["0.80-0.85"] += 1
        elif s < 0.90:
            buckets["0.85-0.90"] += 1
        elif s < 0.95:
            buckets["0.90-0.95"] += 1
        else:
            buckets["0.95-1.00"] += 1

    samples = [
        {
            "goal": e["goal"][:120] + ("..." if len(e["goal"]) > 120 else ""),
            "eval_score": e["eval_score"],
            "steps": len(e.get("steps", [])),
            "tools": list(
                {s.get("tool_name", "") for s in e.get("steps", []) if s.get("tool_name")}
            ),
        }
        for e in examples[:3]
    ]

    return {
        "count": len(examples),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "min_score_found": round(min(scores), 4) if scores else 0.0,
        "max_score_found": round(max(scores), 4) if scores else 0.0,
        "score_distribution": buckets,
        "samples": samples,
    }


@router.post("/export-training-data")
async def export_training_data(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    output_format: str = Query("openai", alias="format", pattern="^(openai|anthropic)$"),
    limit: int = Query(1000, ge=1, le=10000),
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """Export successful goal executions as JSONL for LLM fine-tuning.

    Query params:
        min_score: Minimum eval score to include (default 0.8).
        format:    JSONL format: 'openai' or 'anthropic'.
        limit:     Maximum number of examples to export.

    Returns:
        Streaming JSONL download.
    """
    tenant = _require_tenant(request)
    goal_service = getattr(request.app.state, "goal_service", None)
    db = _db_factory(request, goal_service)

    # DB is authoritative when configured; in-memory only for no-DB builds.
    examples = await _collect(db, goal_service, min_score, limit, tenant.tenant_id)

    if output_format == "openai":
        jsonl_lines = [_to_openai_format(ex) for ex in examples]
    else:
        jsonl_lines = [_to_anthropic_format(ex) for ex in examples]

    content = "\n".join(json.dumps(line) for line in jsonl_lines)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"agentverse_training_{output_format}_{timestamp}.jsonl"

    return StreamingResponse(
        io.StringIO(content),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Training-Examples": str(len(jsonl_lines)),
        },
    )


async def _collect_training_examples_db(
    db: Any,
    min_score: float,
    limit: int,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Completed, high-scoring goals from Postgres, under the tenant's RLS context.

    The score is the goal's latest durable evaluation: ``evaluations.average_score``
    (EvalRunner.score_and_persist), falling back to ``eval_scorecards.overall_score``
    (the runtime scorecard). One latest row per goal (DISTINCT ON) so repeated
    evaluations do not duplicate examples. Steps are fetched in ONE query. Raises
    on DB failure (the caller turns it into a 503).
    """
    from sqlalchemy import text

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (
            await session.execute(
                text(
                    """
                    WITH ev AS (
                        SELECT DISTINCT ON (goal_id) goal_id, average_score AS score
                        FROM evaluations WHERE tenant_id = :tid
                        ORDER BY goal_id, created_at DESC
                    ), sc AS (
                        SELECT DISTINCT ON (goal_id) goal_id, overall_score AS score
                        FROM eval_scorecards WHERE tenant_id = :tid
                        ORDER BY goal_id, created_at DESC
                    )
                    SELECT g.id, g.goal_text, COALESCE(ev.score, sc.score) AS score
                    FROM goals g
                    LEFT JOIN ev ON ev.goal_id = g.id
                    LEFT JOIN sc ON sc.goal_id = g.id
                    WHERE g.tenant_id = :tid
                      AND g.status IN ('complete', 'completed')
                      AND COALESCE(ev.score, sc.score) >= :min_score
                    ORDER BY score DESC, g.id
                    LIMIT :limit
                    """
                ),
                {"min_score": min_score, "limit": limit, "tid": tenant_id},
            )
        ).fetchall()
        goal_ids = [str(r[0]) for r in rows]
        steps_by_goal: dict[str, list[dict[str, Any]]] = {gid: [] for gid in goal_ids}
        if goal_ids:
            step_rows = (
                await session.execute(
                    text(
                        "SELECT goal_id, output, tool_calls FROM goal_steps "
                        "WHERE tenant_id = :tid AND goal_id = ANY(:gids) "
                        "ORDER BY goal_id, step_index ASC"
                    ),
                    {"tid": tenant_id, "gids": goal_ids},
                )
            ).fetchall()
            for gid, output, tool_calls in step_rows:
                calls = tool_calls if isinstance(tool_calls, list) else []
                first = calls[0] if calls and isinstance(calls[0], dict) else {}
                steps_by_goal.setdefault(str(gid), []).append(
                    {
                        "type": "step_complete",
                        "tool_name": first.get("tool_name", ""),
                        "output": output or "",
                    }
                )

    examples: list[dict[str, Any]] = []
    for goal_id, goal_text, score in rows:
        steps = steps_by_goal.get(str(goal_id), [])
        examples.append(
            {
                "goal": goal_text or "",
                # goals has no result column; the final step's output is the answer
                # (the old query exported error_message as the "result").
                "result": steps[-1]["output"] if steps else "",
                "steps": steps,
                "eval_score": float(score or 0.0),
                "model": "unknown",
            }
        )
    return examples


def _score_of(scorecard: Any) -> float | None:
    if scorecard is None:
        return None
    avg = getattr(scorecard, "average_score", None)
    value = avg() if callable(avg) else avg
    if value is None:
        value = getattr(scorecard, "overall_score", None)
    return float(value) if isinstance(value, int | float) else None


def _collect_training_examples_memory(
    goal_service: Any,
    min_score: float,
    limit: int,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """No-DB fallback: completed goals in the GoalService cache.

    Scores come from ``GoalService._eval_scores`` (goal_id → EvalScorecard, the
    in-process mirror of the evaluations row). The old code read a non-existent
    ``GoalRecord.eval_score`` attribute, so nothing ever qualified.
    """
    if goal_service is None:
        return []

    goals = list(getattr(goal_service, "_goals", {}).values())
    scores = getattr(goal_service, "_eval_scores", {}) or {}
    examples: list[dict[str, Any]] = []

    for g in goals:
        if str(getattr(g, "tenant_id", "")) != tenant_id:
            continue
        status = str(getattr(getattr(g, "status", ""), "value", getattr(g, "status", "")))
        if status.lower() not in ("complete", "completed"):
            continue
        eval_score = _score_of(scores.get(getattr(g, "goal_id", None)))
        if eval_score is None or eval_score < min_score:
            continue

        events = getattr(g, "events", []) or []
        steps = [e for e in events if e.get("type") == "step_complete"]
        if not steps:
            continue

        examples.append(
            {
                "goal": getattr(g, "goal_text", ""),
                "result": getattr(g, "result", None) or str(steps[-1].get("output", "")),
                "steps": steps,
                "eval_score": eval_score,
                "model": getattr(g, "model", None) or "unknown",
            }
        )

        if len(examples) >= limit:
            break

    return examples


def _to_openai_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to OpenAI fine-tuning JSONL format."""
    _system = "You are an autonomous AI agent. Execute goals step by step."
    messages = [
        {"role": "system", "content": _system},
        {"role": "user", "content": example["goal"]},
    ]
    for step in example.get("steps", []):
        tool_name = step.get("tool_name", "")
        output = step.get("output", "")
        if tool_name:
            messages.append({"role": "assistant", "content": f"[{tool_name}] {output}"})

    messages.append({"role": "assistant", "content": example.get("result", "")})
    return {"messages": messages, "metadata": {"eval_score": example.get("eval_score")}}


def _to_anthropic_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to Anthropic fine-tuning JSONL format."""
    turns = []
    for step in example.get("steps", []):
        tool_name = step.get("tool_name", "")
        output = step.get("output", "")
        if tool_name:
            turns.append({"role": "assistant", "content": f"[{tool_name}] {output}"})

    return {
        "system": "You are an autonomous AI agent. Execute goals step by step.",
        "messages": [
            {"role": "user", "content": example["goal"]},
            *turns,
            {"role": "assistant", "content": example.get("result", "")},
        ],
        "metadata": {
            "eval_score": example.get("eval_score"),
            "model": example.get("model"),
        },
    }
