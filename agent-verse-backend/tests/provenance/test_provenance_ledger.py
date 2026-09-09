"""Tests for ProvenanceLedger with claim-level source chain — 6 tests."""
from __future__ import annotations

from app.provenance.ledger import ProvenanceLedger
from app.provenance.source_ref import SourceRef


def _make_source(source_type: str = "knowledge_base", url: str = "https://example.com") -> SourceRef:
    return SourceRef(source_type=source_type, url=url, chunk_id="c1")


def test_ledger_records_claim_and_lists() -> None:
    ledger = ProvenanceLedger()
    rec = ledger.record(
        claim_text="The sky is blue",
        sources=[_make_source()],
        step_id="step_1",
        model_id="gpt-4",
        confidence=0.9,
    )
    assert rec.claim_id != ""
    assert rec.verification_status == "supported"
    assert len(ledger.list_all()) == 1


def test_ledger_unsupported_on_low_confidence() -> None:
    ledger = ProvenanceLedger()
    rec = ledger.record(
        claim_text="Uncertain claim",
        sources=[],
        step_id="step_2",
        model_id="gpt-4",
        confidence=0.2,
    )
    assert rec.verification_status == "unsupported"


def test_ledger_unknown_status_no_sources_mid_confidence() -> None:
    ledger = ProvenanceLedger()
    rec = ledger.record(
        claim_text="Maybe true",
        sources=[],
        step_id="step_3",
        model_id="gpt-4",
        confidence=0.6,
    )
    # Has confidence >= 0.5 but no sources → "unknown"
    assert rec.verification_status == "unknown"


def test_ledger_export_is_json_serializable() -> None:
    import json
    ledger = ProvenanceLedger()
    ledger.record(
        claim_text="Claim 1",
        sources=[_make_source("web", "https://news.com")],
        step_id="step_4",
        model_id="claude-3",
        confidence=0.8,
    )
    exported = ledger.export()
    serialized = json.dumps(exported)
    assert "Claim 1" in serialized
    assert "sources" in serialized


def test_ledger_clear_removes_all() -> None:
    ledger = ProvenanceLedger()
    ledger.record(
        claim_text="Claim A", sources=[_make_source()],
        step_id="s1", model_id="m1", confidence=0.7,
    )
    ledger.record(
        claim_text="Claim B", sources=[_make_source()],
        step_id="s2", model_id="m1", confidence=0.8,
    )
    assert len(ledger.list_all()) == 2
    ledger.clear()
    assert len(ledger.list_all()) == 0


def test_ledger_multiple_sources_per_claim() -> None:
    ledger = ProvenanceLedger()
    sources = [
        _make_source("knowledge_base", "https://kb.example.com"),
        _make_source("web", "https://web.example.com"),
    ]
    rec = ledger.record(
        claim_text="Multi-source claim",
        sources=sources,
        step_id="step_5",
        model_id="gpt-4",
        confidence=0.85,
    )
    assert len(rec.supporting_sources) == 2
    exported = rec.to_dict()
    assert len(exported["sources"]) == 2
