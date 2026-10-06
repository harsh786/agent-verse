"""Liveness-aware restore of Celery messages left unacked by dead workers (a06-F099-03).

The problem
-----------
Tasks are ``acks_late``. kombu's Redis transport emulates acks: a message a
worker took is kept in the ``unacked`` hash (``tag -> [message, exchange,
routing_key]``) and the ``unacked_index`` sorted set (``tag -> time taken``)
until the worker acks it. kombu puts it back on its queue only once it is older
than the transport's ONE ``visibility_timeout`` -- sized for the longest goal
(~25 h, ``celery_app._goal_visibility_timeout_s``). When a whole worker dies
(SIGKILL, OOM-kill of the main process, lost node) its messages wait out those
25 h. A dead prefork *child* is already handled (``task_reject_on_worker_lost``).

kombu stores no owner in ``unacked``: nothing says which worker holds a
message. So ownership is recorded here:

* **Worker side** (``WorkerLiveness``, wired by :func:`connect_worker_liveness`).
  Each worker main process gets an instance id (``hostname/pid/nonce`` -- unique
  per process start, so a worker restarted under the same hostname is a *new*
  instance and never makes its predecessor look alive). A daemon thread sets
  ``agentverse:celery:worker_alive:<instance>`` with a TTL of the grace period
  every grace / 4 s. On ``task_received`` (main process, after kombu has put the
  message into ``unacked``) the worker records ``tag -> instance`` in the
  ``agentverse:celery:unacked_owner`` hash, only while the tag is still unacked.
* **Restorer** (:func:`restore_dead_worker_messages`, beat task
  ``restore-dead-worker-messages`` every 60 s on ``maintenance``, single-flight
  via ``beat_task_guard``). For each owner record whose instance's alive key is
  gone (no heartbeat for the grace period -- about four missed beats) and whose
  message has been unacked for at least the grace period, one Lua script --
  what kombu's ``restore_by_tag`` does, for that tag only -- re-checks
  everything atomically (instance still dead, owner record unchanged, the
  unacked entry byte-for-byte the one read) and then removes the tag from
  ``unacked`` / ``unacked_index`` / the owner hash and pushes the message (marked
  ``redelivered``) back onto the head of its queue. A message is restored at
  most once: the second attempt, or kombu's own visibility-timeout restore, finds
  the tag gone. Owner records of acked messages are deleted (compare-and-delete).

Never touched: messages of a live instance; messages without an owner record
(a worker on older code, or one that died between taking the message and
``task_received``) -- the visibility timeout stays their backstop.

Goal tasks
----------
``run_goal`` messages are left to the goal runner reaper
(``reap_stale_goal_runners``, GOAL-STALL). A redelivered ``run_goal`` is
idempotent while the dead runner's per-goal lock is held (it skips), but the
reaper deliberately releases that lock and then decides between requeue and
fail (a goal that already ran a tool must not run twice). A restored message
landing between the release and the fail could re-run such a goal, so restoring
it early adds a risk and no benefit -- the reaper already recovers goals within
``goal_heartbeat_stale_seconds``. Its unacked entry is redelivered at the 25 h
backstop, by then a no-op on the terminal goal row. Every other task (workflow
callbacks, mission deliverables, training export, ...) is restored: they are
``acks_late`` tasks, so they already had to tolerate this redelivery, only later.
"""

from __future__ import annotations

import contextlib
import copy
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

OWNER_KEY = "agentverse:celery:unacked_owner"
ALIVE_KEY_PREFIX = "agentverse:celery:worker_alive:"
STATS_KEY = "agentverse:celery:dead_worker_restore_stats"
DEFAULT_RESTORE_LIMIT = 500

# Tasks whose own dead-worker sweeper owns recovery (see the module docstring).
SWEEPER_OWNED_TASKS: frozenset[str] = frozenset(
    {
        "app.scaling.tasks.run_goal",
        "agentverse.goals.run_goal_free",
        "agentverse.goals.run_goal_starter",
        "agentverse.goals.run_goal_professional",
        "agentverse.goals.run_goal_enterprise",
    }
)

# KEYS: unacked, owner. ARGV: tag, instance.
_RECORD_OWNER_LUA = """
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then
    redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
    return 1
end
return 0
"""

