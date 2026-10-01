"""The Docker sandbox path of CodeInterpreter, against a real Docker daemon.

``_execute_docker`` passed ``timeout=`` to ``client.containers.run()``. docker-py
has no such argument, so on every host where Docker is reachable — which is the
production sandbox path, the only one that is actually isolated — every single
execution failed instantly with ``run() got an unexpected keyword argument
'timeout'``. The broad ``except`` then pattern-matched "timeout" in that message
and reported ``timed_out=True``, disguising a TypeError as a slow script.

CI never noticed because it has no Docker: the module falls back to the
(unsandboxed) subprocess path and the existing tests pass there.

Even without the TypeError, ``detach=False`` gives docker-py no way to enforce a
wall-clock limit: a ``while True:`` would pin the executor thread forever. And a
non-zero exit raised ``ContainerError``, losing the program's real stderr.

Skipped automatically where Docker is unavailable.
"""

from __future__ import annotations

import time

import pytest

from app.tools import code_interpreter as ci

pytestmark = pytest.mark.skipif(
    not ci._docker_available(), reason="Docker daemon not reachable on this host"
)


@pytest.mark.asyncio
async def test_python_stdout_is_captured() -> None:
    result = await ci.CodeInterpreter(timeout=30).execute(
        "print('hello from sandbox')", "python"
    )
    assert result.success is True, f"sandbox failed: {result.stderr!r}"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert "hello from sandbox" in result.stdout


@pytest.mark.asyncio
async def test_nonzero_exit_keeps_the_programs_own_stderr() -> None:
    result = await ci.CodeInterpreter(timeout=30).execute(
        "import sys\nsys.stderr.write('boom-on-stderr\\n')\nsys.exit(3)", "python"
    )
    assert result.success is False
    assert result.exit_code == 3, f"exit code lost: {result!r}"
    assert "boom-on-stderr" in result.stderr, (
        f"the program's stderr was replaced by an exception string: {result.stderr!r}"
    )
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_runaway_code_is_killed_at_the_deadline() -> None:
    """An infinite loop must be killed at the timeout, not hang the worker."""
    t0 = time.monotonic()
    result = await ci.CodeInterpreter(timeout=3).execute("while True:\n    pass\n", "python")
    elapsed = time.monotonic() - t0

    assert result.timed_out is True
    assert result.success is False
    # Deadline plus container start/kill overhead — nowhere near "forever".
    assert elapsed < 25, f"runaway code ran {elapsed:.1f}s past a 3s timeout"


@pytest.mark.asyncio
async def test_sandbox_has_no_network() -> None:
    result = await ci.CodeInterpreter(timeout=20).execute(
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "    print('NETWORK-REACHABLE')\n"
        "except OSError:\n"
        "    print('no-network')\n",
        "python",
    )
    assert result.success is True, result.stderr
    assert "no-network" in result.stdout
    assert "NETWORK-REACHABLE" not in result.stdout


@pytest.mark.asyncio
async def test_containers_are_not_left_behind() -> None:
    import docker

    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True)}
    await ci.CodeInterpreter(timeout=20).execute("print(1)", "python")
    await ci.CodeInterpreter(timeout=3).execute("while True:\n    pass\n", "python")
    after = {c.id for c in client.containers.list(all=True)}
    assert after <= before, f"sandbox containers leaked: {after - before}"


@pytest.mark.asyncio
async def test_huge_output_is_truncated_and_bounded() -> None:
    """CODE-01: 50 MB of stdout is read live up to the cap, then the program is
    stopped; nothing close to 50 MB is held in this process."""
    import docker

    client = docker.from_env()
    before = {c.id for c in client.containers.list(all=True)}
    t0 = time.monotonic()
    result = await ci.CodeInterpreter(timeout=60).execute(
        "import sys\nchunk = 'z' * 65536\nfor _ in range(800):\n    sys.stdout.write(chunk)\n",
        "python",
    )
    assert time.monotonic() - t0 < 30, "the overflowing program was not stopped"
    assert result.timed_out is False
    assert result.success is False
    assert "Output exceeded" in result.stderr
    assert "[output truncated" in result.stdout
    assert len(result.stdout) <= ci._MAX_OUTPUT_CHARS + 200
    after = {c.id for c in client.containers.list(all=True)}
    assert after <= before, f"sandbox containers leaked: {after - before}"
