"""Unit tests for the API_POLL helpers (2.W-1)."""

from __future__ import annotations

from app.triggers.polling import extract_path, poll_should_fire


def test_extract_dotted_path() -> None:
    obj = {"status": {"health": "green"}}
    assert extract_path(obj, "$.status.health") == "green"
    assert extract_path(obj, "status.health") == "green"


def test_extract_list_index() -> None:
    obj = {"items": [{"id": 1}, {"id": 2}]}
    assert extract_path(obj, "items.1.id") == 2


def test_extract_missing_segment_returns_none() -> None:
    assert extract_path({"a": 1}, "a.b.c") is None
    assert extract_path({"a": 1}, "missing") is None


def test_extract_empty_path_returns_root() -> None:
    assert extract_path({"a": 1}, "") == {"a": 1}


def test_poll_fires_on_first_observation() -> None:
    assert poll_should_fire("ok", last_value=None) is True


def test_poll_dedups_unchanged_value() -> None:
    assert poll_should_fire("ok", last_value="ok") is False


def test_poll_fires_on_change() -> None:
    assert poll_should_fire("down", last_value="ok") is True


def test_poll_respects_expected_value() -> None:
    # changed but does not match expected -> no fire
    assert poll_should_fire("down", last_value="ok", expected_value="up") is False
    # changed and matches expected -> fire
    assert poll_should_fire("up", last_value="ok", expected_value="up") is True
