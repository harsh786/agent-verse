"""conftest for tests/execution_environment — prevent in-process resource limits.

tests/execution_environment/test_worker_entrypoint.py calls main() in-process.
main() calls _set_resource_limits() which sets RLIMIT_DATA on the CURRENT pytest
process.  Setting RLIMIT_DATA=512MB on the pytest process causes subsequent
subprocess.Popen / asyncio.create_subprocess_exec calls to fail with
BlockingIOError [Errno 35] (EAGAIN / resource temporarily unavailable).

The autouse fixture below mocks _set_resource_limits to a no-op for all tests
in this package except the tests that directly test the function itself, ensuring
main() tests exercise control flow without mutating the test-runner environment.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_resource_limits_in_process(request):
    """Prevent _set_resource_limits from mutating the pytest process's RLIMIT.

    Skip the mock for tests that specifically exercise _set_resource_limits so
    they can still validate exception-safety.
    """
    test_name = request.node.name
    if "set_resource_limits" in test_name:
        yield  # let the dedicated tests call the real function
        return
    with patch(
        "app.execution_environment.worker_entrypoint._set_resource_limits",
        return_value=None,
    ):
        yield
