"""T2.1 — entity extraction + knowledge-graph population from step outputs."""
from __future__ import annotations

from app.agent.entity_memory_wiring import extract_entities, record_entities_from_steps
from app.agent.state import StepResult, StepStatus
from app.knowledge_graph.models import NodeType
from app.knowledge_graph.store import KnowledgeGraphStore


def _step(output: str) -> StepResult:
    return StepResult(description="s", output=output, status=StepStatus.COMPLETE)


def test_extract_entities_finds_ids_urls_emails_and_names() -> None:
    text = (
        "Found ticket JIRA-101 assigned to Ada Lovelace, see "
        "https://example.com/x and email ada@example.com about Acme Corp."
    )
    found = dict(extract_entities(text))
    assert found.get("JIRA-101") == "id"
    assert found.get("ada@example.com") == "email"
    assert any(k.startswith("https://example.com") for k in found)
    assert "Ada Lovelace" in found
    assert "Acme Corp" in found


def test_extract_skips_boilerplate_capitalizations() -> None:
    found = dict(extract_entities("Found 0 issues. Summary: The Result was empty."))
    # sentence-initial boilerplate must not become entities
    assert "Found" not in found
    assert "Summary" not in found
    assert "The" not in found


def test_record_populates_store_and_is_recallable() -> None:
    store = KnowledgeGraphStore()
    tenant = "t-kg-1"
    n = record_entities_from_steps(
        store,
        tenant,
        [_step("Ticket JIRA-101 relates to Acme Corp")],
        source_id="goal-1",
    )
    assert n >= 2
    hits = store.query_nodes(tenant_id=tenant, search="JIRA-101")
    assert any(node.label == "JIRA-101" and node.node_type == NodeType.ENTITY for node in hits)
    # tenant isolation: another tenant sees nothing
    assert store.query_nodes(tenant_id="other", search="JIRA-101") == []


def test_record_is_idempotent() -> None:
    store = KnowledgeGraphStore()
    tenant = "t-kg-2"
    steps = [_step("Acme Corp signed with Globex")]
    record_entities_from_steps(store, tenant, steps)
    record_entities_from_steps(store, tenant, steps)  # again
    acme = store.query_nodes(tenant_id=tenant, search="Acme Corp")
    # Deterministic node_id → no duplicate nodes for the same entity.
    assert len([n for n in acme if n.label == "Acme Corp"]) == 1


def test_record_noops_without_store_or_tenant() -> None:
    assert record_entities_from_steps(None, "t", [_step("Acme")]) == 0
    assert record_entities_from_steps(KnowledgeGraphStore(), "", [_step("Acme")]) == 0
