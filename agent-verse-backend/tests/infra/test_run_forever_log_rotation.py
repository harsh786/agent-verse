"""scripts/run_forever.py rotates its log by size (it reached 2.5 GB, never rotated).

The launchd plist pointed StandardOutPath at run_forever.log and every child
(API, worker, beat) inherited that descriptor, so nothing could rotate it. Now
the detached / launchd supervisor writes the file itself through a
RotatingFileHandler (default 50 MB x 5) and pipes each child's output into it.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import logging.handlers
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "run_forever.py"
MB = 1024 * 1024


@pytest.fixture(scope="module")
def rf() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("run_forever_logs_under_test", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses resolve their module by name
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT.parent))


@pytest.fixture(autouse=True)
def _reset_logger(rf: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(rf.keep_awake, "log", rf.keep_awake.log)  # restored after
    yield
    for handler in list(rf._LOGGER.handlers):
        rf._LOGGER.removeHandler(handler)
        handler.close()


def _args(**kw: Any) -> SimpleNamespace:
    return SimpleNamespace(**{"log_max_mb": None, "log_backups": None, **kw})


# ── configuration ────────────────────────────────────────────────────────────


def test_default_rotation_is_50_mb_times_5(rf: ModuleType) -> None:
    assert rf.log_rotation_settings(_args(), {}) == rf.LogRotation(50 * MB, 5)


def test_env_overrides_the_default_and_the_flag_overrides_env(rf: ModuleType) -> None:
    env = {rf.LOG_MAX_MB_ENV: "10", rf.LOG_BACKUPS_ENV: "3"}
    assert rf.log_rotation_settings(_args(), env) == rf.LogRotation(10 * MB, 3)
    flags = _args(log_max_mb=0.5, log_backups=2)
    assert rf.log_rotation_settings(flags, env) == rf.LogRotation(MB // 2, 2)


@pytest.mark.parametrize(
    ("args", "env"),
    [
        ({"log_max_mb": 0}, {}),
        ({"log_max_mb": -1}, {}),
        # backupCount=0 means RotatingFileHandler never rotates: refused.
        ({"log_backups": 0}, {}),
        ({}, {"AGENTVERSE_RUN_FOREVER_LOG_MAX_MB": "lots"}),
        ({}, {"AGENTVERSE_RUN_FOREVER_LOG_BACKUPS": "2.5"}),
    ],
)
def test_invalid_rotation_settings_are_refused(
    rf: ModuleType, args: dict[str, Any], env: dict[str, str]
) -> None:
    with pytest.raises(SystemExit):
        rf.log_rotation_settings(_args(**args), env)


def test_cli_flags_parse_and_are_forwarded_to_the_detached_supervisor(rf: ModuleType) -> None:
    args = rf.build_parser().parse_args(["start", "--log-max-mb", "20", "--log-backups", "7"])
    forwarded = rf._forwarded_run_args(args)
    i = forwarded.index("--log-file")
    assert forwarded[i + 1] == str(rf.LOG_FILE)
    assert forwarded[forwarded.index("--log-max-mb") + 1] == "20"
    assert forwarded[forwarded.index("--log-backups") + 1] == "7"
    # The forwarded `run` command line parses back to the same settings.
    run = rf.build_parser().parse_args(forwarded)
    assert rf.log_rotation_settings(run, {}) == rf.LogRotation(20 * MB, 7)
    # Without flags the env/default applies in the detached process.
    plain = rf._forwarded_run_args(rf.build_parser().parse_args(["start"]))
    assert "--log-max-mb" not in plain and "--log-backups" not in plain
    assert "--log-file" in plain


def test_launchd_plist_no_longer_points_stdout_at_the_rotated_log(
    rf: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rf, "STATE_DIR", tmp_path)
    monkeypatch.setattr(rf, "PLIST_PATH", tmp_path / "agent.plist")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(rf.subprocess, "run", fake_run)
    assert rf.cmd_install(rf.build_parser().parse_args(["install"])) == 0
    plist = plistlib.loads((tmp_path / "agent.plist").read_bytes())
    assert plist["StandardOutPath"] == str(rf.STDIO_LOG)
    assert plist["StandardErrorPath"] == str(rf.STDIO_LOG)
    argv = plist["ProgramArguments"]
    assert argv[argv.index("--log-file") + 1] == str(rf.LOG_FILE)
    assert all(c[0] == "/bin/launchctl" for c in calls)


# ── the rotating file ───────────────────────────────────────────────────────


def test_log_file_rotates_at_the_limit_and_keeps_n_backups(rf: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "run_forever.log"
    handler = rf.configure_log_file(path, rf.LogRotation(max_bytes=2000, backups=2))
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert (handler.maxBytes, handler.backupCount) == (2000, 2)
    for i in range(200):
        rf.log(f"line {i:04d} " + "x" * 80)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["run_forever.log", "run_forever.log.1", "run_forever.log.2"]
    assert all((tmp_path / f).stat().st_size <= 2000 for f in files)
    assert "line 0199" in path.read_text()


def test_an_oversized_legacy_log_is_set_aside_not_deleted(rf: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "run_forever.log"
    path.write_text("legacy\n" * 1000)  # 7000 bytes, over the 1000-byte limit
    rf.configure_log_file(path, rf.LogRotation(max_bytes=1000, backups=1))
    rf.log("fresh start")
    (archived,) = tmp_path.glob("run_forever.log.*.old")
    assert archived.read_text() == "legacy\n" * 1000
    assert "fresh start" in path.read_text()
    assert "legacy" not in path.read_text()


def test_a_log_under_the_limit_is_appended_to(rf: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "run_forever.log"
    path.write_text("earlier run\n")
    rf.configure_log_file(path, rf.LogRotation(max_bytes=MB, backups=1))
    rf.log("next run")
    assert path.read_text().startswith("earlier run\n")
    assert not list(tmp_path.glob("*.old"))


def test_keep_awake_helpers_log_into_the_file_too(rf: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "run_forever.log"
    rf.configure_log_file(path, rf.LogRotation(max_bytes=MB, backups=1))
    rf.keep_awake.log("caffeinate restarted")
    assert "caffeinate restarted" in path.read_text()


def test_child_output_is_prefixed_and_long_lines_are_split(rf: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "run_forever.log"
    rf.configure_log_file(path, rf.LogRotation(max_bytes=10 * MB, backups=1))
    long_line = b"y" * (rf._MAX_LINE_BYTES + 10)
    stream = io.BytesIO(b"INFO started\nbad \xff byte\n" + long_line + b"\n")
    rf._pump_output("worker", stream)
    text = path.read_text()
    assert "[worker] INFO started" in text
    assert "[worker] bad � byte" in text
    assert text.count("[worker] y") == 2  # split, not buffered without bound
    assert stream.closed


def test_supervised_child_output_goes_through_the_rotating_log(
    rf: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "run_forever.log"
    rf.configure_log_file(path, rf.LogRotation(max_bytes=MB, backups=1))
    assert rf.capturing_child_output()
    code = "import sys; print('to-stdout'); sys.stderr.write('to-stderr\\n')"
    service = rf._Service("api", [sys.executable, "-c", code], tmp_path)
    state = rf._RunState()
    thread = threading.Thread(
        target=rf._supervise_one, args=(service, object(), state), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        text = path.read_text()
        if "[api] to-stdout" in text and "[api] to-stderr" in text:
            break
        time.sleep(0.1)
    state.stopping = True
    thread.join(timeout=15)
    assert not thread.is_alive()
    text = path.read_text()
    assert "[api] to-stdout" in text
    assert "[api] to-stderr" in text
    assert "[api] started (child pid" in text


def test_an_older_plist_stdout_on_the_log_is_taken_over(tmp_path: Path) -> None:
    """launchd's descriptor into run_forever.log is re-pointed at the stdio file."""
    log_path = tmp_path / "run_forever.log"
    stdio = tmp_path / "run_forever.stdio.log"
    snippet = f"""
import importlib.util, sys
sys.path.insert(0, {str(SCRIPT.parent)!r})
spec = importlib.util.spec_from_file_location("rf_child", {str(SCRIPT)!r})
rf = importlib.util.module_from_spec(spec); sys.modules["rf_child"] = rf
spec.loader.exec_module(rf)
from pathlib import Path
rf.configure_log_file(Path({str(log_path)!r}), rf.LogRotation(1024 * 1024, 2),
                      stdio_path=Path({str(stdio)!r}))
rf.log("managed line")
print("stray print", flush=True)
"""
    with log_path.open("ab") as out:
        proc = subprocess.run(
            [sys.executable, "-c", snippet],
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    assert proc.returncode == 0, stdio.read_text() if stdio.exists() else ""
    assert "managed line" in log_path.read_text()
    assert "stray print" not in log_path.read_text()
    assert "stray print" in stdio.read_text()
