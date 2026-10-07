from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(not hasattr(signal, "SIGUSR2"), reason="POSIX only")
def test_sigusr2_dumps_every_thread_stack() -> None:
    code = textwrap.dedent(
        """
        import os, signal, threading, time
        from app.observability.debug_signals import install_stack_dump_signal
        assert install_stack_dump_signal() and install_stack_dump_signal()
        def spin_in_named_function():
            end = time.time() + 5
            while time.time() < end:
                pass
        threading.Thread(target=spin_in_named_function, daemon=True).start()
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGUSR2)
        time.sleep(0.2)
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    )
    assert out.returncode == 0, out.stderr[-500:]
    assert "spin_in_named_function" in out.stderr
