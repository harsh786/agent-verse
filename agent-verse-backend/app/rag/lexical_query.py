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

    @property
    def is_long(self) -> bool:
        return self.significant_word_count > MAX_PHRASE_WORDS


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
    seen: set[str] = set()
    significant = 0
    for match in _COMPOUND_RE.finditer(normalized):
        token = match.group(0).strip("./")
        if not token:
            continue
        lowered = token.casefold()
        if is_identifier(token):
            significant += 1
            if lowered not in seen:
                seen.add(lowered)
                identifiers.append(token)
            continue
        if lowered in STOPWORDS or len(lowered) < 2:
            continue
        significant += 1
        if lowered not in seen:
            seen.add(lowered)
            words.append(lowered)
    ranked_words = sorted(words, key=lambda w: -len(w))  # stable: ties keep position
    terms = (*identifiers, *ranked_words)[:MAX_LEXICAL_TERMS]
    return LexicalQuery(
        terms=tuple(terms),
        identifiers=tuple(identifiers),
        significant_word_count=significant,
    )


def identifier_parts(token: str) -> list[str]:
    """The word parts of a compound identifier ("TJ-5531" → ["tj", "5531"])."""
    return [p.casefold() for p in _PART_RE.findall(normalize_hyphens(token))]
