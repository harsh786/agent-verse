"""Operator stack dumps: ``kill -USR2 <pid>`` prints every thread's stack.

A process that burns a core while serving little (a hot background thread
starving the event loop through the GIL) cannot be diagnosed from logs alone,
and production images carry no profiler. ``faulthandler`` is in the standard
library, async-signal-safe, and writes to stderr (the container log), so an
operator runs ``docker kill -s USR2 <container>`` (or ``kubectl exec ... kill
-USR2 1``) and reads the stacks — no restart, no new dependency.
"""

from __future__ import annotations

import faulthandler
import signal
import sys

_installed = False


def install_stack_dump_signal() -> bool:
    """Register SIGUSR2 -> dump all threads' stacks to stderr (idempotent).

    Returns False where the signal does not exist (Windows) or registration
    is refused (not the main thread, an embedding host).
    """
    global _installed
    if _installed:
        return True
    sigusr2 = getattr(signal, "SIGUSR2", None)
    if sigusr2 is None:
        return False
    try:
        faulthandler.register(sigusr2, file=sys.stderr, all_threads=True, chain=False)
    except (RuntimeError, ValueError, OSError):
        return False
    _installed = True
    return True
