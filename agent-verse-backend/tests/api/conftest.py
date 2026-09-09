"""Conftest for API tests — ensures the Python SDK is importable."""
from __future__ import annotations

import os
import sys

# The agentverse SDK lives in Archived/agent-verse-sdk-python.
# Insert it at the front so `import agentverse` resolves to the SDK, not the
# backend CLI entrypoint that shares the name.
_SDK_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../Archived/agent-verse-sdk-python")
)
if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)
