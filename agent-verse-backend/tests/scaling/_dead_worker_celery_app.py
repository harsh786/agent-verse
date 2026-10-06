"""A minimal Celery app for the dead-worker restore integration test.

Started as a real ``celery worker`` subprocess (then SIGKILLed) by
``test_dead_worker_restore_redis_integration.py``. It carries the same
acks_late settings and the same liveness wiring as app.scaling.celery_app.
"""

from __future__ import annotations

import os
import time

import redis
from celery import Celery  # type: ignore[import-untyped]

from app.scaling.dead_worker_restore import connect_worker_liveness

BROKER = os.environ["DWR_TEST_BROKER"]
QUEUE = "dwr.celery"

app = Celery("dwr_test", broker=BROKER)
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue=QUEUE,
    broker_transport_options={"visibility_timeout": 3600},
    worker_hijack_root_logger=False,
)
connect_worker_liveness(app)


@app.task(name="dwr.wait_for")  # type: ignore[untyped-decorator]
def wait_for(release_key: str, done_key: str) -> str:
    """Hold the message until the test sets *release_key*, then mark it done."""
    client = redis.Redis.from_url(BROKER)
    deadline = time.time() + 120
    while time.time() < deadline and not client.exists(release_key):
        time.sleep(0.1)
    client.set(done_key, os.getpid())
    return "done"
