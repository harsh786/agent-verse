"""Tests that TypeScript SDK has all required methods.

The TypeScript SDK is a separate monorepo project. When its source is not
present in this checkout (it lives under ``Archived/agent-verse-sdk-typescript``
in a full monorepo layout), these completeness checks skip with a reason rather
than erroring on a missing file — a missing sibling project is not a backend
regression.
"""
import os

import pytest

_SDK_SRC = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../Archived/agent-verse-sdk-typescript/src",
    )
)

_requires_ts_sdk = pytest.mark.skipif(
    not os.path.isfile(os.path.join(_SDK_SRC, "client.ts")),
    reason=f"TypeScript SDK source not present at {_SDK_SRC} (separate monorepo project)",
)


@_requires_ts_sdk
def test_typescript_sdk_has_all_required_methods():
    """TypeScript SDK must expose all methods from the API surface."""
    with open(os.path.join(_SDK_SRC, "client.ts")) as f:
        src = f.read()

    required_methods = [
        "getAgent", "updateAgent", "deleteAgent", "snapshotAgent",
        "listAgentVersions", "rollbackAgent",
        "listSchedules", "createSchedule", "deleteSchedule",
        "recallMemory", "storeMemory",
        "searchKnowledge",
        "deleteConnector", "testConnector", "getConnectorCatalog",
        "getGoalMetrics", "getCostMetrics",
    ]

    missing = [m for m in required_methods if m not in src]
    assert not missing, f"TypeScript SDK missing methods: {missing}"


@_requires_ts_sdk
def test_typescript_sdk_has_required_interfaces():
    with open(os.path.join(_SDK_SRC, "client.ts")) as f:
        src = f.read()

    # Types live in types.ts, but the client imports them — check both files
    with open(os.path.join(_SDK_SRC, "types.ts")) as f:
        types_src = f.read()

    combined = src + types_src
    required_types = ["AgentSnapshot", "Schedule", "Memory", "GoalMetrics"]
    missing = [t for t in required_types if t not in combined]
    assert not missing, f"TypeScript SDK missing interfaces: {missing}"
