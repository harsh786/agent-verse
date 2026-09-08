"""Unit tests for the RSS/Atom feed parser used by the RSS_FEED poller (2.W-1)."""

from __future__ import annotations

from app.triggers.rss import FeedEntry, new_entries, parse_feed

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item><title>First post</title><link>https://ex.com/1</link><guid>guid-1</guid></item>
  <item><title>Second post</title><link>https://ex.com/2</link><guid>guid-2</guid></item>
</channel></rss>"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Ex</title>
  <entry><id>atom-1</id><title>A1</title><link href="https://ex.com/a1"/></entry>
  <entry><id>atom-2</id><title>A2</title><link href="https://ex.com/a2"/></entry>
</feed>"""


def test_parse_rss_items() -> None:
    entries = parse_feed(_RSS)
    assert [e.entry_id for e in entries] == ["guid-1", "guid-2"]
    assert entries[0].title == "First post"
    assert entries[0].link == "https://ex.com/1"


def test_parse_atom_entries() -> None:
    entries = parse_feed(_ATOM)
    assert [e.entry_id for e in entries] == ["atom-1", "atom-2"]
    assert entries[1].link == "https://ex.com/a2"


def test_parse_falls_back_to_link_or_title_when_no_guid() -> None:
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>No guid</title><link>https://ex.com/x</link></item>"
        "<item><title>Only title</title></item>"
        "</channel></rss>"
    )
    entries = parse_feed(xml)
    assert [e.entry_id for e in entries] == ["https://ex.com/x", "Only title"]


def test_parse_malformed_returns_empty() -> None:
    assert parse_feed("not xml at all <<<") == []


def test_parse_rejects_dtd_entity_xxe() -> None:
    # Billion-laughs / XXE payloads must be refused, not parsed.
    evil = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        "<rss version=\"2.0\"><channel><item><guid>&lol;</guid></item></channel></rss>"
    )
    assert parse_feed(evil) == []


def test_new_entries_dedup() -> None:
    entries = [FeedEntry("a", "A", ""), FeedEntry("b", "B", ""), FeedEntry("c", "C", "")]
    fresh = new_entries(entries, processed_ids={"a", "c"})
    assert [e.entry_id for e in fresh] == ["b"]
