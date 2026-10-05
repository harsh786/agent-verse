"""Query analysis for the lexical retrieval legs (full-text, trigram, BM25).

A natural-language question ("Using the fleet workbook, calculate the total H1
diesel cost in INR ...") never has every one of its words in one chunk, so a
full-text query that ANDs every term (``plainto_tsquery``) matches nothing.
This module turns a query into a bounded set of *significant terms* — stop
words dropped, identifiers first, at most :data:`MAX_LEXICAL_TERMS` — that the
full-text leg ORs together and ranks, while still rewarding chunks that match
every term.

Identifiers and hyphenated codes ("TJ-5531", "out-of-gauge", "MSKU-4102") are
kept whole *and* split into their parts, so a chunk that spells the code the
same way, or only mentions a part of it, can still be found.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

#: Upper bound on the number of OR-ed terms the full-text leg sends to
#: Postgres. Each term is one ``plainto_tsquery`` call and one GIN lookup, so
#: the cost of a query is bounded no matter how long the step text is.
MAX_LEXICAL_TERMS = 16

#: A query whose significant terms number at most this is a "short" query: the
#: exact-phrase leg also searches for the whole query as one phrase.
MAX_PHRASE_WORDS = 8

# Unicode hyphens / dashes that PDF and Office extractors emit inside codes
# ("TJ<U+2011>5531") are folded to ASCII "-" before tokenizing.
_HYPHENS = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2212\u00ad", "-"))

# A word, optionally joined to further words by "-", "_", "/" or "." with no
# spaces: "TJ-5531", "out-of-gauge", "diesel_cost_inr", "v2.3.1".
_COMPOUND_RE = re.compile(r"[^\W_]+(?:[-_./][^\W_]+)*")
_PART_RE = re.compile(r"[^\W_]+")

# The Postgres ``english`` text-search stop list (Snowball), plus question
# fillers that carry no retrieval signal on their own.
_STOPWORD_TEXT = """
    a about above after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers herself him
    himself his how i if in into is it its itself just me more most my myself no
    nor not now of off on once only or other our ours ourselves out over own same
    she should so some such than that the their theirs them themselves then there
    these they this those through to too under until up very was we were what when
    where which while who whom why will with would you your yours yourself
    yourselves s t don
    much many please tell show give find using use
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def normalize_hyphens(text: str) -> str:
    """Fold Unicode hyphens/dashes to ASCII ``-``."""
    return text.translate(_HYPHENS)


def is_identifier(token: str) -> bool:
    """A code-like token: joined parts ("TJ-5531") or letters mixed with digits."""
    if len(token) < 2:
        return False
    if any(sep in token for sep in "-_./"):
        return True
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


@dataclass(frozen=True)
class LexicalQuery:
    """The analysed form of one retrieval query."""

    #: Significant terms, identifiers first, de-duplicated, at most
    #: :data:`MAX_LEXICAL_TERMS`. Compound identifiers appear whole.
    terms: tuple[str, ...]
    #: Code-like tokens of the query in their original spelling ("TJ-5531").
    identifiers: tuple[str, ...]
    #: Number of significant (non-stop-word) words in the query.
    significant_word_count: int
    #: Significant tokens (identifiers included) in query order.
    ordered_tokens: tuple[str, ...] = ()
    #: The query with hyphens folded and surrounding punctuation stripped.
    normalized: str = ""
    #: Codes the user typed with a space ("TJ 5531"): their hyphenated and
    #: joined spellings ("TJ-5531", "TJ5531"), searched as exact phrases and
    #: full-text terms. Postgres' parser reads "TJ-5531" as 'tj' + '-5531', so
    #: the bare "5531" of the spaced form never matches it in full text.
    derived_identifiers: tuple[str, ...] = ()

    @property
    def is_long(self) -> bool:
        return self.significant_word_count > MAX_PHRASE_WORDS

    def fts_terms(self) -> tuple[str, ...]:
        """Terms for the full-text OR: :attr:`terms` plus the separator-free
        spelling of each compound identifier ("TJ-5531" → "TJ5531"), so a chunk
        that writes the code without its hyphen still matches. Bounded by
        ``2 * MAX_LEXICAL_TERMS``."""
        out = list(self.terms)
        for derived in self.derived_identifiers:
            if derived.casefold() not in {t.casefold() for t in out}:
                out.append(derived)
        for ident in self.identifiers:
            joined = _SEPARATORS_RE.sub("", ident)
            if joined != ident and joined.casefold() not in {t.casefold() for t in out}:
                out.append(joined)
        return tuple(out[: 2 * MAX_LEXICAL_TERMS])

    def trigram_text(self) -> str:
        """What the fuzzy (pg_trgm word-similarity) leg looks for.

        Identifiers when the query has any (a code is what a typo-tolerant
        match helps most), else the significant words of a short query in
        their original order. Empty for a long query without identifiers: no
        chunk contains a long question's words contiguously, so the leg would
        only add noise.
        """
        if self.identifiers:
            return " ".join(self.identifiers)[:200]
        if self.is_long:
            return ""
        return " ".join(self.ordered_tokens)[:200]

    def phrases(self) -> tuple[tuple[str, float], ...]:
        """Exact phrases and their weights for the exact-match leg.

        The whole (short) query counts double; every identifier of 3+
        characters counts once. Phrases shorter than 3 characters are dropped
        (no trigram index support, and no signal).
        """
        out: dict[str, float] = {}
        whole = self.normalized
        if not self.is_long and len(whole) >= 3 and self.significant_word_count >= 1:
            out[whole.casefold()] = 2.0
        for ident in (*self.identifiers, *self.derived_identifiers):
            if len(ident) >= 3:
                out.setdefault(ident.casefold(), 1.0)
        return tuple(out.items())[:MAX_LEXICAL_TERMS]