# KEYS: unacked, owner. ARGV: tag1, instance1, tag2, instance2, ...
# Deletes the owner records whose message is no longer unacked (acked, rejected
# or restored), only while the record still names the instance read.
_SWEEP_STALE_OWNERS_LUA = """
local gone = {}
for i = 1, #ARGV, 2 do
    local tag = ARGV[i]
    if redis.call('HEXISTS', KEYS[1], tag) == 0
        and redis.call('HGET', KEYS[2], tag) == ARGV[i + 1] then
        redis.call('HDEL', KEYS[2], tag)
        gone[#gone + 1] = tag
    end
end
return gone
"""

# KEYS: unacked, unacked_index, owner, alive key of the instance, stats, queue1..n.
# ARGV: tag, unacked entry as read, instance, message to push.
# -1 instance alive again, -2 owner record changed, -3 entry gone or changed.
_RESTORE_LUA = """
if redis.call('EXISTS', KEYS[4]) == 1 then return -1 end
if redis.call('HGET', KEYS[3], ARGV[1]) ~= ARGV[3] then return -2 end
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return -3 end
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
for i = 6, #KEYS do
    redis.call('RPUSH', KEYS[i], ARGV[4])
end
redis.call('HINCRBY', KEYS[5], 'restored', 1)
return 1
"""

RESTORED, INSTANCE_ALIVE, OWNER_CHANGED, ENTRY_GONE = 1, -1, -2, -3


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def heartbeat_interval_seconds(grace_seconds: float) -> float:
    """A worker beats four times per grace period (dead after ~4 missed beats)."""
    return max(1.0, grace_seconds / 4.0)


@dataclass(frozen=True)
class BrokerKeys:
    """Fully prefixed Redis keys (kombu's ``global_keyprefix`` applied)."""

    unacked: str
    unacked_index: str
    owner: str
    alive_prefix: str
    stats: str

    @classmethod
    def for_prefix(
        cls,
        prefix: str = "",
        *,
        unacked: str = "unacked",
        unacked_index: str = "unacked_index",
    ) -> BrokerKeys:
        return cls(
            unacked=f"{prefix}{unacked}",
            unacked_index=f"{prefix}{unacked_index}",
            owner=f"{prefix}{OWNER_KEY}",
            alive_prefix=f"{prefix}{ALIVE_KEY_PREFIX}",
            stats=f"{prefix}{STATS_KEY}",
        )

    def alive(self, instance: str) -> str:
        return f"{self.alive_prefix}{instance}"


# (message, exchange, routing_key) -> fully prefixed queue list keys; [] = unroutable.
Router = Callable[[dict[str, Any], str, str], list[str]]


class RedisBrokerState:
    """The restorer's reads and atomic writes on the broker's Redis.

    ``client`` is a plain redis-py client on the broker's connection pool (never
    kombu's key-prefixing client: every key passed here is already prefixed).
    """

    def __init__(self, client: Any, keys: BrokerKeys, router: Router) -> None:
        self.client = client
        self.keys = keys
        self.router = router

    # -- worker side --------------------------------------------------------
    def record_owner(self, tag: str, instance: str) -> bool:
        keys = (self.keys.unacked, self.keys.owner)
        return bool(self.client.eval(_RECORD_OWNER_LUA, 2, *keys, tag, instance))

    def heartbeat(self, instance: str, ttl_seconds: float) -> None:
        ttl_ms = max(1000, int(ttl_seconds * 1000))
        self.client.set(self.keys.alive(instance), str(time.time()), px=ttl_ms)

    def forget_instance(self, instance: str) -> None:
        self.client.delete(self.keys.alive(instance))

    # -- restorer side -------------------------------------------------------
    def owners(self) -> dict[str, str]:
        return {
            _text(tag): _text(instance)
            for tag, instance in self.client.hscan_iter(self.keys.owner, count=500)
        }

    def alive_instances(self, instances: set[str]) -> set[str]:
        ordered = sorted(instances)
        if not ordered:
            return set()
        pipe = self.client.pipeline(transaction=False)
        for instance in ordered:
            pipe.exists(self.keys.alive(instance))
        return {inst for inst, n in zip(ordered, pipe.execute(), strict=True) if n}

    def unacked_entry(self, tag: str) -> tuple[str, float | None] | None:
        pipe = self.client.pipeline(transaction=False)
        pipe.hget(self.keys.unacked, tag)
        pipe.zscore(self.keys.unacked_index, tag)
        raw, score = pipe.execute()
        if raw is None:
            return None
        return _text(raw), (float(score) if score is not None else None)

    def sweep_stale_owners(self, pairs: list[tuple[str, str]]) -> set[str]:
        gone: set[str] = set()
        keys = (self.keys.unacked, self.keys.owner)
        for start in range(0, len(pairs), 250):
            argv = [part for pair in pairs[start : start + 250] for part in pair]
            result = self.client.eval(_SWEEP_STALE_OWNERS_LUA, 2, *keys, *argv)
            gone.update(_text(tag) for tag in result or [])
        return gone

    def restore(
        self, tag: str, entry: str, instance: str, message: str, queue_keys: list[str]
    ) -> int:
        keys = (
            self.keys.unacked,
            self.keys.unacked_index,
            self.keys.owner,
            self.keys.alive(instance),
            self.keys.stats,
            *queue_keys,
        )
        return int(self.client.eval(_RESTORE_LUA, len(keys), *keys, tag, entry, instance, message))


