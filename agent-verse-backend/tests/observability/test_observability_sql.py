"""The observability API's SQL must reference only columns that really exist.

Regression: ``/observability/metrics`` and ``/observability/timeseries`` queried
``goals.duration_s`` and ``goals.cost_usd`` — neither column has ever existed
(see ``app/db/models/goal.py`` and the migrations), so against real Postgres every
query raised ``UndefinedColumn`` and a bare ``except: pass`` turned that into zeros.

The statements are now SQLAlchemy Core built from the ORM tables, so a missing
column fails at construction. These tests build every statement the endpoints run,
compile it for the Postgres dialect, and check each referenced column against
``Base.metadata`` — no textual SQL can slip a phantom column past this.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Column
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors

import app.db.models.goal
import app.db.models.runtime_records  # noqa: F401 — registers goal_cost_breakdowns
from app.api import observability as obs
from app.db.models import Base

_TENANT = "3f1c2a9e-5b7d-4c8e-9f0a-1b2c3d4e5f60"
_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = _SINCE + timedelta(hours=24)


def _all_statements() -> dict[str, Any]:
    stmts: dict[str, Any] = {
        "summary": obs.build_goal_summary_stmt(_TENANT, _SINCE),
        "token_usage": obs.build_token_usage_stmt(_TENANT, _SINCE - timedelta(days=30)),
    }
    for bucket in ("minute", "hour", "day"):
        stmts[f"goals_ts_{bucket}"] = obs.build_goals_timeseries_stmt(
            _TENANT, _SINCE, _UNTIL, bucket
        )
        stmts[f"latency_ts_{bucket}"] = obs.build_latency_timeseries_stmt(
            _TENANT, _SINCE, _UNTIL, bucket
        )
        stmts[f"cost_ts_{bucket}"] = obs.build_cost_timeseries_stmt(
            _TENANT, _SINCE, _UNTIL, bucket
        )
    return stmts


def _referenced_columns(stmt: Any) -> set[tuple[str, str]]:
    cols: set[tuple[str, str]] = set()
    for element in visitors.iterate(stmt):
        if isinstance(element, Column) and element.table is not None:
            cols.add((element.table.name, element.name))
    return cols


@pytest.mark.parametrize("name", sorted(_all_statements()))
def test_every_referenced_column_exists_in_orm_metadata(name: str) -> None:
    stmt = _all_statements()[name]
    referenced = _referenced_columns(stmt)
    assert referenced, f"{name}: statement references no table columns?"
    for table, column in referenced:
        assert table in Base.metadata.tables, f"{name}: unknown table {table}"
        assert column in Base.metadata.tables[table].columns, (
            f"{name}: {table}.{column} does not exist in the ORM schema"
        )


@pytest.mark.parametrize("name", sorted(_all_statements()))
def test_statements_compile_for_postgres(name: str) -> None:
    sql = str(_all_statements()[name].compile(dialect=postgresql.dialect()))
    assert "duration_s" not in sql
    assert "goals.cost_usd" not in sql


def test_goal_queries_are_tenant_filtered_and_use_real_timestamps() -> None:
    summary = str(obs.build_goal_summary_stmt(_TENANT, _SINCE).compile(
        dialect=postgresql.dialect()
    ))
    assert "goals.tenant_id = %(tenant_id_1)s" in summary
    # Duration is derived from the real terminal/creation timestamps.
    assert "goals.completed_at - goals.created_at" in summary
    assert "percentile_cont" in summary.lower()


def test_cost_queries_read_the_durable_cost_ledger() -> None:
    sql = str(
        obs.build_cost_timeseries_stmt(_TENANT, _SINCE, _UNTIL, "hour").compile(
            dialect=postgresql.dialect()
        )
    )
    assert "goal_cost_breakdowns.cost_usd" in sql
    assert "goal_cost_breakdowns.tenant_id" in sql


def test_unknown_bucket_is_rejected() -> None:
    with pytest.raises(ValueError):
        obs.build_goals_timeseries_stmt(_TENANT, _SINCE, _UNTIL, "week; DROP TABLE goals")
