"""TRG-32: file_drop triggers are confined to FILE_DROP_ROOT/<tenant_id>.

``file_drop_path`` was tenant-supplied and handed to ``os.listdir`` on the worker
unchecked, so a tenant could enumerate /etc, /app or another tenant's folder
into its goal text.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import fakeredis
import pytest

from app.scaling import tasks
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import creatable_error, resolve_file_drop_dir


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app.core.config import get_settings

    drop_root = tmp_path / "drops"
    (drop_root / "t1" / "inbox").mkdir(parents=True)
    (drop_root / "t2" / "inbox").mkdir(parents=True)
    monkeypatch.setattr(get_settings(), "file_drop_root", str(drop_root))
    return drop_root


def _spec(path: str) -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.FILE_DROP, file_drop_path=path)


@pytest.mark.parametrize("path", ["/etc", "../t2/inbox", "inbox/../../t2", "~/x", "C:\\x"])
def test_escaping_paths_are_rejected_on_create(root: Path, path: str) -> None:
    reason = creatable_error(_spec(path))
    assert reason is not None and "file_drop_path" in reason


def test_a_relative_folder_is_accepted(root: Path) -> None:
    assert creatable_error(_spec("inbox")) is None
    assert resolve_file_drop_dir("t1", "inbox") == os.path.realpath(root / "t1" / "inbox")


def test_type_is_disabled_without_a_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "file_drop_root", "")
    reason = creatable_error(_spec("inbox"))
    assert reason is not None and "FILE_DROP_ROOT" in reason
    assert resolve_file_drop_dir("t1", "inbox") is None


def test_symlinked_folder_escaping_the_root_resolves_to_nothing(root: Path) -> None:
    os.symlink(root / "t2" / "inbox", root / "t1" / "peek")
    assert resolve_file_drop_dir("t1", "peek") is None


def _run_beat(monkeypatch: pytest.MonkeyPatch, sched: dict[str, Any]) -> list[dict[str, Any]]:
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set("schedule:t1:fd", json.dumps(sched))
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: r)
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )
    tasks.fire_due_schedules()
    return sent


def _sched(path: str) -> dict[str, Any]:
    return {
        "schedule_id": "fd",
        "tenant_id": "t1",
        "trigger_type": "file_drop",
        "goal_template": "Process {{payload.file_name}}",
        "file_watch_path": path,
        "file_pattern": "*",
        "paused": False,
    }


def test_beat_lists_only_inside_the_tenant_folder(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / "t1" / "inbox" / "report.csv").write_text("x")
    (root / "t2" / "inbox" / "secret.csv").write_text("x")
    # A symlinked FILE pointing outside the tenant folder is not listed either.
    os.symlink(root / "t2" / "inbox" / "secret.csv", root / "t1" / "inbox" / "leak.csv")

    sent = _run_beat(monkeypatch, _sched("inbox"))

    names = sorted(k["event_payload"]["file_name"] for k in sent)
    assert names == ["report.csv"]


def test_beat_refuses_a_stored_absolute_path(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "t2" / "inbox" / "secret.csv").write_text("x")
    assert _run_beat(monkeypatch, _sched(str(root / "t2" / "inbox"))) == []
    assert _run_beat(monkeypatch, _sched("/etc")) == []
