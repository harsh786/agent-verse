"""The code-sandbox runner (app/sandbox/runner.py), run in-process over real HTTP.

Here the runner is not root, so it runs in ``shared`` isolation mode (every
program as the test user). The per-execution UID mode, enforced memory limits,
the read-only filesystem and the absence of a network are covered against the
real image in tests/sandbox/test_runner_container.py.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.sandbox.runner import (
    EXECUTE_PATH,
    HEALTH_PATH,
    RunnerConfig,
    SandboxServer,
    main,
)

TOKEN = "test-sandbox-token-0123456789"


class _Runner:
    def __init__(self, server: SandboxServer, base: Path) -> None:
        self.server = server
        self.base = base
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        token: str | None = TOKEN,
        raw: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def run(self, code: str, *, language: str = "python", timeout: float = 20) -> dict[str, Any]:
        status, body = self.request(
            "POST",
            EXECUTE_PATH,
            {"language": language, "code": code, "timeout_seconds": timeout},
        )
        assert status == 200, body
        return body


def _start(tmp_path: Path, **overrides: Any) -> _Runner:
    base = tmp_path / "sandbox"
    cfg = RunnerConfig(token=TOKEN, host="127.0.0.1", port=0, workdir_base=str(base), **overrides)
    server = SandboxServer(cfg)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return _Runner(server, base)


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[_Runner]:
    r = _start(tmp_path, max_output_bytes=10_000, max_concurrency=2, queue_timeout_s=0.5)
    try:
        yield r
    finally:
        r.server.shutdown()
        r.server.server_close()


# ── auth & protocol ───────────────────────────────────────────────────────────


def test_health_needs_no_token(runner: _Runner) -> None:
    status, body = runner.request("GET", HEALTH_PATH, token=None)
    assert status == 200
    assert body["status"] == "ok"
    assert body["isolation"] == ("uid" if os.geteuid() == 0 else "shared")
    assert "python" in body["languages"]


@pytest.mark.parametrize("token", [None, "", "wrong-token-wrong-token", TOKEN + "x"])
def test_execute_requires_the_shared_secret(runner: _Runner, token: str | None) -> None:
    status, body = runner.request(
        "POST", EXECUTE_PATH, {"language": "python", "code": "print(1)"}, token=token
    )
    assert status == 401
    assert "token" in body["error"]


def test_basic_auth_scheme_is_not_accepted(runner: _Runner) -> None:
    req = urllib.request.Request(
        runner.url + EXECUTE_PATH,
        data=json.dumps({"language": "python", "code": "print(1)"}).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Basic {TOKEN}")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 401


def test_runs_python_and_reports_output(runner: _Runner) -> None:
    body = runner.run("import sys\nprint('hello')\nprint('oops', file=sys.stderr)")
    assert body["stdout"] == "hello\n"
    assert "oops" in body["stderr"]
    assert body["exit_code"] == 0
    assert body["timed_out"] is False
    assert body["output_truncated"] is False


def test_a_failing_program_returns_its_traceback(runner: _Runner) -> None:
    body = runner.run("raise ValueError('boom')")
    assert body["exit_code"] == 1
    assert "ValueError: boom" in body["stderr"]
    assert 'File "<code>", line 1' in body["stderr"]


def test_bash_runs(runner: _Runner) -> None:
    body = runner.run("echo $((6 * 7))", language="bash")
    assert body["stdout"].strip() == "42"


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"language": "cobol", "code": "x"}, 400),
        ({"language": "python"}, 400),
        ({"language": "python", "code": 1}, 400),
        ({"language": "python", "code": "1", "timeout_seconds": -1}, 400),
        ({"language": "python", "code": "1", "timeout_seconds": "30"}, 400),
        (["not", "an", "object"], 400),
    ],
)
def test_bad_requests_are_refused(runner: _Runner, payload: Any, status: int) -> None:
    got, body = runner.request("POST", EXECUTE_PATH, payload)
    assert got == status, body


def test_malformed_json_is_refused(runner: _Runner) -> None:
    status, _ = runner.request("POST", EXECUTE_PATH, raw=b"{not json")
    assert status == 400


def test_oversized_request_is_refused_before_it_is_read(tmp_path: Path) -> None:
    import http.client

    r = _start(tmp_path, max_code_bytes=1000)
    try:
        # Only the headers are sent: the runner answers from Content-Length alone.
        conn = http.client.HTTPConnection("127.0.0.1", r.server.server_address[1], timeout=10)
        conn.putrequest("POST", EXECUTE_PATH)
        conn.putheader("Authorization", f"Bearer {TOKEN}")
        conn.putheader("Content-Length", str(50 * 1024 * 1024))
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 413
        assert "1000" in json.loads(resp.read())["error"]
        conn.close()
        # A body within the transport limit but with too much code is refused too.
        status, _ = r.request("POST", EXECUTE_PATH, {"language": "python", "code": "#" * 5000})
        assert status == 413
    finally:
        r.server.shutdown()
        r.server.server_close()


# ── isolation ─────────────────────────────────────────────────────────────────


def test_program_environment_carries_no_secrets(
    runner: _Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Whatever the runner process has in its environment, a program sees none of it.
    monkeypatch.setenv("CODE_SANDBOX_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:hunter2@db/x")
    monkeypatch.setenv("VAULT_MASTER_KEY", "super-secret-vault-key")
    body = runner.run("import json, os\nprint(json.dumps(dict(os.environ)))")
    env = json.loads(body["stdout"])
    assert not {"CODE_SANDBOX_TOKEN", "DATABASE_URL", "VAULT_MASTER_KEY"} & set(env)
    assert TOKEN not in body["stdout"]
    assert "hunter2" not in body["stdout"]
    expected = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
    }
    # macOS adds __CF_USER_TEXT_ENCODING to every process on its own.
    assert set(env) - {"__CF_USER_TEXT_ENCODING"} == expected


def test_program_runs_in_a_private_workdir_that_is_removed(runner: _Runner) -> None:
    body = runner.run(
        "import os, stat\n"
        "open('out.txt', 'w').write('x')\n"
        "print(os.getcwd())\n"
        "print(oct(stat.S_IMODE(os.stat('.').st_mode)))\n"
        "print(os.environ['HOME'] == os.getcwd())"
    )
    cwd, mode, home_is_cwd = body["stdout"].split()
    assert Path(cwd).parent.resolve() == runner.base.resolve()
    assert mode == "0o700"
    assert home_is_cwd == "True"
    assert not Path(cwd).exists()  # removed after the run
    assert list(runner.base.iterdir()) == []


def test_concurrent_programs_get_distinct_workdirs(runner: _Runner) -> None:
    results: list[dict[str, Any]] = []

    def go() -> None:
        results.append(runner.run("import os, time\ntime.sleep(0.5)\nprint(os.getcwd())"))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dirs = {r["stdout"].strip() for r in results}
    assert len(dirs) == 2


def test_rlimits_are_applied_to_the_program(runner: _Runner) -> None:
    body = runner.run(
        "import resource as r\n"
        "for n in ('RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE', 'RLIMIT_CORE'):\n"
        "    print(n, *r.getrlimit(getattr(r, n)))",
        timeout=5,
    )
    limits = {
        name: (int(soft), int(hard))
        for name, soft, hard in (line.split() for line in body["stdout"].splitlines())
    }
    cfg = runner.server.config
    assert limits["RLIMIT_CPU"] == (6, 6)  # ceil(timeout) + 1, soft == hard
    assert limits["RLIMIT_FSIZE"] == (cfg.max_file_mb * 1024 * 1024,) * 2
    assert limits["RLIMIT_NOFILE"] == (cfg.max_open_files,) * 2
    assert limits["RLIMIT_CORE"] == (0, 0)


def test_program_cannot_raise_its_own_limits(runner: _Runner) -> None:
    body = runner.run(
        "import resource as r\n"
        "try:\n"
        "    r.setrlimit(r.RLIMIT_CPU, (1000, 1000))\n"
        "    print('raised')\n"
        "except (ValueError, OSError):\n"
        "    print('refused')",
        timeout=5,
    )
    assert body["stdout"].strip() == "refused"


def test_file_size_limit_stops_a_huge_write(tmp_path: Path) -> None:
    r = _start(tmp_path, max_file_mb=1)
    try:
        body = r.run(
            "try:\n"
            "    open('big', 'wb').write(b'x' * (4 * 1024 * 1024))\n"
            "    print('wrote')\n"
            "except OSError as e:\n"
            "    print('refused', e.errno)"
        )
        assert body["stdout"].startswith("refused"), body
    finally:
        r.server.shutdown()
        r.server.server_close()


# ── limits that stop the program ──────────────────────────────────────────────


def test_timeout_kills_a_busy_loop(runner: _Runner) -> None:
    t0 = time.monotonic()
    body = runner.run("while True:\n    pass", timeout=1)
    assert body["timed_out"] is True
    assert body["exit_code"] == 124
    assert "exceeded 1s" in body["stderr"]
    assert time.monotonic() - t0 < 10


def test_timeout_kills_a_sleeper_and_its_children(runner: _Runner) -> None:
    t0 = time.monotonic()
    body = runner.run("sleep 60 & sleep 60 & wait", language="bash", timeout=1)
    assert body["timed_out"] is True
    assert time.monotonic() - t0 < 10


def test_background_processes_do_not_outlive_the_program(runner: _Runner) -> None:
    t0 = time.monotonic()
    body = runner.run("sleep 60 &\necho started", language="bash")
    assert body["stdout"].strip() == "started"
    assert body["timed_out"] is False
    assert time.monotonic() - t0 < 10  # the background sleep did not hold the pipe open


def test_output_is_capped_and_the_program_stopped(runner: _Runner) -> None:
    t0 = time.monotonic()
    body = runner.run("while True:\n    print('x' * 1000)", timeout=30)
    assert body["output_truncated"] is True
    assert len(body["stdout"]) == 10_000
    assert "Output exceeded 10000 bytes" in body["stderr"]
    assert body["timed_out"] is False
    assert time.monotonic() - t0 < 10  # killed at the cap, not at the timeout


def test_timeout_is_clamped_to_the_configured_maximum(tmp_path: Path) -> None:
    r = _start(tmp_path, max_timeout_s=1)
    try:
        body = r.run("import time\ntime.sleep(30)", timeout=600)
        assert body["timed_out"] is True
    finally:
        r.server.shutdown()
        r.server.server_close()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_memory_limit_applies(tmp_path: Path) -> None:
    r = _start(tmp_path, memory_mb=128)
    try:
        body = r.run("b = bytearray(512 * 1024 * 1024)\nprint('allocated')")
        assert body["exit_code"] != 0
        assert "MemoryError" in body["stderr"]
    finally:
        r.server.shutdown()
        r.server.server_close()


def test_busy_runner_answers_429(tmp_path: Path) -> None:
    r = _start(tmp_path, max_concurrency=1, queue_timeout_s=0.2)
    try:
        holder = threading.Thread(target=r.run, args=("import time\ntime.sleep(2)",))
        holder.start()
        time.sleep(0.5)
        status, body = r.request("POST", EXECUTE_PATH, {"language": "python", "code": "print(1)"})
        holder.join()
        assert status == 429
        assert body["limit"] == 1
        # ...and the slot is usable again once the first program finished.
        assert r.run("print(2)")["stdout"] == "2\n"
    finally:
        r.server.shutdown()
        r.server.server_close()


# ── configuration ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("token", ["", "short"])
def test_runner_refuses_to_start_without_a_strong_token(token: str) -> None:
    with pytest.raises(ValueError, match="CODE_SANDBOX_TOKEN"):
        RunnerConfig(token=token)


def test_main_exits_non_zero_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_SANDBOX_TOKEN", raising=False)
    assert main() == 2


def test_config_from_env(tmp_path: Path) -> None:
    cfg = RunnerConfig.from_env(
        {
            "CODE_SANDBOX_TOKEN": TOKEN,
            "CODE_SANDBOX_PORT": "9999",
            "CODE_SANDBOX_WORKDIR": str(tmp_path),
            "CODE_SANDBOX_MAX_CONCURRENCY": "3",
            "CODE_SANDBOX_MEMORY_MB": "512",
        }
    )
    assert (cfg.port, cfg.workdir_base, cfg.max_concurrency, cfg.memory_mb) == (
        9999,
        str(tmp_path),
        3,
        512,
    )
    assert TOKEN not in repr(cfg)
