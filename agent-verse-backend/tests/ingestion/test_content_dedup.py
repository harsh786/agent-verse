"""Tests for ContentDeduplicator in quality_checks."""
from __future__ import annotations

from app.ingestion.quality_checks import ContentDeduplicator, QualityChecker


class TestContentDeduplicator:
    def test_first_chunk_is_not_duplicate(self) -> None:
        dedup = ContentDeduplicator()
        assert dedup.is_duplicate("hello world") is False

    def test_second_identical_chunk_is_duplicate(self) -> None:
        dedup = ContentDeduplicator()
        dedup.deduplicate(["hello world"])
        assert dedup.is_duplicate("hello world") is True

    def test_deduplicate_removes_duplicates(self) -> None:
        dedup = ContentDeduplicator()
        chunks = ["alpha", "beta", "alpha", "gamma", "beta"]
        result = dedup.deduplicate(chunks)
        assert result.unique_chunks == ["alpha", "beta", "gamma"]
        assert result.duplicate_count == 2

    def test_deduplicate_empty_list(self) -> None:
        dedup = ContentDeduplicator()
        result = dedup.deduplicate([])
        assert result.unique_chunks == []
        assert result.duplicate_count == 0

    def test_seeded_dedup_from_existing_hashes(self) -> None:
        dedup1 = ContentDeduplicator()
        r1 = dedup1.deduplicate(["foo", "bar"])
        # Use the seen hashes from the first run in a second deduplicator
        dedup2 = ContentDeduplicator(seen_hashes=r1.seen_hashes)
        r2 = dedup2.deduplicate(["foo", "baz"])
        # "foo" was seen in the first run; "baz" is new
        assert "baz" in r2.unique_chunks
        assert "foo" not in r2.unique_chunks
        assert r2.duplicate_count == 1

    def test_hash_chunk_is_deterministic(self) -> None:
        h1 = ContentDeduplicator.hash_chunk("same text")
        h2 = ContentDeduplicator.hash_chunk("same text")
        assert h1 == h2

    def test_hash_chunk_ignores_leading_trailing_whitespace(self) -> None:
        h1 = ContentDeduplicator.hash_chunk("text")
        h2 = ContentDeduplicator.hash_chunk("  text  ")
        assert h1 == h2