@dataclass
class RestoreReport:
    owners_seen: int = 0
    dead_instances: list[str] = field(default_factory=list)
    restored: list[dict[str, str]] = field(default_factory=list)
    skipped_live: int = 0
    skipped_young: int = 0
    skipped_sweeper_owned: int = 0
    unroutable: int = 0
    lost_race: int = 0
    stale_owners_removed: int = 0
    errors: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "owners_seen": self.owners_seen,
            "dead_instances": self.dead_instances,
            "restored": len(self.restored),
            "restored_tasks": self.restored,
            "skipped_live": self.skipped_live,
            "skipped_young": self.skipped_young,
            "skipped_sweeper_owned": self.skipped_sweeper_owned,
            "unroutable": self.unroutable,
            "lost_race": self.lost_race,
            "stale_owners_removed": self.stale_owners_removed,
            "errors": self.errors,
            "truncated": self.truncated,
        }


def _redelivered_copy(message: dict[str, Any]) -> dict[str, Any]:
    """The message as kombu's ``_do_restore_message`` re-queues it."""
    restored = copy.deepcopy(message)
    with contextlib.suppress(KeyError, TypeError):
        restored["headers"]["redelivered"] = True
        restored["properties"]["delivery_info"]["redelivered"] = True
    return restored


def _task_name(message: dict[str, Any]) -> str:
    headers = message.get("headers")
    return str(headers.get("task") or "") if isinstance(headers, dict) else ""


def restore_dead_worker_messages(
    state: RedisBrokerState,
    *,
    grace_seconds: float,
    now: float | None = None,
    limit: int = DEFAULT_RESTORE_LIMIT,
    sweeper_owned: frozenset[str] = SWEEPER_OWNED_TASKS,
) -> RestoreReport:
    """Put the unacked messages of dead worker instances back on their queues."""
    from kombu.utils.json import dumps, loads

    now = time.time() if now is None else now
    report = RestoreReport()
    owners = state.owners()
    report.owners_seen = len(owners)
    if not owners:
        return report

    gone = state.sweep_stale_owners(sorted(owners.items()))
    report.stale_owners_removed = len(gone)
    held = {tag: inst for tag, inst in owners.items() if tag not in gone}
    alive = state.alive_instances(set(held.values()))
    dead = sorted(set(held.values()) - alive)
    report.dead_instances = dead
    report.skipped_live = sum(1 for inst in held.values() if inst in alive)

    attempts = 0
    for tag, instance in sorted(held.items()):
        if instance in alive:
            continue
        if attempts >= limit:
            report.truncated = True
            break
        try:
            entry = state.unacked_entry(tag)
            if entry is None:  # acked / restored since the sweep
                report.lost_race += 1
                continue
            raw, taken_at = entry
            if taken_at is not None and now - taken_at < grace_seconds:
                report.skipped_young += 1
                continue
            message, exchange, routing_key = loads(raw)
            task = _task_name(message)
            if task in sweeper_owned:
                report.skipped_sweeper_owned += 1
                continue
            queue_keys = state.router(message, exchange, routing_key)
            if not queue_keys:
                report.unroutable += 1
                _log.error(
                    "dead_worker_message_unroutable",
                    tag=tag, task=task, exchange=exchange, routing_key=routing_key,
                )
                continue
            attempts += 1
            outcome = state.restore(
                tag, raw, instance, dumps(_redelivered_copy(message)), queue_keys
            )
        except Exception as exc:
            report.errors += 1
            _log.warning(
                "dead_worker_restore_failed", tag=tag, error=f"{type(exc).__name__}: {exc}"[:200]
            )
            continue
        if outcome == RESTORED:
            report.restored.append({"tag": tag, "task": task, "instance": instance})
            _log.warning(
                "dead_worker_message_restored",
                tag=tag, task=task, instance=instance, queues=queue_keys,
                unacked_for_s=round(now - taken_at, 1) if taken_at is not None else None,
            )
        else:
            report.lost_race += 1

    _record_metrics(report)
    return report


