"""Tests that TypeScript SDK has all required methods."""
import os
import re


def test_typescript_sdk_has_all_required_methods():
    """TypeScript SDK must expose all methods from the API surface."""
    sdk_path = os.path.join(
        os.path.dirname(__file__),
        "../../../agent-verse-sdk-typescript/src/client.ts"
    )
    with open(sdk_path) as f:
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


def test_typescript_sdk_has_required_interfaces():
    sdk_path = os.path.join(
        os.path.dirname(__file__),
        "../../../agent-verse-sdk-typescript/src/client.ts"
    )
    with open(sdk_path) as f:
        src = f.read()

    # Types live in types.ts, but the client imports them — check both files
    types_path = os.path.join(
        os.path.dirname(__file__),
        "../../../agent-verse-sdk-typescript/src/types.ts"
    )
    with open(types_path) as f:
        types_src = f.read()

    combined = src + types_src
    required_types = ["AgentSnapshot", "Schedule", "Memory", "GoalMetrics"]
    missing = [t for t in required_types if t not in combined]
    assert not missing, f"TypeScript SDK missing interfaces: {missing}"
