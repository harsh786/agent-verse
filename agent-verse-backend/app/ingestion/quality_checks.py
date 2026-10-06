"""QualityChecker — validates chunks before ingestion."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_NOISE_PATTERN = re.compile(r"^[\s\.\-_=+*#@!?/\\|<>(){}\[\]]+$")

# Below this many characters a noise ratio means nothing: a short text is kept
# when it carries real content (a one-line policy, ``tag: urgent``, a product
# code page ``SKU TJ-5531``) and skipped only when it is empty or boilerplate.
SHORT_TEXT_CHARS = 50

# A content token: a word of 2+ letters, or any alphanumeric token with a digit
# (``5531``, ``A1``, ``v2``).
_CONTENT_TOKEN = re.compile(r"[^\W\d_]{2,}|[^\W_]*\d[^\W_]*")

# Whole-text boilerplate of a near-empty page (normalised: lower case, letters,
# digits and single spaces only). Matched only against SHORT texts.
_BOILERPLATE_TEXTS = frozenset(
    {
        "loading", "loading please wait", "please wait", "redirecting",
        "page not found", "not found", "404", "404 not found", "404 page not found",
        "403", "403 forbidden", "forbidden", "access denied", "error",
        "untitled", "untitled document", "untitled page", "no content", "no title",
        "coming soon", "under construction", "intentionally left blank",
        "this page intentionally left blank", "this page is intentionally left blank",
        "javascript is required", "please enable javascript", "enable javascript",
        "you need to enable javascript to run this app",
        "javascript must be enabled", "skip to content", "skip to main content",
        "click here", "lorem ipsum", "home", "menu", "null", "none", "undefined",
        "n a", "na", "tbd", "all rights reserved",
    }
)
_COPYRIGHT_LINE = re.compile(
    r"^(?:copyright|\(c\)|©)?\s*(?:©\s*)?[\d\s,\-\u2013]*[^\n]{0,40}all rights reserved\.?$",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).replace("_", " ").split())


def boilerplate_reason(text: str) -> str | None:
    """Why ``text`` is not worth indexing (``empty`` / ``noise`` / ``boilerplate``), or None.

    Length alone never disqualifies a text: only empty, punctuation-only,
    token-free, or a short text that is entirely a known page-chrome phrase
    ("Loading...", "404 Not Found", "© 2024 Acme. All rights reserved.").
    """
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if _NOISE_PATTERN.match(stripped) or not _CONTENT_TOKEN.search(stripped):
        return "noise"
    if len(stripped) < SHORT_TEXT_CHARS * 2 and (
        _normalise(stripped) in _BOILERPLATE_TEXTS or _COPYRIGHT_LINE.match(stripped)
    ):
        return "boilerplate"
    return None


def is_meaningful_text(text: str) -> bool:
    """True when ``text`` carries indexable content, however short."""
    return boilerplate_reason(text) is None


@dataclass
class QualityCheckResult:
    passed: bool
    quality_score: float
    reason: str = ""


class QualityChecker:
    def __init__(self, min_length: int = 10, max_noise_ratio: float = 0.5) -> None:
        self._min_len = min_length
        self._max_noise = max_noise_ratio

    def check(self, content: str) -> QualityCheckResult:
        if not content or not content.strip():
            return QualityCheckResult(False, 0.0, "empty content")
        if len(content.strip()) < self._min_len:
            return QualityCheckResult(False, 0.1, f"too short (min {self._min_len} chars)")
        if _NOISE_PATTERN.match(content.strip()):
            return QualityCheckResult(False, 0.0, "noise-only content")
        words = re.findall(r"\b\w{3,}\b", content)
        word_chars = sum(len(w) for w in words)
        quality = min(1.0, word_chars / max(len(content), 1) * 1.5)
        passed = quality > self._max_noise
        return QualityCheckResult(
            passed,
            round(quality, 3),
            reason="" if passed else "low quality content",
        )


@dataclass
class DeduplicationResult:
    unique_chunks: list[str]
    duplicate_count: int
    seen_hashes: set[str] = field(default_factory=set)


class ContentDeduplicator:
    """Session-scoped chunk deduplicator using SHA-256 content hashes.

    Eliminates duplicate text chunks within a single ingestion batch.
    For cross-batch dedup, pass in a pre-seeded *seen_hashes* set.
    """

    def __init__(self, seen_hashes: set[str] | None = None) -> None:
        self._seen: set[str] = seen_hashes or set()

    @staticmethod
    def hash_chunk(content: str) -> str:
        """Return the SHA-256 hex digest of the normalised chunk content."""
        normalised = content.strip()
        return hashlib.sha256(normalised.encode()).hexdigest()

    def deduplicate(self, chunks: list[str]) -> DeduplicationResult:
        """Filter *chunks* to only those whose hash has not been seen before."""
        unique: list[str] = []
        dupe_count = 0
        for chunk in chunks:
            h = self.hash_chunk(chunk)
            if h in self._seen:
                dupe_count += 1
            else:
                self._seen.add(h)
                unique.append(chunk)
        return DeduplicationResult(
            unique_chunks=unique,
            duplicate_count=dupe_count,
            seen_hashes=self._seen,
        )

    def is_duplicate(self, content: str) -> bool:
        """Return True if this exact content has already been processed."""
        return self.hash_chunk(content) in self._seen