def _record_metrics(report: RestoreReport) -> None:
    try:
        from app.observability.metrics import record_dead_worker_restore

        record_dead_worker_restore("restored", len(report.restored))
        record_dead_worker_restore("skipped_sweeper_owned", report.skipped_sweeper_owned)
        record_dead_worker_restore("unroutable", report.unroutable)
        record_dead_worker_restore("lost_race", report.lost_race)
        record_dead_worker_restore("error", report.errors)
    except Exception as exc:  # metrics never break the restorer
        _log.debug("dead_worker_restore_metrics_failed", error=str(exc)[:120])


# ── kombu channel adapter ──────────────────────────────────────────────────────


def make_router(channel: Any) -> Router:
    """Route like kombu's ``_do_restore_message``, without its dead-letter default."""
    prefix = str(getattr(channel, "global_keyprefix", "") or "")

    def route(message: dict[str, Any], exchange: str, routing_key: str) -> list[str]:
        if exchange:
            try:
                queues = list(
                    channel.typeof(exchange).lookup(
                        channel.get_table(exchange), exchange, routing_key, None
                    )
                )
            except KeyError:
                queues = []
        else:
            queues = [routing_key] if routing_key else []
        priority = channel._get_message_priority(message, reverse=False)
        return [f"{prefix}{channel._q_for_pri(q, priority)}" for q in queues if q]

    return route


def state_for_channel(channel: Any) -> RedisBrokerState | None:
    """A :class:`RedisBrokerState` on a kombu Redis channel; ``None`` off Redis."""
    if not hasattr(channel, "unacked_key") or not getattr(channel, "ack_emulation", False):
        return None
    import redis

    keys = BrokerKeys.for_prefix(
        str(getattr(channel, "global_keyprefix", "") or ""),
        unacked=str(channel.unacked_key),
        unacked_index=str(channel.unacked_index_key),
    )
    client = redis.Redis(connection_pool=channel.client.connection_pool)
    return RedisBrokerState(client, keys, make_router(channel))


@contextlib.contextmanager
def open_broker_state(app: Any) -> Iterator[RedisBrokerState | None]:
    """The broker's state through a fresh connection of *app* (closed after)."""
    conn = app.connection_for_write()
    try:
        yield state_for_channel(conn.default_channel)
    finally:
        with contextlib.suppress(Exception):
            conn.release()


# ── worker side: liveness + ownership ──────────────────────────────────────────


def _settings() -> tuple[bool, float]:
    from app.core.config import get_settings

    settings = get_settings()
    return (
        bool(getattr(settings, "celery_dead_worker_restore_enabled", True)),
        float(getattr(settings, "celery_dead_worker_grace_seconds", 120.0)),
    )


