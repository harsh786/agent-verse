"""Tests for app.provenance.export.ProvenanceExport.

Covers JSON export, citation-list projection, and summary statistics across
empty, single, and multi-record inputs, including mixed verification statuses.
"""
from __future__ import annotations

import json

from app.provenance.claim_trace import ProvenanceRecord
from app.provenance.export import ProvenanceExport
from app.provenance.source_ref import SourceRef


def _record(
    claim_id: str = "claim-1",
    claim_text: str = "The sky is blue.",
    confidence: float = 0.9,
    verification_status: str = "supported",
    sources: list[SourceRef] | None = None,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        claim_id=claim_id,
        claim_text=claim_text,
        supporting_sources=sources if sources is not None else [SourceRef(source_type="web", url="https://example.com")],
        generated_by_step="verify",
        generated_by_model="fake-model",
        confidence=confidence,
        verification_status=verification_status,
    )


class TestToJson:
    def test_empty_list_produces_empty_json_array(self):
        export = ProvenanceExport()
        assert json.loads(export.to_json([])) == []

    def test_serializes_records_to_valid_json(self):
        export = ProvenanceExport()
        records = [_record(claim_id="c1"), _record(claim_id="c2", verification_status="unknown")]
        result = json.loads(export.to_json(records))
        assert len(result) == 2
        assert result[0]["claim_id"] == "c1"
        assert result[1]["verification_status"] == "unknown"

    def test_output_is_indented(self):
        export = ProvenanceExport()
        text = export.to_json([_record()])
        assert "\n" in text


class TestToCitationList:
    def test_empty_list(self):
        export = ProvenanceExport()
        assert export.to_citation_list([]) == []

    def test_truncates_claim_text_to_200_chars(self):
        export = ProvenanceExport()
        long_claim = "x" * 500
        record = _record(claim_text=long_claim)
        citations = export.to_citation_list([record])
        assert len(citations[0]["claim"]) == 200
        assert citations[0]["claim"] == long_claim[:200]

    def test_includes_confidence_status_and_sources(self):
        export = ProvenanceExport()
        source = SourceRef(source_type="doc", url="https://docs.example.com", chunk_id="chunk-9")
        record = _record(confidence=0.42, verification_status="unsupported", sources=[source])
        citations = export.to_citation_list([record])
        assert citations[0]["confidence"] == 0.42
        assert citations[0]["status"] == "unsupported"
        assert citations[0]["sources"] == [source.to_dict()]

    def test_multiple_records_preserve_order(self):
        export = ProvenanceExport()
        records = [_record(claim_id=f"c{i}", claim_text=f"claim {i}") for i in range(3)]
        citations = export.to_citation_list(records)
        assert [c["claim"] for c in citations] == ["claim 0", "claim 1", "claim 2"]


class TestToSummary:
    def test_empty_list_returns_zeroed_summary(self):
        export = ProvenanceExport()
        summary = export.to_summary([])
        assert summary == {"total": 0, "supported": 0, "unsupported": 0, "unknown": 0}
        assert "avg_confidence" not in summary

    def test_counts_each_verification_status(self):
        export = ProvenanceExport()
        records = [
            _record(claim_id="c1", verification_status="supported", confidence=1.0),
            _record(claim_id="c2", verification_status="supported", confidence=0.8),
            _record(claim_id="c3", verification_status="unsupported", confidence=0.2),
            _record(claim_id="c4", verification_status="unknown", confidence=0.5),
        ]
        summary = export.to_summary(records)
        assert summary["total"] == 4
        assert summary["supported"] == 2
        assert summary["unsupported"] == 1
        assert summary["unknown"] == 1
        assert summary["avg_confidence"] == round((1.0 + 0.8 + 0.2 + 0.5) / 4, 3)

    def test_avg_confidence_rounded_to_three_decimals(self):
        export = ProvenanceExport()
        records = [
            _record(claim_id="c1", confidence=1 / 3),
            _record(claim_id="c2", confidence=1 / 7),
        ]
        summary = export.to_summary(records)
        assert summary["avg_confidence"] == round((1 / 3 + 1 / 7) / 2, 3)

    def test_status_outside_known_set_is_not_counted(self):
        export = ProvenanceExport()
        records = [_record(verification_status="pending")]
        summary = export.to_summary(records)
        assert summary["total"] == 1
        assert summary["supported"] == 0
        assert summary["unsupported"] == 0
        assert summary["unknown"] == 0
