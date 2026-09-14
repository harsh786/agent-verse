"""Populate the tenant knowledge graph with entities observed during a run.

Recall is ALREADY wired: ``PlannerMixin`` adapts the injected
``KnowledgeGraphStore`` through ``KnowledgeGraphFactsSource`` into the planner
context. The gap this closes is *population*: nothing extracted entities from
step/tool outputs during an agent run, so the graph stayed empty for run-learned
facts (it was only filled by document ingestion). Here we extract entities from
completed step outputs and upsert them as ``ENTITY`` nodes, so a later plan can
recall "what did we already learn about X".

Extraction is a deterministic fast tier (IDs, URLs, emails, proper-noun phrases).
A future LLM-based triple extractor can layer on top, gated by cost/profile —
mirroring the platform's fast-classifier + optional-LLM-classify pattern. It is a
real extractor, not a stub.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any

from app.knowledge_graph.models import GraphNode, NodeType

_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}-\d+\b")  # JIRA-101, PROJ-42
_URL_RE = re.compile(r"https?://[^\s)>\]}]+")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Proper-noun phrase: 1-4 Capitalized tokens (covers "Rome", "Acme Corp").
_PROPER_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3}\b")

# Sentence-initial / boilerplate capitalizations that are not entities.
_STOP = frozenset(
    {
        "The", "A", "An", "This", "That", "These", "Those", "It", "Step", "Goal",
        "Found", "Summary", "Tool", "Result", "Output", "Error", "None", "Insufficient",
        "Data", "No", "Yes", "Note", "Then", "Next", "Here", "There", "When", "While",
    }
)

_MAX_LABEL_LEN = 80


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Return distinct ``(label, kind)`` entity candidates from free text.

    Deterministic and order-preserving; ``kind`` ∈ {id, url, email, name}.
    """
    if not text:
        return []
    found: dict[str, str] = {}
    for m in _ID_RE.findall(text):
        found.setdefault(m, "id")
    for m in _URL_RE.findall(text):
        found.setdefault(m.rstrip(".,);]"), "url")
    for m in _EMAIL_RE.findall(text):
        found.setdefault(m, "email")
    for m in _PROPER_RE.findall(text):
        label = m.strip()
        if len(label) < 3 or len(label) > _MAX_LABEL_LEN:
            continue
        if label in _STOP:
            continue
        # A single sentence-initial stopword followed by real nouns is still useful;
        # only skip when the WHOLE phrase is a lone stopword (handled above).
        found.setdefault(label, "name")
    return list(found.items())


def _node_id(tenant_id: str, label: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{label.casefold()}".encode()).hexdigest()[:16]
    return f"kg_run_{digest}"


def record_entities_from_steps(
    store: Any,
    tenant_id: str,
    steps: Sequence[Any],
    *,
    source_id: str | None = None,
    max_per_run: int = 50,
) -> int:
    """Extract entities from completed step outputs and upsert ENTITY nodes.

    Idempotent (deterministic ``node_id`` → re-adding the same entity overwrites
    the same index slot). Best-effort: a single bad node never aborts the run.
    Returns the number of nodes successfully upserted this call.
    """
    if store is None or not tenant_id:
        return 0
    add_node = getattr(store, "add_node", None)
    if not callable(add_node):
        return 0
    count = 0
    for step in steps:
        output = (getattr(step, "output", "") or "").strip()
        if not output:
            continue
        for label, kind in extract_entities(output):
            node = GraphNode(
                node_id=_node_id(tenant_id, label),
                tenant_id=tenant_id,
                node_type=NodeType.ENTITY,
                label=label,
                content=label,
                source_id=source_id,
                confidence=0.6,  # heuristic-tier extraction → moderate confidence
                metadata={"kind": kind, "origin": "agent_run"},
            )
            try:
                add_node(node)
                count += 1
            except Exception:
                continue
            if count >= max_per_run:
                return count
    return count
