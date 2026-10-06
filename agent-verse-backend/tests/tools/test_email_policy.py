"""a02-F036-02 (a): the per-tenant recipient allowlist of the agent email tool."""

from __future__ import annotations

import pytest

from app.tools.email_policy import (
    MAX_ALLOWLIST_ENTRIES,
    AllowlistEntryError,
    disallowed_recipients,
    normalize_allowlist,
    recipient_allowed,
)


def test_entries_are_normalized_and_deduplicated() -> None:
    assert normalize_allowlist(
        [" Alice@Example.com ", "@Partner.io", "*.Corp.example", "partner.io", "alice@example.com"]
    ) == ["alice@example.com", "partner.io", "*.corp.example"]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not a domain",
        "localhost",
        "a@b",
        "@",
        "*.",
        "*.x",
        "a@@example.com",
        "bad\nexample.com",
        "x" * 300 + ".com",
    ],
)
def test_malformed_entries_are_refused(bad: str) -> None:
    with pytest.raises(AllowlistEntryError):
        normalize_allowlist([bad])


def test_entry_count_is_bounded() -> None:
    entries = [f"d{i}.example.com" for i in range(MAX_ALLOWLIST_ENTRIES + 1)]
    with pytest.raises(AllowlistEntryError):
        normalize_allowlist(entries)


def test_empty_allowlist_allows_everyone() -> None:
    assert disallowed_recipients(["anyone@anywhere.test", "x@y.io"], []) == []


def test_address_domain_and_wildcard_matching() -> None:
    allow = normalize_allowlist(["boss@example.com", "partner.io", "*.corp.example"])
    assert recipient_allowed("BOSS@example.com", allow)
    assert not recipient_allowed("intern@example.com", allow)  # address entry only
    assert recipient_allowed("anyone@partner.io", allow)
    assert not recipient_allowed("anyone@sub.partner.io", allow)  # domain = exact
    assert not recipient_allowed("anyone@evilpartner.io", allow)
    assert recipient_allowed("ops@eu.corp.example", allow)
    assert not recipient_allowed("ops@corp.example", allow)  # wildcard = subdomains
    assert not recipient_allowed("ops@evilcorp.example", allow)
    assert not recipient_allowed("no-at-sign", allow)


def test_any_disallowed_recipient_is_reported() -> None:
    allow = ["partner.io"]
    assert disallowed_recipients(["a@partner.io", "b@else.com", "c@partner.io"], allow) == [
        "b@else.com"
    ]
