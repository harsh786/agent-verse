"""CPUs this process may really use: scheduler affinity and the container's CPU quota.

``os.cpu_count()`` is the NODE's count inside a container: a 2-CPU pod on a
64-core node must not size thread pools (OCR, torch intra-op threads) for 64.

This module has no side effects on import (unlike ``app.ocr.concurrency``, which
pins ``OMP_THREAD_LIMIT`` for Tesseract), so model loaders can use it safely.
"""

from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path

CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def cgroup_cpu_quota(
    *,
    v2_cpu_max: Path = CGROUP_V2_CPU_MAX,
    v1_quota: Path = CGROUP_V1_QUOTA,
    v1_period: Path = CGROUP_V1_PERIOD,
) -> float | None:
    """The container's CPU limit in CPUs (cgroup v2, else v1), or None when unlimited."""
    try:
        quota, period = v2_cpu_max.read_text().split()[:2]
        if quota != "max" and int(period) > 0:
            return int(quota) / int(period)
        return None
    except (OSError, ValueError):
        pass
    try:
        quota_us = int(v1_quota.read_text().strip())
        period_us = int(v1_period.read_text().strip())
        if quota_us > 0 and period_us > 0:
            return quota_us / period_us
    except (OSError, ValueError):
        pass
    return None


def available_cpus(quota: float | None = None) -> int:
    """CPUs this process may use: ``min(cpu_count, affinity, ceil(cgroup quota))``.

    ``quota`` is the container CPU limit; pass the result of
    :func:`cgroup_cpu_quota` (``None`` = unlimited).
    """
    count = os.cpu_count() or 1
    with contextlib.suppress(AttributeError, OSError):  # macOS: no sched_getaffinity
        count = min(count, len(os.sched_getaffinity(0)))
    if quota is not None:
        count = min(count, max(1, math.ceil(quota)))
    return max(1, count)


__all__ = ["available_cpus", "cgroup_cpu_quota"]
