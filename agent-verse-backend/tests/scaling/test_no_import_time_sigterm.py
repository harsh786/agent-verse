"""Importing app.scaling.tasks must not replace the host process's SIGTERM handler."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_import_leaves_sigterm_handler_alone() -> None:
    code = textwrap.dedent(
        """
        import signal
        marker = lambda *_: None
        signal.signal(signal.SIGTERM, marker)
        import app.scaling.tasks  # noqa: F401
        assert signal.getsignal(signal.SIGTERM) is marker, "import replaced SIGTERM"
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]


def test_worker_process_init_installs_the_handler() -> None:
    code = textwrap.dedent(
        """
        import signal
        import app.scaling.tasks as tasks
        before = signal.getsignal(signal.SIGTERM)
        from celery.signals import worker_process_init
        worker_process_init.send(sender=None)
        after = signal.getsignal(signal.SIGTERM)
        assert after is not before and callable(after), (before, after)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
