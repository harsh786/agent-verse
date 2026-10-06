"""Integration (a06-F099-03): the dead-worker restorer on a real Redis broker.

Real kombu Redis transport (ack emulation: the ``unacked`` hash and
``unacked_index``), the real Lua scripts, and -- in the last test -- a real
``celery worker`` subprocess that is SIGKILLed while it holds a message.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/scaling/test_dead_worker_restore_redis_integration.py -m integration
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.scaling import dead_worker_restore as dwr

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
QUEUE = "dwr.test"


@pytest.fixture
def broker(redis_url: str) -> Iterator[str]:
    import redis

    client = redis.Redis.from_url(redis_url)
    client.flushdb()
    yield redis_url
    client.flushdb()
    client.close()


def _raw(url: str) -> Any:
    import redis

    return redis.Redis.from_url(url)


def _publish(url: str, body: dict[str, Any], task: str) -> None:
    from kombu import Connection, Exchange, Queue  # type: ignore[import-untyped]

    queue = Queue(QUEUE, Exchange(QUEUE, type="direct"), routing_key=QUEUE)
    with Connection(url) as conn:
        conn.Producer().publish(
            body, exchange=queue.exchange, routing_key=QUEUE, declare=[queue],
            headers={"task": task}, serializer="json",
        )


class _Worker:
    """A kombu consumer that takes one message and never acks it."""

    def __init__(self, url: str) -> None:
        from kombu import Connection, Consumer, Exchange, Queue

        self.conn = Connection(url)
        self.channel = self.conn.channel()
        self.messages: list[Any] = []
        queue = Queue(QUEUE, Exchange(QUEUE, type="direct"), routing_key=QUEUE)
        self.consumer = Consumer(
            self.channel, queues=[queue], callbacks=[self._on], accept=["json"]
        )
        self.consumer.qos(prefetch_count=1)
        self.consumer.consume()

    def _on(self, _body: Any, message: Any) -> None:
        self.messages.append(message)

    def take_one(self) -> Any:
        deadline = time.time() + 10
        while not self.messages and time.time() < deadline:
            self.conn.drain_events(timeout=5)
        assert self.messages, "no message delivered"
        return self.messages[-1]

    def die(self) -> None:
        """Hard death: the connection goes away without restoring or acking."""
        self.channel.qos.restore_at_shutdown = False
        self.conn.release()


def _state(url: str) -> tuple[Any, dwr.RedisBrokerState]:
    from kombu import Connection  # type: ignore[import-untyped]

    conn = Connection(url)
    state = dwr.state_for_channel(conn.default_channel)
    assert state is not None
    return conn, state


def _wait(cond: Callable[[], Any], timeout: float, what: str) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def test_dead_workers_message_is_restored_and_a_live_workers_is_untouched(broker: str) -> None:
    raw = _raw(broker)
    _publish(broker, {"n": "dead"}, "workflow.deliver_workflow_callback")
    _publish(broker, {"n": "live"}, "agentverse.training_export.run")

    dead, live = _Worker(broker), _Worker(broker)
    dead_msg, live_msg = dead.take_one(), live.take_one()
    assert {dead_msg.payload["n"], live_msg.payload["n"]} == {"dead", "live"}
    assert raw.hlen("unacked") == 2 and raw.llen(QUEUE) == 0

    _, dead_state = _state(broker)
    _, live_state = _state(broker)
    dead_state.heartbeat("dead@host/1/a", ttl_seconds=1.0)  # expires on its own
    assert dead_state.record_owner(dead_msg.delivery_tag, "dead@host/1/a")
    live_state.heartbeat("live@host/2/b", ttl_seconds=60.0)
    assert live_state.record_owner(live_msg.delivery_tag, "live@host/2/b")

    conn, state = _state(broker)
    # Both alive: nothing moves.
    report = dwr.restore_dead_worker_messages(state, grace_seconds=1.0)
    assert report.restored == [] and report.skipped_live == 2

    dead.die()
    _wait(lambda: not raw.exists("agentverse:celery:worker_alive:dead@host/1/a"), 5, "expiry")
    time.sleep(1.1)  # the message is older than the grace too

    report = dwr.restore_dead_worker_messages(state, grace_seconds=1.0)
    assert [r["tag"] for r in report.restored] == [dead_msg.delivery_tag]
    assert report.skipped_live == 1
    assert raw.llen(QUEUE) == 1
    assert raw.hexists("unacked", live_msg.delivery_tag)
    assert not raw.hexists("unacked", dead_msg.delivery_tag)
    assert raw.hget("agentverse:celery:unacked_owner", live_msg.delivery_tag) == b"live@host/2/b"
    assert not raw.hexists("agentverse:celery:unacked_owner", dead_msg.delivery_tag)
    assert raw.hget("agentverse:celery:dead_worker_restore_stats", "restored") == b"1"

    # A new worker gets it, marked redelivered, and acks it.
    again = _Worker(broker)
    msg = again.take_one()
    assert msg.payload == {"n": "dead"}
    assert msg.delivery_info.get("redelivered") is True
    assert msg.headers["task"] == "workflow.deliver_workflow_callback"
    msg.ack()
    assert not raw.hexists("unacked", msg.delivery_tag)

    # Second tick: nothing more to do; the live worker's message still held.
    assert dwr.restore_dead_worker_messages(state, grace_seconds=1.0).restored == []
    assert raw.hexists("unacked", live_msg.delivery_tag) and raw.llen(QUEUE) == 0
    live_msg.ack()
    again.conn.release()
    live.conn.release()
    conn.release()


def test_concurrent_restorers_push_the_message_exactly_once(broker: str) -> None:
    raw = _raw(broker)
    for i in range(20):
        _publish(broker, {"i": i}, "workflow.deliver_workflow_callback")
    tags: list[str] = []
    workers = []
    for _ in range(20):
        w = _Worker(broker)
        tags.append(w.take_one().delivery_tag)
        workers.append(w)
    _, rec = _state(broker)
    for tag in tags:
        assert rec.record_owner(tag, "gone/1/x")  # never beat: dead
    for w in workers:
        w.die()
    time.sleep(0.2)

    reports: list[dwr.RestoreReport] = []
    lock = threading.Lock()

    def run() -> None:
        conn, state = _state(broker)
        try:
            r = dwr.restore_dead_worker_messages(state, grace_seconds=0.1)
        finally:
            conn.release()
        with lock:
            reports.append(r)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sum(len(r.restored) for r in reports) == 20
    assert raw.llen(QUEUE) == 20
    assert raw.hlen("unacked") == 0 and raw.zcard("unacked_index") == 0
    assert raw.hlen("agentverse:celery:unacked_owner") == 0


def test_kombus_own_restore_and_ours_never_double_push(broker: str) -> None:
    """kombu's visibility-timeout restore (restore_by_tag) racing ours: one copy."""
    raw = _raw(broker)
    _publish(broker, {"n": 1}, "workflow.deliver_workflow_callback")
    w = _Worker(broker)
    msg = w.take_one()
    _, rec = _state(broker)
    rec.record_owner(msg.delivery_tag, "gone/1/y")
    w.die()
    conn, state = _state(broker)
    conn.default_channel.qos.restore_by_tag(msg.delivery_tag)  # kombu's backstop first
    report = dwr.restore_dead_worker_messages(state, grace_seconds=0.0)
    assert report.restored == []
    assert raw.llen(QUEUE) == 1
    assert raw.hlen("agentverse:celery:unacked_owner") == 0  # stale record swept
    conn.release()


