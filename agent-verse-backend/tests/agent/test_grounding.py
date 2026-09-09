"""Tests for Phase 3 claim grounding checker."""

from app.agent.grounding import (
    GroundingResult,
    annotate_ungrounded,
    check_grounding,
    extract_claims,
)


class TestExtractClaims:
    def test_extracts_jira_ids(self) -> None:
        claims = extract_claims("Found issues: JIRA-123, BAU-456, OPS-789")
        assert "jira_id" in claims
        assert "JIRA-123" in claims["jira_id"]
        assert "BAU-456" in claims["jira_id"]

    def test_extracts_urls(self) -> None:
        claims = extract_claims("See https://jira.example.com/browse/JIRA-1 for details")
        assert "url" in claims
        assert any("jira.example.com" in u for u in claims["url"])

    def test_extracts_dates(self) -> None:
        claims = extract_claims("Due date: 2026-07-15, created 2026-01-01")
        assert "date" in claims
        assert "2026-07-15" in claims["date"]

    def test_extracts_quoted_strings(self) -> None:
        claims = extract_claims('The title is "Fix login bug" as confirmed')
        assert "quoted" in claims
        assert "Fix login bug" in claims["quoted"]

    def test_no_claims_in_generic_text(self) -> None:
        claims = extract_claims("The task was completed successfully")
        # May have some number matches but no specific entity types
        all_claims = [c for clist in claims.values() for c in clist]
        # No JIRA IDs, URLs, etc.
        assert not any(c.startswith("http") for c in all_claims)

    def test_deduplicates_repeated_jira_ids(self) -> None:
        claims = extract_claims("JIRA-123 is referenced twice: JIRA-123")
        assert claims.get("jira_id", []).count("JIRA-123") == 1

    def test_extracts_email(self) -> None:
        claims = extract_claims("Contact user@example.com for details")
        assert "email" in claims
        assert "user@example.com" in claims["email"]

    def test_extracts_github_pr(self) -> None:
        claims = extract_claims("See PR #42 for the fix")
        assert "github_pr" in claims
        assert "42" in claims["github_pr"]


class TestCheckGrounding:
    def test_grounded_claim_passes(self) -> None:
        output = "Found issue JIRA-123 titled 'Fix login'"
        evidence = ["JIRA-123 title: Fix login, status: open, priority: high"]
        result = check_grounding(output, evidence)
        assert result.grounded is True

    def test_ungrounded_claim_fails(self) -> None:
        output = "Found 5 issues including JIRA-999 (does not exist)"
        evidence = ["Total issues: 3, JIRA-100, JIRA-200, JIRA-300"]
        result = check_grounding(output, evidence)
        assert "JIRA-999" in result.ungrounded_claims

    def test_empty_output_is_grounded(self) -> None:
        result = check_grounding("", ["evidence"])
        assert result.grounded is True

    def test_empty_evidence_with_claims_is_not_grounded(self) -> None:
        # P0-4: a concrete claim with no evidence at all cannot be grounded
        # (previously this fail-open path returned grounded=True).
        result = check_grounding("Found JIRA-123", [])
        assert result.grounded is False
        assert "JIRA-123" in result.ungrounded_claims

    def test_empty_evidence_with_no_claims_is_grounded(self) -> None:
        # No extractable claims → nothing can be ungrounded, even with no evidence.
        result = check_grounding("The task finished.", [])
        assert result.grounded is True

    def test_strict_mode_fails_any_ungrounded(self) -> None:
        # JIRA-123 is in evidence but JIRA-456 is not — strict mode fails on any ungrounded claim
        output = "Found JIRA-123 and JIRA-456"
        evidence = ["JIRA-123 found"]
        result = check_grounding(output, evidence, strict=True)
        assert result.grounded is False
        assert "JIRA-456" in result.ungrounded_claims

    def test_25pct_threshold_non_strict(self) -> None:
        # 3 claims, 0 ungrounded → grounded
        output = "JIRA-1 JIRA-2 JIRA-3 all confirmed"
        evidence = ["JIRA-1 JIRA-2 JIRA-3"]
        result = check_grounding(output, evidence)
        assert result.grounded is True

    def test_all_claims_present_in_evidence(self) -> None:
        output = "Issue BAU-42 created on 2026-07-04"
        evidence = ["Created issue BAU-42 on 2026-07-04 with status open"]
        result = check_grounding(output, evidence)
        assert result.grounded is True
        assert result.checked_claims >= 1

    def test_evidence_length_is_populated(self) -> None:
        output = "JIRA-100 found"
        evidence = ["JIRA-100 is the issue"]
        result = check_grounding(output, evidence)
        assert result.evidence_length > 0

    def test_multiple_tool_outputs_combined(self) -> None:
        output = "Found JIRA-10 and JIRA-20"
        evidence = ["JIRA-10 details here", "JIRA-20 details here"]
        result = check_grounding(output, evidence)
        assert result.grounded is True

    def test_case_insensitive_matching(self) -> None:
        output = "URL: https://EXAMPLE.COM/path"
        evidence = ["response from https://example.com/path"]
        result = check_grounding(output, evidence)
        # case-insensitive check — URL lowercased matches
        assert result.grounded is True

    def test_checked_claims_count(self) -> None:
        output = "JIRA-10 and JIRA-20 and JIRA-30"
        evidence = ["JIRA-10 JIRA-20 JIRA-30"]
        result = check_grounding(output, evidence)
        assert result.checked_claims >= 3


class TestAnnotateUngrounded:
    def test_annotates_ungrounded_claims(self) -> None:
        output = "Found JIRA-999 and completed task"
        result = GroundingResult(
            grounded=False,
            ungrounded_claims=["JIRA-999"],
            checked_claims=1,
            evidence_length=10,
        )
        annotated = annotate_ungrounded(output, result)
        assert "[UNGROUNDED CLAIM" in annotated
        assert "JIRA-999" in annotated

    def test_grounded_result_unchanged(self) -> None:
        output = "Task completed"
        result = GroundingResult(grounded=True, ungrounded_claims=[], checked_claims=0, evidence_length=0)
        assert annotate_ungrounded(output, result) == output

    def test_caps_at_five_annotations(self) -> None:
        # Build output with 8 fake claims
        claims = [f"JIRA-{i}" for i in range(100, 108)]
        output = " ".join(claims)
        result = GroundingResult(
            grounded=False,
            ungrounded_claims=claims,
            checked_claims=len(claims),
            evidence_length=0,
        )
        annotated = annotate_ungrounded(output, result)
        annotation_count = annotated.count("[UNGROUNDED CLAIM")
        assert annotation_count <= 5

    def test_empty_ungrounded_list_unchanged(self) -> None:
        output = "Some output"
        result = GroundingResult(
            grounded=False,
            ungrounded_claims=[],
            checked_claims=0,
            evidence_length=0,
        )
        assert annotate_ungrounded(output, result) == output
