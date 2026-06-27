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

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

_MIN_EXPORT_SCORE = 0.8


@router.post("/export-training-data")
async def export_training_data(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    format: str = Query("openai", pattern="^(openai|anthropic)$"),
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
    goal_service = getattr(request.app.state, "goal_service", None)
    examples = _collect_training_examples(goal_service, min_score, limit)

    if format == "openai":
        jsonl_lines = [_to_openai_format(ex) for ex in examples]
    else:
        jsonl_lines = [_to_anthropic_format(ex) for ex in examples]

    content = "\n".join(json.dumps(line) for line in jsonl_lines)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"agentverse_training_{format}_{timestamp}.jsonl"

    return StreamingResponse(
        io.StringIO(content),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Training-Examples": str(len(jsonl_lines)),
        },
    )


def _collect_training_examples(
    goal_service: Any,
    min_score: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Extract high-scoring goal executions from the GoalService."""
    if goal_service is None:
        return []

    goals = list(getattr(goal_service, "_goals", {}).values())
    examples: list[dict[str, Any]] = []

    for g in goals:
        status = str(getattr(g, "status", "")).lower()
        if status not in ("complete", "completed"):
            continue
        eval_score = getattr(g, "eval_score", None)
        if eval_score is None or eval_score < min_score:
            continue

        events = getattr(g, "events", [])
        steps = [e for e in events if e.get("type") == "step_complete"]
        if not steps:
            continue

        examples.append(
            {
                "goal": getattr(g, "goal", ""),
                "result": getattr(g, "result", ""),
                "steps": steps,
                "eval_score": eval_score,
                "model": getattr(g, "model", "unknown"),
            }
        )

        if len(examples) >= limit:
            break

    return examples


def _to_openai_format(example: dict[str, Any]) -> dict[str, Any]:
    """Convert a goal execution to OpenAI fine-tuning JSONL format."""
    messages = [
        {"role": "system", "content": "You are an autonomous AI agent. Execute goals step by step."},
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
