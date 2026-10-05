"""Streaming training-example source (OPS-37).

The export used to materialise up to 10k goals (and every step output) in
memory, from a query whose ``DISTINCT ON`` CTEs scanned all of the tenant's
evaluations and scorecards. Now:

* candidates are read in keyset batches over the tenant's completed goals
  (``(created_at, id) DESC``, partial index ``ix_goals_tenant_completed_created``);
* each goal's score is its latest evaluation (else latest scorecard), looked
  up per candidate with a ``LIMIT 1`` on the ``(goal_id, created_at DESC)``
  indexes — never a whole-ledger scan;
* every batch runs in its own short RLS transaction, so a long download does
  not pin a connection or a snapshot;
* step outputs are truncated and capped per goal (the final step, which is
  the exported answer, is always kept).

``iter_training_examples`` is an async generator: memory is bounded by one
batch whatever ``limit`` is. DB failures raise (callers turn the first one into
a 503; a failure mid-stream aborts the download).
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from typing import Any

from app.db.rls import sqlalchemy_rls_context

BATCH_SIZE = 200
MAX_STEP_OUTPUT_CHARS = 4000
MAX_STEPS_PER_GOAL = 50

_SCORE_SQL = (
    "COALESCE("
    "(SELECT e.average_score FROM evaluations e "
    " WHERE e.goal_id = g.id AND e.tenant_id = :tid "
    " ORDER BY e.created_at DESC LIMIT 1), "
    "(SELECT sc.overall_score FROM eval_scorecards sc "
    " WHERE sc.goal_id = g.id AND sc.tenant_id = :tid "
    " ORDER BY sc.created_at DESC LIMIT 1))"
)

# Candidate goals with their score; the caller appends keyset / LIMIT clauses.
CANDIDATES_SQL = (
    "SELECT g.id, g.goal_text, g.created_at, s.score "
    "FROM goals g "
    f"CROSS JOIN LATERAL (SELECT {_SCORE_SQL} AS score) s "
    "WHERE g.tenant_id = :tid AND g.status IN ('complete', 'completed') "
    "AND s.score >= :min_score"
)


async def _candidate_batch(
    db: Any,
    tenant_id: str,
    min_score: float,
    size: int,
    after: tuple[datetime.datetime, str] | None,
) -> tuple[list[Any], dict[str, list[dict[str, Any]]]]:
    from sqlalchemy import text

    keyset = " AND (g.created_at, g.id) < (:after_ts, :after_id)" if after else ""
    params: dict[str, Any] = {"tid": tenant_id, "min_score": min_score, "lim": size}
    if after:
        params["after_ts"], params["after_id"] = after
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (
            await session.execute(
                text(CANDIDATES_SQL + keyset + " ORDER BY g.created_at DESC, g.id DESC LIMIT :lim"),
                params,
            )
        ).fetchall()
        goal_ids = [str(r[0]) for r in rows]
        steps: dict[str, list[dict[str, Any]]] = {gid: [] for gid in goal_ids}
        if goal_ids:
            step_rows = (
                await session.execute(
                    text(
                        "SELECT goal_id, LEFT(output, :max_chars), tool_calls FROM ("
                        " SELECT goal_id, output, tool_calls, step_index, "
                        " ROW_NUMBER() OVER (PARTITION BY goal_id ORDER BY step_index) AS rn,"
                        " ROW_NUMBER() OVER (PARTITION BY goal_id ORDER BY step_index DESC)"
                        " AS rn_desc"
                        " FROM goal_steps WHERE tenant_id = :tid AND goal_id = ANY(:gids)"
                        # The first steps up to the cap, plus always the last one:
                        # its output is the exported answer.
                        ") st WHERE rn < :max_steps OR rn_desc = 1"
                        " ORDER BY goal_id, step_index ASC"
                    ),
                    {
                        "tid": tenant_id,
                        "gids": goal_ids,
                        "max_chars": MAX_STEP_OUTPUT_CHARS,
                        "max_steps": MAX_STEPS_PER_GOAL,
                    },
                )
            ).fetchall()
            for gid, output, tool_calls in step_rows:
                calls = tool_calls if isinstance(tool_calls, list) else []
                first = calls[0] if calls and isinstance(calls[0], dict) else {}
                steps.setdefault(str(gid), []).append(
                    {
                        "type": "step_complete",
                        "tool_name": first.get("tool_name", ""),
                        "output": output or "",
                    }
                )
    return rows, steps


async def iter_training_examples(
    db: Any,
    tenant_id: str,
    min_score: float,
    limit: int,
    *,
    batch_size: int = BATCH_SIZE,
) -> AsyncIterator[dict[str, Any]]:
    """Yield up to *limit* qualifying examples, newest goal first, one batch in memory."""
    emitted = 0
    after: tuple[datetime.datetime, str] | None = None
    while emitted < limit:
        size = min(batch_size, limit - emitted)
        rows, steps = await _candidate_batch(db, tenant_id, min_score, size, after)
        for goal_id, goal_text, _created, score in rows:
            goal_steps = steps.get(str(goal_id), [])
            yield {
                "goal_id": str(goal_id),
                "goal": goal_text or "",
                # goals has no result column; the final step's output is the answer.
                "result": goal_steps[-1]["output"] if goal_steps else "",
                "steps": goal_steps,
                "eval_score": float(score or 0.0),
                "model": "unknown",
            }
            emitted += 1
        if len(rows) < size:
            return
        last = rows[-1]
        after = (last[2], str(last[0]))


async def preview_aggregate(
    db: Any, tenant_id: str, min_score: float, limit: int
) -> dict[str, Any]:
    """Count / score stats of the first *limit* candidates in one aggregate query.

    No example rows (or step outputs) are fetched.
    """
    from sqlalchemy import text

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*), AVG(score), MIN(score), MAX(score), "
                    "COUNT(*) FILTER (WHERE score < 0.85), "
                    "COUNT(*) FILTER (WHERE score >= 0.85 AND score < 0.90), "
                    "COUNT(*) FILTER (WHERE score >= 0.90 AND score < 0.95), "
                    "COUNT(*) FILTER (WHERE score >= 0.95) "
                    f"FROM ({CANDIDATES_SQL} ORDER BY g.created_at DESC, g.id DESC "
                    "LIMIT :lim) c"
                ),
                {"tid": tenant_id, "min_score": min_score, "lim": limit},
            )
        ).fetchone()
    count, avg, lo, hi, b1, b2, b3, b4 = row if row is not None else (0,) * 8
    return {
        "count": int(count or 0),
        "avg_score": round(float(avg), 4) if avg is not None else 0.0,
        "min_score_found": round(float(lo), 4) if lo is not None else 0.0,
        "max_score_found": round(float(hi), 4) if hi is not None else 0.0,
        "score_distribution": {
            "0.80-0.85": int(b1 or 0),
            "0.85-0.90": int(b2 or 0),
            "0.90-0.95": int(b3 or 0),
            "0.95-1.00": int(b4 or 0),
        },
    }


def to_openai_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to OpenAI fine-tuning JSONL format."""
    system = "You are an autonomous AI agent. Execute goals step by step."
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": example["goal"]},
    ]
    for step in example.get("steps", []):
        tool_name = step.get("tool_name", "")
        output = step.get("output", "")
        if tool_name:
            messages.append({"role": "assistant", "content": f"[{tool_name}] {output}"})

    messages.append({"role": "assistant", "content": example.get("result", "")})
    return {"messages": messages, "metadata": {"eval_score": example.get("eval_score")}}


def to_anthropic_format(example: dict[str, Any]) -> dict[str, Any]:
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


def formatter(output_format: str) -> Any:
    return to_openai_format if output_format == "openai" else to_anthropic_format
