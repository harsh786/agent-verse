"""Fine-tuning data export endpoint.

Exports high-scoring goal executions as JSONL suitable for:
  - Anthropic Claude fine-tuning
  - OpenAI GPT fine-tuning

With a database (OPS-37) nothing is materialised in memory: the synchronous
export streams JSONL straight from keyset batches (``app.training_export.stream``),
preview is one aggregate query, and large exports run as durable jobs
(``training_export_jobs`` + a Celery worker writing to object storage) that any
replica can report on and serve.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.training_export import jobs as export_jobs
from app.training_export.stream import (
    formatter,
    iter_training_examples,
    preview_aggregate,
    to_anthropic_format,
    to_openai_format,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

# Kept for importers of the old private names.
_to_openai_format = to_openai_format
_to_anthropic_format = to_anthropic_format


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
_STORE_UNAVAILABLE = "Training data store unavailable"


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


def _sample(example: dict[str, Any]) -> dict[str, Any]:
    goal = example["goal"]
    return {
        "goal": goal[:120] + ("..." if len(goal) > 120 else ""),
        "eval_score": example["eval_score"],
        "steps": len(example.get("steps", [])),
        "tools": list(
            {s.get("tool_name", "") for s in example.get("steps", []) if s.get("tool_name")}
        ),
    }


def _memory_preview(examples: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [e["eval_score"] for e in examples]
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
    return {
        "count": len(examples),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "min_score_found": round(min(scores), 4) if scores else 0.0,
        "max_score_found": round(max(scores), 4) if scores else 0.0,
        "score_distribution": buckets,
    }


@router.get("/export-training-data/preview")
async def preview_training_data(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    limit: int = Query(1000, ge=1, le=10000),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Preview training data stats without triggering a download.

    Returns count, score distribution, and up to 3 sample records so the
    operator can verify the filter settings before exporting. With a database
    this is one aggregate query plus a 3-row sample — no examples are collected.
    """
    tenant = _require_tenant(request)
    goal_service = getattr(request.app.state, "goal_service", None)
    db = _db_factory(request, goal_service)

    if db is None:
        examples = _collect_training_examples_memory(
            goal_service, min_score, limit, tenant.tenant_id
        )
        return {**_memory_preview(examples), "samples": [_sample(e) for e in examples[:3]]}

    try:
        stats = await preview_aggregate(db, tenant.tenant_id, min_score, limit)
        samples = [
            _sample(e)
            async for e in iter_training_examples(
                db, tenant.tenant_id, min_score, min(3, limit), batch_size=3
            )
        ]
    except Exception as exc:
        # An empty preview is indistinguishable from "no qualifying goals".
        _log.warning("training_export_preview_failed tenant=%s: %s", tenant.tenant_id, exc)
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc
    return {**stats, "samples": samples}


