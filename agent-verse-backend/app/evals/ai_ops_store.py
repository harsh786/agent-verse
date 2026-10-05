"""Durable storage for the AI-Ops centre (eval datasets, results, drift state).

`app/api/ai_ops.py` is a mounted, live router whose entire state — evaluation
datasets and their golden tasks, eval results, LLM-judge configs, metric
baselines and drift alerts — lived in module-level dicts. Everything was lost on
restart and invisible to every other replica, so a baseline set on one pod meant
drift was computed against "no baseline" on the next request served elsewhere.

Every method is tenant-scoped through ``sqlalchemy_rls_context``; the underlying
tables use ENABLE + FORCE row level security.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


class AIOpsStore:
    """Postgres-backed store for the ai_ops_* tables."""

    def __init__(self, db_factory: Any) -> None:
        self._db = db_factory

    # ── datasets ────────────────────────────────────────────────────────────
    async def create_dataset(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        name: str,
        description: str,
        golden_tasks: list[dict[str, Any]],
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_datasets "
                    "(id, tenant_id, name, description, golden_tasks) "
                    "VALUES (:id, :t, :n, :d, CAST(:g AS jsonb))"
                ),
                {
                    "id": dataset_id,
                    "t": tenant_id,
                    "n": name,
                    "d": description,
                    "g": json.dumps(golden_tasks),
                },
            )
            await s.commit()

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT id, tenant_id, name, description, golden_tasks, "
                        "       version, created_at "
                        "FROM ai_ops_datasets WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id, "id": dataset_id},
                )
            ).fetchone()
        return _dataset_row(row) if row is not None else None

    async def list_datasets(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT id, tenant_id, name, description, golden_tasks, "
                        "       version, created_at "
                        "FROM ai_ops_datasets WHERE tenant_id = :t "
                        "ORDER BY created_at DESC"
                    ),
                    {"t": tenant_id},
                )
            ).fetchall()
        return [_dataset_row(r) for r in rows]

    # ── eval results ────────────────────────────────────────────────────────
    async def add_eval_result(
        self, *, tenant_id: str, result_id: str, dataset_id: str, payload: dict[str, Any]
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_eval_results (id, tenant_id, dataset_id, payload) "
                    "VALUES (:id, :t, :d, CAST(:p AS jsonb))"
                ),
                {
                    "id": result_id,
                    "t": tenant_id,
                    "d": dataset_id,
                    "p": json.dumps(payload),
                },
            )
            await s.commit()

    async def update_eval_result(
        self, *, tenant_id: str, result_id: str, payload: dict[str, Any]
    ) -> None:
        """Replace a result's payload (a 202 run records ``running`` then its outcome)."""
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "UPDATE ai_ops_eval_results SET payload = CAST(:p AS jsonb) "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"id": result_id, "t": tenant_id, "p": json.dumps(payload)},
            )
            await s.commit()

    async def claim_run(
        self, tenant_id: str, result_id: str, owner: str, lease_seconds: float
    ) -> dict[str, Any] | None:
        """Lease an active run to one step (P7-1); None when another step holds it.

        Compare-and-set on the run row with the DB clock, so steps on any replica
        or worker agree: the lease is taken when it is free, expired, or already
        ``owner``'s. Returns the run's payload (with the lease) to work on.
        """
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE ai_ops_eval_results SET payload = payload || "
                        "  jsonb_build_object('lease_owner', CAST(:o AS text), "
                        "    'lease_until', extract(epoch FROM clock_timestamp()) "
                        "                   + CAST(:lease AS float8)) "
                        "WHERE tenant_id = :t AND id = :id "
                        "  AND payload->>'status' IN ('queued', 'running') "
                        "  AND (COALESCE(CAST(payload->>'lease_until' AS float8), 0) "
                        "         < extract(epoch FROM clock_timestamp()) "
                        "       OR payload->>'lease_owner' = CAST(:o AS text)) "
                        "RETURNING payload"
                    ),
                    {"t": tenant_id, "id": result_id, "o": owner, "lease": float(lease_seconds)},
                )
            ).fetchone()
            await s.commit()
        return dict(row[0] or {}) if row is not None else None

    async def save_run(
        self,
        tenant_id: str,
        result_id: str,
        owner: str,
        payload: dict[str, Any],
        lease_seconds: float,
        *,
        release: bool = False,
    ) -> bool:
        """Write a run's progress, fenced on the lease (False: the lease was lost).

        Renews the lease, or frees it (``release``) when the step is done with
        the run.
        """
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "UPDATE ai_ops_eval_results SET payload = CAST(:p AS jsonb) || "
                        "  jsonb_build_object('lease_owner', CAST(:o AS text), "
                        "    'lease_until', CASE WHEN CAST(:release AS boolean) THEN 0 "
                        "      ELSE extract(epoch FROM clock_timestamp()) "
                        "           + CAST(:lease AS float8) END) "
                        "WHERE tenant_id = :t AND id = :id "
                        "  AND payload->>'lease_owner' = CAST(:o AS text) "
                        "RETURNING id"
                    ),
                    {
                        "t": tenant_id,
                        "id": result_id,
                        "o": owner,
                        "p": json.dumps(payload),
                        "lease": float(lease_seconds),
                        "release": bool(release),
                    },
                )
            ).fetchone()
            await s.commit()
        return row is not None

    async def get_eval_result(self, tenant_id: str, result_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT payload FROM ai_ops_eval_results "
                        "WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id, "id": result_id},
                )
            ).fetchone()
        return dict(row[0] or {}) if row is not None else None

    async def list_eval_results(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT payload FROM ai_ops_eval_results WHERE tenant_id = :t "
                        "ORDER BY created_at DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "k": limit},
                )
            ).fetchall()
        return [dict(r[0] or {}) for r in rows]

    async def count_eval_results(self, tenant_id: str) -> int:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM ai_ops_eval_results WHERE tenant_id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar_one()
            )

    # ── judges ──────────────────────────────────────────────────────────────
    async def create_judge(
        self, *, tenant_id: str, judge_id: str, payload: dict[str, Any]
    ) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_judges (id, tenant_id, payload) "
                    "VALUES (:id, :t, CAST(:p AS jsonb))"
                ),
                {"id": judge_id, "t": tenant_id, "p": json.dumps(payload)},
            )
            await s.commit()

    async def get_judge(self, tenant_id: str, judge_id: str) -> dict[str, Any] | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text("SELECT payload FROM ai_ops_judges WHERE tenant_id = :t AND id = :id"),
                    {"t": tenant_id, "id": judge_id},
                )
            ).fetchone()
        return dict(row[0] or {}) if row is not None else None

    # ── baselines ───────────────────────────────────────────────────────────
    async def set_baseline(self, *, tenant_id: str, metric_name: str, value: float) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_baselines (tenant_id, metric_name, value) "
                    "VALUES (:t, :m, :v) "
                    "ON CONFLICT (tenant_id, metric_name) DO UPDATE "
                    "SET value = EXCLUDED.value, updated_at = NOW()"
                ),
                {"t": tenant_id, "m": metric_name, "v": float(value)},
            )
            await s.commit()

    async def set_baseline_if_absent(
        self, *, tenant_id: str, metric_name: str, value: float
    ) -> None:
        """First-run auto-baseline: never clobber an operator-set value."""
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_baselines (tenant_id, metric_name, value) "
                    "VALUES (:t, :m, :v) ON CONFLICT (tenant_id, metric_name) DO NOTHING"
                ),
                {"t": tenant_id, "m": metric_name, "v": float(value)},
            )
            await s.commit()

    async def get_baseline(self, tenant_id: str, metric_name: str) -> float | None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT value FROM ai_ops_baselines "
                        "WHERE tenant_id = :t AND metric_name = :m"
                    ),
                    {"t": tenant_id, "m": metric_name},
                )
            ).fetchone()
        return float(row[0]) if row is not None else None

    async def count_baselines(self, tenant_id: str) -> int:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM ai_ops_baselines WHERE tenant_id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar_one()
            )

    # ── drift alerts ────────────────────────────────────────────────────────
    async def add_alert(self, *, tenant_id: str, alert: dict[str, Any]) -> None:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            await s.execute(
                text(
                    "INSERT INTO ai_ops_drift_alerts "
                    "(id, tenant_id, severity, metric_name, payload) "
                    "VALUES (:id, :t, :sev, :m, CAST(:p AS jsonb))"
                ),
                {
                    "id": alert["alert_id"],
                    "t": tenant_id,
                    "sev": alert.get("severity", "info"),
                    "m": alert.get("metric_name", ""),
                    "p": json.dumps(alert),
                },
            )
            await s.commit()

    async def list_alerts(
        self, tenant_id: str, severity: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        # CAST(...) not `:sev::text` — see the bind-parameter note
                        # in TrustApprovalStore.list; asyncpg also cannot infer the
                        # type of a bare NULL bind.
                        "SELECT payload FROM ai_ops_drift_alerts WHERE tenant_id = :t "
                        "  AND (CAST(:sev AS text) IS NULL "
                        "       OR severity = CAST(:sev AS text)) "
                        "ORDER BY created_at DESC LIMIT :k"
                    ),
                    {"t": tenant_id, "sev": severity, "k": limit},
                )
            ).fetchall()
        return [dict(r[0] or {}) for r in rows]

    async def alert_severity_counts(self, tenant_id: str) -> dict[str, int]:
        async with self._db() as s, sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT severity, count(*) FROM ai_ops_drift_alerts "
                        "WHERE tenant_id = :t GROUP BY severity"
                    ),
                    {"t": tenant_id},
                )
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}


def _dataset_row(row: Any) -> dict[str, Any]:
    tasks = list(row[4] or [])
    return {
        "dataset_id": row[0],
        "tenant_id": row[1],
        "name": row[2],
        "description": row[3],
        "golden_tasks": tasks,
        "task_count": len(tasks),
        "created_at": _iso(row[6]),
        "version": int(row[5]),
    }
