"""Minimal RSS/Atom feed parsing for the RSS_FEED trigger poller (2.W-1).

Dependency-light: parses with the stdlib ``xml.etree.ElementTree`` (no feedparser)
and fetches with ``httpx``. ``fetch_rss_entries`` is the single network seam so
tests monkeypatch it; ``parse_feed`` is pure and unit-tested directly.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Hardening: RSS/Atom feeds never need a DTD or custom entities. Rejecting them
# blocks XXE (external entities) and billion-laughs entity-expansion attacks
# without the defusedxml dependency. Feeds are untrusted external input.
_UNSAFE_XML = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_MAX_FEED_BYTES = 8 * 1024 * 1024  # cap parsed feed size


@dataclass(frozen=True)
class FeedEntry:
    entry_id: str  # stable id for dedup (guid / atom id / link / title)
    title: str
    link: str


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_feed(xml_text: str | bytes) -> list[FeedEntry]:
    """Parse RSS 2.0 (``<item>``) or Atom (``<entry>``) into FeedEntry list.

    Returns [] on malformed XML rather than raising — a bad feed must not crash
    the beat loop.
    """
    if isinstance(xml_text, bytes):
        xml_text = xml_text.decode("utf-8", errors="replace")
    # Reject oversized or DTD/entity-bearing feeds before handing to the parser.
    if len(xml_text) > _MAX_FEED_BYTES or _UNSAFE_XML.search(xml_text):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    entries: list[FeedEntry] = []

    # RSS 2.0: channel/item
    for item in root.iter("item"):
        guid = _text(item.find("guid"))
        link = _text(item.find("link"))
        title = _text(item.find("title"))
        entry_id = guid or link or title
        if entry_id:
            entries.append(FeedEntry(entry_id=entry_id, title=title, link=link))

    # Atom: feed/entry
    for entry in root.iter(f"{_ATOM_NS}entry"):
        aid = _text(entry.find(f"{_ATOM_NS}id"))
        title = _text(entry.find(f"{_ATOM_NS}title"))
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "") if link_el is not None else ""
        entry_id = aid or link or title
        if entry_id:
            entries.append(FeedEntry(entry_id=entry_id, title=title, link=link))

    return entries


def new_entries(entries: list[FeedEntry], processed_ids: set[str]) -> list[FeedEntry]:
    """Entries whose id has not been seen before (preserves feed order)."""
    return [e for e in entries if e.entry_id not in processed_ids]


def fetch_rss_entries(url: str, *, timeout: float = 10.0) -> list[FeedEntry]:
    """Fetch and parse a feed URL. Network/parse failures yield [] (logged by
    the caller). This is the seam tests monkeypatch to avoid real network."""
    import httpx

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return parse_feed(resp.text)
