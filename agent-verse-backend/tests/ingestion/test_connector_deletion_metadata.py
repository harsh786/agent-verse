"""P1c-13: the catalogue's ``supports_deletion`` matches what reconciliation can do.

MongoDB, Redis and Elasticsearch list what exists upstream (``iter_live_doc_ids``),
so ``POST /sources/{id}/reconcile`` removes deleted items — but the catalogue said
``supports_deletion: false`` for them (live, ``GET /sources/catalogue``). Every
connector that can list upstream says so, and none that cannot claims it.
"""

from __future__ import annotations

from app.ingestion.base_connector import lists_upstream
from app.ingestion.connector_registry import (
    _REGISTRY,
    get_connector_metadata,
    load_all_connectors,
)


def test_catalogue_deletion_support_matches_the_live_listing() -> None:
    load_all_connectors()
    meta = {m["source_type"]: m for m in get_connector_metadata()}
    wrong = {
        source_type: (meta[source_type]["supports_deletion"], lists_upstream(cls))
        for source_type, cls in _REGISTRY.items()
        if source_type in meta and bool(meta[source_type]["supports_deletion"]) != lists_upstream(cls)
    }
    assert not wrong, f"(catalogue says, can list upstream): {wrong}"
    for source_type in ("mongodb", "redis", "elasticsearch", "opensearch"):
        assert meta[source_type]["supports_deletion"] is True, source_type
