"""The code-sandbox runner in its real image, with the deployment's hardening.

Builds ``Dockerfile.sandbox`` (``python:3.12-slim`` + the runner; the base image
must already be local — the build never pulls) and runs it the way compose and
the Helm charts do: root with only SETUID/SETGID/KILL, no-new-privileges,
read-only root filesystem, tmpfs workdirs — and no network (``network_mode:
none``; compose uses an internal network and Kubernetes a deny-all-egress
NetworkPolicy). Requests are sent from inside the container to 127.0.0.1, so the
test needs no published port.

Needs Docker (colima: DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock).
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
TOKEN = "container-sandbox-token-0123456789"
BASE_IMAGE = "python:3.12-slim"

# Sent with `docker exec` inside the runner's container.
_CLIENT = r"""
import json, sys, urllib.request, urllib.error
method, path, token, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
req = urllib.request.Request("http://127.0.0.1:8080" + path, method=method,
                             data=body.encode() if body else None)
if token:
    req.add_header("Authorization", "Bearer " + token)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print(json.dumps({"status": r.status, "body": json.loads(r.read())}))
except urllib.error.HTTPError as e:
    print(json.dumps({"status": e.code, "body": json.loads(e.read() or b"{}")}))
"""


def _docker() -> Any:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get(BASE_IMAGE)
        return client
    except Exception as exc:
        pytest.skip(f"needs Docker with {BASE_IMAGE} present locally: {exc}")


class _Sandbox:
    def __init__(self, container: Any) -> None:
        self.container = container

    def call(
        self, method: str, path: str, body: Any = None, token: str | None = TOKEN
    ) -> tuple[int, dict[str, Any]]:
        res = self.container.exec_run(
            [
                "python",
                "-c",
                _CLIENT,
                method,
                path,
                token or "",
                "" if body is None else json.dumps(body),
            ]
        )
        assert res.exit_code == 0, res.output.decode(errors="replace")
        out = json.loads(res.output.decode().strip().splitlines()[-1])
        return int(out["status"]), dict(out["body"])

    def run(self, code: str, *, language: str = "python", timeout: float = 20) -> dict[str, Any]:
        status, body = self.call(
            "POST", "/v1/execute", {"language": language, "code": code, "timeout_seconds": timeout}
        )
        assert status == 200, body
        return body


@pytest.fixture(scope="module")
def sandbox() -> Iterator[_Sandbox]:
    client = _docker()
    tag = f"agentverse-code-sandbox-test:{uuid.uuid4().hex[:12]}"
    client.images.build(
        path=str(BACKEND), dockerfile="Dockerfile.sandbox", tag=tag, rm=True, pull=False
    )
    container = None
    try:
        container = client.containers.run(
            tag,
            detach=True,
            user="0:0",
            environment={
                "CODE_SANDBOX_TOKEN": TOKEN,
                "CODE_SANDBOX_MAX_CONCURRENCY": "2",
                "CODE_SANDBOX_MEMORY_MB": "128",
                "CODE_SANDBOX_MAX_PROCESSES": "16",
            },
            read_only=True,
            tmpfs={
                "/sandbox": "size=64m,mode=1733,noexec,nosuid,nodev",
                "/tmp": "size=16m,mode=1777,noexec,nosuid,nodev",
            },
            cap_drop=["ALL"],
            cap_add=["SETUID", "SETGID", "KILL"],
            security_opt=["no-new-privileges"],
            network_mode="none",
            pids_limit=256,
            mem_limit="768m",
        )
        sb = _Sandbox(container)
        import time

        deadline = time.monotonic() + 30
        while True:
            try:
                status, health = sb.call("GET", "/healthz", token=None)
                if status == 200:
                    break
            except (AssertionError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise AssertionError(container.logs().decode(errors="replace")[-2000:])
            time.sleep(0.5)
        yield sb
    finally:
        if container is not None:
            container.remove(force=True)
        client.images.remove(tag, force=True)


def test_runner_uses_per_execution_uids(sandbox: _Sandbox) -> None:
    _status, health = sandbox.call("GET", "/healthz", token=None)
    assert health["isolation"] == "uid"
    body = sandbox.run("import os\nprint(os.getuid(), os.getgid(), os.getgroups())")
    uid, gid, groups = body["stdout"].split(maxsplit=2)
    assert int(uid) >= 20000 and int(gid) == int(uid)
    assert groups.strip() == "[]"


def test_token_is_required(sandbox: _Sandbox) -> None:
    status, _ = sandbox.call("POST", "/v1/execute", {"language": "python", "code": "1"}, None)
    assert status == 401
    status, _ = sandbox.call(
        "POST", "/v1/execute", {"language": "python", "code": "1"}, "not-the-token-at-all"
    )
    assert status == 401


def test_program_cannot_read_the_runner_secret_or_files(sandbox: _Sandbox) -> None:
    body = sandbox.run(
        "import os\n"
        "print('env', 'CODE_SANDBOX_TOKEN' in os.environ)\n"
        "for path in ('/proc/1/environ', '/proc/1/mem', '/opt/code-sandbox/runner.py'):\n"
        "    try:\n"
        "        open(path, 'rb').read(64)\n"
        "        print(path, 'READ')\n"
        "    except OSError as e:\n"
        "        print(path, 'denied', type(e).__name__)\n"
    )
    assert TOKEN not in body["stdout"]
    assert "env False" in body["stdout"]
    assert "READ" not in body["stdout"], body["stdout"]


def test_program_cannot_write_outside_its_workdir(sandbox: _Sandbox) -> None:
    body = sandbox.run(
        "import os\n"
        "open('mine.txt', 'w').write('ok')\n"
        "print('workdir ok', os.getcwd().startswith('/sandbox/run-'))\n"
        "for path in ('/etc/pwned', '/opt/pwned', '/pwned'):\n"
        "    try:\n"
        "        open(path, 'w').write('x')\n"
        "        print(path, 'WROTE')\n"
        "    except OSError as e:\n"
        "        print(path, 'denied')\n"
        "try:\n"
        "    os.listdir('/sandbox')\n"
        "    print('LISTED /sandbox')\n"
        "except OSError:\n"
        "    print('cannot list /sandbox')\n"
    )
    out = body["stdout"]
    assert "workdir ok True" in out
    for path in ("/etc/pwned", "/opt/pwned", "/pwned"):
        assert f"{path} denied" in out, out
    # /sandbox itself: an execution user may create an entry there (that is how
    # its workdir comes to be) but cannot list it, so siblings cannot be found.
    assert "cannot list /sandbox" in out


def test_concurrent_executions_cannot_see_or_signal_each_other(sandbox: _Sandbox) -> None:
    victim_result: dict[str, Any] = {}

    def victim() -> None:
        victim_result.update(
            sandbox.run(
                "import os, time\n"
                "open('secret.txt', 'w').write('victim-secret')\n"
                "time.sleep(4)\n"
                "print('victim done', os.getuid())",
                timeout=20,
            )
        )

    t = threading.Thread(target=victim)
    t.start()
    import time

    time.sleep(1.5)
    attacker = sandbox.run(
        "import os, signal\n"
        "me = os.getpid()\n"
        "seen = []\n"
        "for pid in os.listdir('/proc'):\n"
        "    if not pid.isdigit() or int(pid) == me:\n"
        "        continue\n"
        "    for leaf in ('cwd', 'root'):\n"
        "        try:\n"
        "            target = os.readlink(f'/proc/{pid}/{leaf}')\n"
        "            for name in os.listdir(target):\n"
        "                if name == 'secret.txt':\n"
        "                    seen.append(open(os.path.join(target, name)).read())\n"
        "        except OSError:\n"
        "            pass\n"
        "    try:\n"
        "        seen.append(open(f'/proc/{pid}/environ').read())\n"
        "    except OSError:\n"
        "        pass\n"
        "print('seen', 'victim-secret' in ''.join(seen))\n"
        "try:\n"
        "    os.kill(-1, signal.SIGKILL)\n"
        "except OSError:\n"
        "    pass\n"
        "print('attacker done')",
    )
    t.join()
    assert "seen False" in attacker["stdout"], attacker
    assert victim_result["exit_code"] == 0, victim_result
    assert "victim done" in victim_result["stdout"]


def test_no_network_egress(sandbox: _Sandbox) -> None:
    body = sandbox.run(
        "import socket\n"
        "for host, port in (('1.1.1.1', 53), ('8.8.8.8', 443)):\n"
        "    s = socket.socket()\n"
        "    s.settimeout(3)\n"
        "    try:\n"
        "        s.connect((host, port))\n"
        "        print(host, 'CONNECTED')\n"
        "    except OSError as e:\n"
        "        print(host, 'blocked')\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 443)\n"
        "    print('dns RESOLVED')\n"
        "except OSError:\n"
        "    print('dns blocked')\n"
    )
    assert "CONNECTED" not in body["stdout"], body
    assert "RESOLVED" not in body["stdout"], body


def test_memory_limit_applies(sandbox: _Sandbox) -> None:
    body = sandbox.run("b = bytearray(512 * 1024 * 1024)\nprint('allocated')")
    assert "allocated" not in body["stdout"]
    assert "MemoryError" in body["stderr"]
    assert body["exit_code"] == 1


def test_fork_bomb_is_bounded_per_execution(sandbox: _Sandbox) -> None:
    body = sandbox.run(
        "import os, time\n"
        "n = 0\n"
        "for _ in range(500):\n"
        "    try:\n"
        "        pid = os.fork()\n"
        "    except OSError:\n"
        "        break\n"
        "    if pid == 0:\n"
        "        time.sleep(60)\n"
        "        os._exit(0)\n"
        "    n += 1\n"
        "print('forked', n)",
        timeout=20,
    )
    forked = int(body["stdout"].split()[1])
    assert forked < 16, body  # CODE_SANDBOX_MAX_PROCESSES=16 per UID


def test_timeout_kills_and_escaped_processes_do_not_survive(sandbox: _Sandbox) -> None:
    body = sandbox.run(
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()  # leave the runner's process group\n"
        "    time.sleep(300)\n"
        "    os._exit(0)\n"
        "while True:\n"
        "    pass",
        timeout=2,
    )
    assert body["timed_out"] is True and body["exit_code"] == 124
    # Whatever slot runs next: no process of any execution UID may still be alive
    # (the runner kills every process of a slot's UID before reusing it) — not
    # even as a zombie for long (the runner, PID 1, reaps what it adopts).
    probe = (
        "import os\n"
        "me = os.getpid()\n"
        "alive, zombies = [], []\n"
        "for pid in os.listdir('/proc'):\n"
        "    if pid.isdigit() and int(pid) != me:\n"
        "        try:\n"
        "            status = open(f'/proc/{pid}/status').read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        uid = int(status.split('Uid:')[1].split()[0])\n"
        "        state = status.split('State:')[1].split()[0]\n"
        "        if uid >= 20000:\n"
        "            (zombies if state == 'Z' else alive).append(pid)\n"
        "print(len(alive), len(zombies))"
    )
    import time

    alive = zombies = -1
    for _ in range(10):
        alive, zombies = map(int, sandbox.run(probe)["stdout"].split())
        assert alive == 0, "a process of an execution UID outlived its run"
        if zombies == 0:
            break
        time.sleep(0.5)
    assert zombies == 0, "orphaned zombies are not reaped"


def test_output_cap_and_cleanup_in_the_image(sandbox: _Sandbox) -> None:
    body = sandbox.run("import sys\nsys.stdout.write('y' * 3_000_000)")
    assert body["output_truncated"] is True
    assert len(body["stdout"]) == 1_000_000
    _status, health = sandbox.call("GET", "/healthz", token=None)
    assert health["slots_quarantined"] == 0


def test_workdirs_are_removed(sandbox: _Sandbox) -> None:
    sandbox.run("open('leftover', 'w').write('x')")
    res = sandbox.container.exec_run(["python", "-c", "import os; print(os.listdir('/sandbox'))"])
    assert res.output.decode().strip() == "[]"