def _filename(output_format: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"agentverse_training_{output_format}_{timestamp}.jsonl"


@router.post("/export-training-data", response_model=None)
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
        Streaming JSONL download. With a database the body is produced batch by
        batch while it is sent (memory is bounded by one batch), so the example
        count is not known up front: ``X-Training-Examples`` is only sent by the
        DB-less build. For very large exports use the durable job API.
    """
    tenant = _require_tenant(request)
    goal_service = getattr(request.app.state, "goal_service", None)
    db = _db_factory(request, goal_service)
    to_line = formatter(output_format)
    headers = {"Content-Disposition": f'attachment; filename="{_filename(output_format)}"'}

    if db is None:
        examples = _collect_training_examples_memory(
            goal_service, min_score, limit, tenant.tenant_id
        )
        content = "\n".join(json.dumps(to_line(ex)) for ex in examples)
        return StreamingResponse(
            iter([content]),
            media_type="application/x-ndjson",
            headers={**headers, "X-Training-Examples": str(len(examples))},
        )

    source = iter_training_examples(db, tenant.tenant_id, min_score, limit)
    # Read the first batch before answering: a store that cannot be read is a
    # 503, not a 200 with an empty (or silently truncated) file.
    try:
        first: dict[str, Any] | None = await anext(source)
    except StopAsyncIteration:
        first = None
    except Exception as exc:
        _log.warning("training_export_failed tenant=%s: %s", tenant.tenant_id, exc)
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc

    async def _body() -> AsyncIterator[bytes]:
        if first is None:
            return
        yield json.dumps(to_line(first)).encode()
        try:
            async for example in source:
                yield b"\n" + json.dumps(to_line(example)).encode()
        except Exception:
            # Headers are gone; abort the transfer so the client sees an
            # incomplete download instead of a file that looks whole.
            _log.exception("training_export_stream_aborted tenant=%s", tenant.tenant_id)
            raise

    return StreamingResponse(_body(), media_type="application/x-ndjson", headers=headers)


# ── durable export jobs ───────────────────────────────────────────────────────


def _enqueue(job_id: str, tenant_id: str) -> None:
    """Hand the job to a Celery worker (maintenance queue)."""
    from app.training_export.tasks import run_training_export

    run_training_export.apply_async(args=[job_id, tenant_id])


def _jobs_db(request: Request) -> Any:
    db = _db_factory(request, getattr(request.app.state, "goal_service", None))
    if db is None:
        raise HTTPException(503, "Durable export jobs need the database")
    return db


@router.post("/export-training-data/jobs", status_code=202)
async def create_training_export_job(
    min_score: float = Query(_MIN_EXPORT_SCORE, ge=0.0, le=1.0),
    output_format: str = Query("openai", alias="format", pattern="^(openai|anthropic)$"),
    limit: int = Query(10000, ge=1, le=1_000_000),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Queue a background export; poll ``GET .../jobs/{job_id}`` then download."""
    tenant = _require_tenant(request)
    db = _jobs_db(request)
    if export_jobs.object_store_from_env() is None:
        raise HTTPException(503, "Object storage is not configured for export jobs")
    try:
        job = await export_jobs.create_job(db, tenant.tenant_id, output_format, min_score, limit)
    except export_jobs.TooManyActiveExportJobsError as exc:
        raise HTTPException(429, str(exc)) from exc
    except export_jobs.TrainingExportUnavailableError as exc:
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc
    try:
        _enqueue(job["job_id"], tenant.tenant_id)
    except Exception as exc:
        _log.warning("training_export_enqueue_failed job=%s: %s", job["job_id"], exc)
        try:
            await export_jobs.mark_failed(
                db, tenant.tenant_id, job["job_id"], f"enqueue failed: {exc}"
            )
        except Exception as mark_exc:
            _log.error("training_export_mark_failed_failed job=%s: %s", job["job_id"], mark_exc)
        raise HTTPException(503, "Export queue unavailable; please retry") from exc
    return job


@router.get("/export-training-data/jobs")
async def list_training_export_jobs(
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tenant = _require_tenant(request)
    try:
        jobs = await export_jobs.list_jobs(_jobs_db(request), tenant.tenant_id)
    except export_jobs.TrainingExportUnavailableError as exc:
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc
    return {"jobs": jobs}


@router.get("/export-training-data/jobs/{job_id}")
async def get_training_export_job(
    job_id: str,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    tenant = _require_tenant(request)
    try:
        job = await export_jobs.get_job(_jobs_db(request), tenant.tenant_id, job_id)
    except export_jobs.TrainingExportUnavailableError as exc:
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc
    if job is None:
        raise HTTPException(404, "Export job not found")
    if job["has_file"]:
        job["download_url"] = f"/intelligence/export-training-data/jobs/{job_id}/download"
    return job


@router.get("/export-training-data/jobs/{job_id}/download", response_model=None)
async def download_training_export_job(
    job_id: str,
    request: Request = None,  # type: ignore[assignment]
) -> StreamingResponse | JSONResponse:
    """Stream a finished job's JSONL from object storage (owning tenant only)."""
    tenant = _require_tenant(request)
    try:
        found = await export_jobs.get_job_object_key(_jobs_db(request), tenant.tenant_id, job_id)
    except export_jobs.TrainingExportUnavailableError as exc:
        raise HTTPException(503, _STORE_UNAVAILABLE) from exc
    if found is None:
        raise HTTPException(404, "Export job not found")
    status, key = found
    if status == "expired":
        # NF-17: the file was deleted after the retention period.
        return JSONResponse(
            status_code=410,
            content={
                "detail": "Export file expired and was deleted; start a new export",
                "status": status,
            },
        )
    if status != "complete" or not key:
        return JSONResponse(
            status_code=409, content={"detail": f"Export job is {status}", "status": status}
        )
    store = export_jobs.object_store_from_env()
    if store is None:
        raise HTTPException(503, "Object storage is not configured for export jobs")
    chunks = export_jobs.iter_object(store, key)
    try:
        first = await anext(chunks)
    except StopAsyncIteration:
        first = b""
    except Exception as exc:
        _log.warning("training_export_download_failed job=%s: %s", job_id, exc)
        raise HTTPException(503, "Export file unavailable; please retry") from exc

    async def _body() -> AsyncIterator[bytes]:
        yield first
        async for chunk in chunks:
            yield chunk

    return StreamingResponse(
        _body(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="agentverse_training_{job_id}.jsonl"'
        },
    )


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