def test_owner_is_recorded_only_while_the_message_is_unacked(broker: str) -> None:
    raw = _raw(broker)
    _publish(broker, {"n": 1}, "workflow.deliver_workflow_callback")
    w = _Worker(broker)
    msg = w.take_one()
    msg.ack()
    _, state = _state(broker)
    assert not state.record_owner(msg.delivery_tag, "late/1/z")
    assert raw.hlen("agentverse:celery:unacked_owner") == 0
    w.conn.release()


def _celery_worker(broker: str, name: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "DWR_TEST_BROKER": broker,
        "CELERY_DEAD_WORKER_RESTORE_ENABLED": "true",
        "CELERY_DEAD_WORKER_GRACE_SECONDS": "10",
        "PYTHONPATH": str(BACKEND_ROOT),
    }
    return subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "tests.scaling._dead_worker_celery_app",
            "worker", "-P", "solo", "-c", "1", "-Q", "dwr.celery", "-n", f"{name}@%h",
            "--without-heartbeat", "--without-gossip", "--without-mingle",
            "--loglevel=WARNING",
        ],
        cwd=BACKEND_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


def _kill(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGKILL)
    proc.wait(10)


def test_sigkilled_celery_worker_message_is_restored_and_runs_on_the_next(
    broker: str,
) -> None:
    from celery import Celery  # type: ignore[import-untyped]

    raw = _raw(broker)
    client = Celery("dwr_client", broker=broker)
    client.conf.task_default_queue = "dwr.celery"
    owner_key = "agentverse:celery:unacked_owner"

    a = _celery_worker(broker, "a")
    b: subprocess.Popen[bytes] | None = None
    try:
        client.send_task("dwr.wait_for", args=["dwr:release", "dwr:done"])
        owners = _wait(lambda: raw.hgetall(owner_key), 60, "worker A to own the message")
        ((tag, inst_a),) = owners.items()
        tag = tag.decode()
        assert inst_a.decode().startswith("a@")
        assert raw.exists(f"agentverse:celery:worker_alive:{inst_a.decode()}")

        conn, state = _state(broker)
        live = dwr.restore_dead_worker_messages(state, grace_seconds=10)
        assert live.restored == [] and live.skipped_live == 1  # alive: untouched

        _kill(a)
        assert raw.hexists("unacked", tag) and raw.llen("dwr.celery") == 0

        def restored() -> bool:
            return bool(dwr.restore_dead_worker_messages(state, grace_seconds=10).restored)

        _wait(restored, 40, "the restorer to see worker A dead")
        assert raw.llen("dwr.celery") == 1 and not raw.hexists("unacked", tag)
        conn.release()

        b = _celery_worker(broker, "b")
        owners = _wait(lambda: raw.hgetall(owner_key), 60, "worker B to own the message")
        ((tag_b, inst_b),) = owners.items()
        assert tag_b.decode() == tag and inst_b.decode().startswith("b@")
        raw.set("dwr:release", 1)
        _wait(lambda: raw.exists("dwr:done"), 30, "the task to finish on worker B")
        _wait(lambda: not raw.hexists("unacked", tag), 10, "worker B to ack")
    finally:
        _kill(a)
        if b is not None:
            _kill(b)