class WorkerLiveness:
    """Heartbeat + message ownership of one worker main process."""

    def __init__(self, app: Any, *, grace_seconds: float) -> None:
        self.app = app
        self.grace_seconds = grace_seconds
        self.instance_id = ""
        self._state: RedisBrokerState | None = None
        self._conn: Any = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid = 0

    def ensure_started(self, hostname: str | None) -> RedisBrokerState | None:
        if self._thread is not None and self._pid == os.getpid():
            return self._state
        with self._lock:
            if self._thread is not None and self._pid == os.getpid():
                return self._state
            host = hostname or f"celery@{socket.gethostname()}"
            self._pid = os.getpid()
            self.instance_id = f"{host}/{self._pid}/{uuid.uuid4().hex[:12]}"
            self._conn = self.app.connection_for_write()
            self._state = state_for_channel(self._conn.default_channel)
            if self._state is not None:
                # Alive BEFORE any message is recorded as ours.
                self._state.heartbeat(self.instance_id, self.grace_seconds)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="agentverse-worker-liveness", daemon=True
            )
            self._thread.start()
            _log.info(
                "worker_liveness_started",
                instance=self.instance_id,
                grace_s=self.grace_seconds,
                interval_s=heartbeat_interval_seconds(self.grace_seconds),
                broker_supported=self._state is not None,
            )
            return self._state

    def _run(self) -> None:
        interval = heartbeat_interval_seconds(self.grace_seconds)
        while not self._stop.wait(interval):
            state = self._state
            if state is None:
                return
            try:
                state.heartbeat(self.instance_id, self.grace_seconds)
            except Exception as exc:  # keep beating; a long outage reads as dead
                _log.warning(
                    "worker_liveness_heartbeat_failed",
                    instance=self.instance_id, error=f"{type(exc).__name__}: {exc}"[:160],
                )

    def record(self, tag: str, hostname: str | None) -> None:
        state = self.ensure_started(hostname)
        if state is None or not tag:
            return
        state.record_owner(tag, self.instance_id)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.release()


_liveness_by_app: dict[int, WorkerLiveness] = {}


def worker_liveness(app: Any) -> WorkerLiveness | None:
    """This process's liveness tracker for *app* (``None`` when disabled)."""
    tracker = _liveness_by_app.get(id(app))
    if tracker is None:
        enabled, grace = _settings()
        if not enabled:
            return None
        tracker = _liveness_by_app.setdefault(id(app), WorkerLiveness(app, grace_seconds=grace))
    return tracker


def connect_worker_liveness(app: Any) -> None:
    """Wire heartbeat + ownership recording into *app*'s workers (idempotent)."""
    if getattr(app, "_agentverse_liveness_connected", False):
        return
    app._agentverse_liveness_connected = True
    from celery.signals import (  # type: ignore[import-untyped]
        task_received,
        worker_ready,
        worker_shutdown,
    )

    def _ours(sender: Any) -> bool:
        return getattr(sender, "app", app) is app

    def _on_ready(sender: Any = None, **_kw: Any) -> None:
        if not _ours(sender):
            return
        try:
            tracker = worker_liveness(app)
            if tracker is not None:
                tracker.ensure_started(getattr(sender, "hostname", None))
        except Exception as exc:  # never fail worker start over liveness
            _log.warning("worker_liveness_start_failed", error=f"{type(exc).__name__}: {exc}"[:200])

    def _on_received(sender: Any = None, request: Any = None, **_kw: Any) -> None:
        if request is None or not _ours(sender):
            return
        try:
            tracker = worker_liveness(app)
            if tracker is None:
                return
            message = getattr(request, "message", None)
            tag = getattr(message, "delivery_tag", None)
            if tag:
                tracker.record(str(tag), getattr(sender, "hostname", None))
        except Exception as exc:  # unrecorded = the 25 h backstop, never a lost task
            _log.warning(
                "worker_ownership_record_failed", error=f"{type(exc).__name__}: {exc}"[:200]
            )

    def _on_shutdown(sender: Any = None, **_kw: Any) -> None:
        tracker = _liveness_by_app.pop(id(app), None)
        if tracker is not None:
            tracker.stop()

    worker_ready.connect(_on_ready, weak=False)
    task_received.connect(_on_received, weak=False)
    worker_shutdown.connect(_on_shutdown, weak=False)


__all__ = [
    "ALIVE_KEY_PREFIX",
    "OWNER_KEY",
    "SWEEPER_OWNED_TASKS",
    "BrokerKeys",
    "RedisBrokerState",
    "RestoreReport",
    "WorkerLiveness",
    "connect_worker_liveness",
    "heartbeat_interval_seconds",
    "make_router",
    "open_broker_state",
    "restore_dead_worker_messages",
    "state_for_channel",
    "worker_liveness",
]
