"""2.W-1 / DB_ROW_CHANGE: safe, allowlisted table polling (approved design)."""

from __future__ import annotations

from app.scaling.tasks import _row_change_fires, _safe_db_table


def test_safe_table_requires_allowlist_membership() -> None:
    allow = frozenset({"orders", "tickets"})
    assert _safe_db_table("orders", allow) is True
    assert _safe_db_table("secrets", allow) is False  # not allowlisted


def test_safe_table_rejects_injection_and_bad_identifiers() -> None:
    allow = frozenset({"orders"})
    # SQL-injection / qualified / spaced names never pass, even if a fragment is listed.
    assert _safe_db_table("orders; DROP TABLE users", allow) is False
    assert _safe_db_table("public.orders", allow) is False
    assert _safe_db_table("orders --", allow) is False
    assert _safe_db_table("", allow) is False
    assert _safe_db_table("Orders", allow) is False  # uppercase not an allowed identifier here


def test_row_change_first_observation_sets_baseline() -> None:
    # No prior count → establish baseline, do not fire.
    assert _row_change_fires(5, None) is False


def test_row_change_fires_only_on_growth() -> None:
    assert _row_change_fires(6, 5) is True
    assert _row_change_fires(5, 5) is False
    assert _row_change_fires(4, 5) is False  # deletions do not fire


def test_allowlist_parsing_from_settings(monkeypatch) -> None:
    from app.scaling import tasks

    class _S:
        db_row_change_tables = " orders , tickets ,, "

    monkeypatch.setattr(tasks, "get_settings", lambda: _S(), raising=False)
    # _db_row_change_allowlist imports get_settings from app.core.config inside the
    # function, so patch there instead.
    monkeypatch.setattr("app.core.config.get_settings", lambda: _S())
    assert tasks._db_row_change_allowlist() == frozenset({"orders", "tickets"})