_SEPARATORS_RE = re.compile(r"[-_./]")
_EDGE_PUNCT = " \t\r\n?!.,;:\"'`()[]{}"


def analyze_query(query: str) -> LexicalQuery:
    """Split ``query`` into significant terms and identifiers.

    Order of :attr:`LexicalQuery.terms`: identifiers (as written in the query),
    then the remaining words longest first (longer words are rarer and more
    discriminating), ties by position. The cut-off at
    :data:`MAX_LEXICAL_TERMS` therefore drops the least informative words.
    """
    normalized = normalize_hyphens(query)
    identifiers: list[str] = []
    words: list[str] = []
    ordered: list[str] = []
    seen: set[str] = set()
    significant = 0
    for match in _COMPOUND_RE.finditer(normalized):
        token = match.group(0).strip("./")
        if not token:
            continue
        lowered = token.casefold()
        if is_identifier(token):
            significant += 1
            ordered.append(token)
            if lowered not in seen:
                seen.add(lowered)
                identifiers.append(token)
            continue
        if lowered in STOPWORDS or len(lowered) < 2:
            continue
        significant += 1
        ordered.append(lowered)
        if lowered not in seen:
            seen.add(lowered)
            words.append(lowered)
    derived = _spaced_codes(ordered)
    ranked_words = sorted(words, key=lambda w: -len(w))  # stable: ties keep position
    terms = (*identifiers, *ranked_words)[:MAX_LEXICAL_TERMS]
    return LexicalQuery(
        terms=tuple(terms),
        identifiers=tuple(identifiers),
        significant_word_count=significant,
        ordered_tokens=tuple(ordered[: 2 * MAX_LEXICAL_TERMS]),
        normalized=" ".join(normalized.split()).strip(_EDGE_PUNCT)[:200],
        derived_identifiers=derived,
    )


def _spaced_codes(tokens: list[str]) -> tuple[str, ...]:
    """Hyphenated + joined spellings of "<letters> <digits>" pairs ("TJ 5531").

    Only a short alphabetic prefix (1-4 letters) followed by a number of 2+
    digits counts, which is the shape of job / invoice / ticket codes. At most
    ``MAX_LEXICAL_TERMS // 2`` pairs are kept.
    """
    out: list[str] = []
    for prefix, number in itertools.pairwise(tokens):
        if prefix.isalpha() and 1 <= len(prefix) <= 4 and number.isdigit() and len(number) >= 2:
            out.extend((f"{prefix}-{number}", f"{prefix}{number}"))
        if len(out) >= MAX_LEXICAL_TERMS:
            break
    return tuple(out)


def identifier_parts(token: str) -> list[str]:
    """The word parts of a compound identifier ("TJ-5531" → ["tj", "5531"])."""
    return [p.casefold() for p in _PART_RE.findall(normalize_hyphens(token))]


def compound_tokens(text: str) -> list[str]:
    """Lower-cased compound tokens of ``text`` that contain a separator
    ("TJ-5531" → "tj-5531", "out-of-gauge"), hyphens folded. Used by BM25 to
    index a code whole next to its parts."""
    return [
        m.group(0).casefold()
        for m in _COMPOUND_RE.finditer(normalize_hyphens(text))
        if _SEPARATORS_RE.search(m.group(0))
    ]
