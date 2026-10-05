"""MDB-02 / TG-02: MongoDB tool risk comes from the tool OPERATION, never the connector name.

The connector name is a tenant-chosen display string. ``budget-db`` used to turn
``mongodb_insert_one`` into ``read`` (the substring ``get`` in "budget"), and
``prod-db`` used to DOWNGRADE ``mongodb_delete_one`` from destructive to
write_high (the high-risk-connector override returned before the destructive
check). A name may only ever raise a tier.
"""

from __future__ import annotations

import pytest

from app.agent.tool_risk import classify_tool_risk

_RANK = {"read": 0, "write_low": 1, "write_high": 2, "destructive": 3}

CONNECTOR_NAMES = [
    "",
    "mongodb",
    "MongoDB",
    "budget-db",
    "target-db",
    "widget-store",
    "settings-db",
    "prod-db",
    "production",
    "readonly",
    "read-only replica",
    "analytics",
    "list-of-things",
    "search-index",
    "status-board",
    "billing",
    "orders",
]

EXPECTED = {
    "mongodb_find": "read",
    "mongodb_find_one": "read",
    "mongodb_count": "read",
    "mongodb_list_collections": "read",
    "mongodb_insert_one": "write_high",
    "mongodb_insert_many": "write_high",
    "mongodb_update_one": "write_high",
    "mongodb_update_many": "write_high",
    "mongodb_replace_one": "write_high",
    "mongodb_delete_one": "write_high",
    "mongodb_delete_many": "destructive",
    "mongodb_drop_collection": "destructive",
    "mongodb_drop_database": "destructive",
}


@pytest.mark.parametrize("server_name", CONNECTOR_NAMES)
@pytest.mark.parametrize(("tool", "expected"), sorted(EXPECTED.items()))
def test_mongodb_tool_risk_ignores_connector_name_for_writes(
    tool: str, expected: str, server_name: str
) -> None:
    risk = classify_tool_risk(tool, server_name)
    if expected == "read":
        # A high-risk connector name may RAISE a read tool, never anything else.
        assert _RANK[risk] >= _RANK["read"]
    else:
        assert risk == expected, (tool, server_name, risk)


@pytest.mark.parametrize("server_name", ["", "mongodb", "analytics", "readonly", "budget-db"])
@pytest.mark.parametrize(
    "tool", ["mongodb_find", "mongodb_find_one", "mongodb_count", "mongodb_list_collections"]
)
def test_mongodb_read_tools_are_read_on_neutral_names(tool: str, server_name: str) -> None:
    assert classify_tool_risk(tool, server_name) == "read"


def test_aggregate_without_arguments_is_gated() -> None:
    """The pipeline is unknown: the approval-requiring tier, under every name."""
    for name in CONNECTOR_NAMES:
        assert classify_tool_risk("mongodb_aggregate", name) == "write_high", name


@pytest.mark.parametrize("server_name", CONNECTOR_NAMES)
@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$match": {"a": 1}}, {"$out": "x"}],
        [{"$merge": {"into": {"db": "other", "coll": "x"}}}],
        [{"$facet": {"a": [{"$out": "x"}]}}],
    ],
)
def test_aggregate_with_write_stages_is_write_high(
    pipeline: list[dict[str, object]], server_name: str
) -> None:
    assert (
        classify_tool_risk("mongodb_aggregate", server_name, arguments={"pipeline": pipeline})
        == "write_high"
    )


@pytest.mark.parametrize("server_name", ["", "mongodb", "budget-db", "analytics", "readonly"])
def test_read_only_aggregate_is_read(server_name: str) -> None:
    args = {"pipeline": [{"$match": {"a": 1}}, {"$group": {"_id": "$a", "n": {"$sum": 1}}}]}
    assert classify_tool_risk("mongodb_aggregate", server_name, arguments=args) == "read"


@pytest.mark.parametrize("pipeline", [None, "not-a-list", [1, 2], [{"$match": {}}, "x"]])
def test_aggregate_with_unverifiable_pipeline_is_gated(pipeline: object) -> None:
    assert (
        classify_tool_risk("mongodb_aggregate", "mongodb", arguments={"pipeline": pipeline})
        == "write_high"
    )


@pytest.mark.parametrize("server_name", ["prod-db", "production", "billing", "deploy-db"])
@pytest.mark.parametrize(
    "tool", ["delete_issue", "drop_table", "purge_cache", "mongodb_delete_many", "remove_user"]
)
def test_high_risk_connector_name_never_lowers_destructive(tool: str, server_name: str) -> None:
    assert classify_tool_risk(tool, server_name) == "destructive"


@pytest.mark.parametrize("server_name", ["budget-db", "target-db", "widget-store", "settings-db"])
@pytest.mark.parametrize("tool", ["create_record", "send_message", "insert_row", "publish_post"])
def test_generic_connector_name_never_lowers_a_write(tool: str, server_name: str) -> None:
    alone = classify_tool_risk(tool)
    assert _RANK[classify_tool_risk(tool, server_name)] >= _RANK[alone]


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("budget_db__mongodb_insert_one", "write_high"),
        ("target_db__mongodb_aggregate", "write_high"),
        ("prod_db__mongodb_delete_one", "write_high"),
        ("prod_db__mongodb_delete_many", "destructive"),
        ("analytics__mongodb_find", "read"),
        ("builtin-mongodb:budget-db/mongodb_update_one", "write_high"),
    ],
)
def test_connection_qualified_mongodb_names_keep_their_operation_risk(
    tool: str, expected: str
) -> None:
    assert classify_tool_risk(tool, "budget-db") == expected


@pytest.mark.parametrize("server_name", ["budget-db", "target-db", "widget-store", "readonly"])
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("mongodb_insert_one", {"collection": "c", "document": {"a": 1}}),
        ("mongodb_update_one", {"collection": "c", "filter": {}, "update": {"a": 1}}),
        ("mongodb_aggregate", {"collection": "c", "pipeline": [{"$out": "x"}]}),
    ],
)
async def test_governed_gate_never_runs_a_mongodb_write_unapproved(
    tool: str, arguments: dict[str, object], server_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the gate: the write is routed to approval, never auto-run."""
    from types import SimpleNamespace

    from app.agent.tool_gate import GateDecision, GovernedToolGate

    gate = GovernedToolGate(guardrails=None, autonomy_mode="bounded-autonomous")
    asked: list[str] = []

    async def _approve(tool_name: str, *_a: object, **_k: object) -> GateDecision:
        asked.append(tool_name)
        return GateDecision(False, "rejected in test")

    monkeypatch.setattr(gate, "_approve", _approve)
    decision = await gate.authorize(
        tool_name=tool,
        server_name=server_name,
        arguments=arguments,
        tenant_ctx=SimpleNamespace(tenant_id="t-1"),
        goal_id="g-1",
    )
    assert asked == [tool]
    assert decision.allowed is False


@pytest.mark.parametrize("server_name", ["prod-db", "readonly", "analytics", "budget-db", ""])
def test_mongodb_delete_one_is_approvable_never_read_or_auto(server_name: str) -> None:
    """Owner decision: a single delete pauses for HITL approval (write_high) under any
    connector name; it is never lowered to read/write_low and never raised to an
    unapprovable destructive deny."""
    assert classify_tool_risk("mongodb_delete_one", server_name) == "write_high"
