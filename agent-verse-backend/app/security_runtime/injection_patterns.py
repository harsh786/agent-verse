"""Shared, normalized prompt-injection detector.

One curated pattern set used by every injection check in the backend:

* ``app.guardrails_v2.engine`` (the ``prompt_injection`` guardrail rule, and
  through it the memory-write gate in ``app.memory.screening``);
* ``app.security_runtime.guardrail_enforcer`` (local tool-args/output scan);
* ``app.agent.exfil_guard.check_tool_output_for_injection`` (tool output);
* ``app.intelligence.indirect_injection`` (retrieved content / RAG chunks).

Each call site keeps its own return shape and adds its context-specific
patterns (SQL for the enforcer, the ``INDIRECT``/``EXFIL`` sets for the two
content scanners).

Matching runs on a **normalized view** of the text:

1. compatibility folding (NFKD, combining marks dropped — so full-width,
   mathematical-alphanumeric and accented look-alikes become plain ASCII; this
   is a superset of NFKC for matching purposes);
2. zero-width, bidi-control, soft-hyphen and variation-selector characters are
   removed; Unicode *tag* characters (U+E0020..U+E007E, "ASCII smuggling") are
   decoded to the ASCII they hide;
3. Cyrillic/Greek/Armenian confusables are folded to Latin, typographic quotes
   and dashes to ASCII, everything is lower-cased;
4. whitespace runs collapse to one space (or one newline when the run holds a
   line break, so line-anchored header patterns still work);
5. when digits/symbols sit next to letters, two extra *leetspeak* views are
   also scanned (``1``→``i`` and ``1``→``l``; ``0 3 4 5 7 @ $ !``).

Patterns are written for that view: lowercase, word gaps are bounded
(``\\W+(?:\\w+\\W+){0,n}`` — ``\\w``/``\\W`` are disjoint, so a gap has a single
parse) and every repetition is bounded, so there is no catastrophic
backtracking; ``tests/security_runtime/test_injection_patterns.py`` holds a
100 KB performance guard.

Instruction-like patterns are *negatable*: a hit directly preceded by
"not / n't / never" ("Never reveal your system prompt", "do not ignore the
previous instructions") is skipped, and the scan continues past it so a later
real hit is still found.

Deliberately NOT detected (documented trade-offs, kept as strict xfails in the
corpus tests):

* letter-spaced words ("i g n o r e previous instructions") — de-spacing text
  makes ordinary prose collide with patterns;
* "disregard my previous message(s)" — everyday email language, so
  ``messages`` is not an instruction noun;
* free paraphrases with no anchor phrase ("the instructions you got earlier no
  longer matter") — that is the LLM classifier's job, not a regex's;
* non-English phrasings;
* "include the API key in the URL" (with *the*) — that is how many API docs
  read; only "in a/an link/url/image/markdown" counts as an exfil instruction.

Also by design: a *quoted* attack string ("attackers write 'ignore previous
instructions'") is still flagged — quotes are free for an attacker to add, so
abstract security text is only safe while it describes attacks without
reproducing them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

Severity = Literal["low", "medium", "high", "critical"]

# ── normalization ────────────────────────────────────────────────────────────

# Invisible / layout characters removed before matching.
_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),  # soft hyphen
    (0x034F, 0x034F),  # combining grapheme joiner
    (0x061C, 0x061C),  # arabic letter mark
    (0x115F, 0x1160),  # hangul fillers
    (0x17B4, 0x17B5),  # khmer inherent vowels (invisible)
    (0x180B, 0x180F),  # mongolian variation selectors / vowel separator
    (0x200B, 0x200F),  # zero-width space/joiners, LRM/RLM
    (0x202A, 0x202E),  # bidi embeddings / overrides
    (0x2060, 0x206F),  # word joiner, invisible operators, bidi isolates
    (0x3164, 0x3164),  # hangul filler
    (0xFE00, 0xFE0F),  # variation selectors
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
    (0xFFA0, 0xFFA0),  # half-width hangul filler
    (0xE0000, 0xE001F),  # tag block (non-ASCII part)
    (0xE007F, 0xE007F),  # cancel tag
    (0xE0100, 0xE01EF),  # variation selectors supplement
)

# Look-alikes NFKD does not fold (keys are lowercase; lookup happens after
# lower-casing). Cyrillic, Greek, Armenian and Latin-extended confusables plus
# typographic punctuation.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic
    "\u0430": "a",
    "\u0432": "b",
    "\u0435": "e",
    "\u0451": "e",
    "\u0450": "e",
    "\u043a": "k",
    "\u043c": "m",
    "\u043d": "h",
    "\u043e": "o",
    "\u0440": "p",
    "\u0441": "c",
    "\u0442": "t",
    "\u0443": "y",
    "\u0445": "x",
    "\u0456": "i",
    "\u0457": "i",
    "\u0458": "j",
    "\u0455": "s",
    "\u04bb": "h",
    "\u0501": "d",
    "\u051b": "q",
    "\u051d": "w",
    "\u04cf": "l",
    "\u0475": "v",
    "\u04af": "y",
    "\u0261": "g",
    "\u0251": "a",
    "\u0131": "i",
    "\u0237": "j",
    "\uabaa": "s",
    # Greek
    "\u03b1": "a",
    "\u03b2": "b",
    "\u03b5": "e",
    "\u03b7": "n",
    "\u03b9": "i",
    "\u03ba": "k",
    "\u03bd": "v",
    "\u03bf": "o",
    "\u03c1": "p",
    "\u03c4": "t",
    "\u03c5": "u",
    "\u03c7": "x",
    "\u03f2": "c",
    "\u03f3": "j",
    "\u03c9": "w",
    "\u03b3": "y",
    "\u03bc": "u",
    # Armenian
    "\u0578": "n",
    "\u0585": "o",
    "\u057d": "u",
    "\u0570": "h",
    "\u0581": "g",
    "\u0566": "q",
    # punctuation
    "\u2018": "'",
    "\u2019": "'",
    "\u201b": "'",
    "\u02bc": "'",
    "\u00b4": "'",
    "`": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
}

_NEWLINES = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")

_ASCII_FOLD: dict[str, str] = {chr(c): _CONFUSABLES.get(chr(c), chr(c).lower()) for c in range(128)}

# Leetspeak substitutions, applied to the already-normalized (lowercase) view.
_LEET_I = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
        "|": "i",
    }
)
_LEET_L = str.maketrans(
    {
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
        "|": "l",
    }
)
_LEET_HINT = re.compile(r"[a-z][013457@$!|]|[013457@$!|][a-z]")


def _is_invisible(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _INVISIBLE_RANGES)


@lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    """Fold one non-ASCII character to its matching form ('' = drop it)."""
    cp = ord(ch)
    if 0xE0020 <= cp <= 0xE007E:  # tag characters hide printable ASCII
        return _ASCII_FOLD[chr(cp - 0xE0000)]
    if _is_invisible(cp):
        return ""
    out: list[str] = []
    for part in unicodedata.normalize("NFKD", ch):
        if unicodedata.combining(part):
            continue
        for low in part.lower():
            if unicodedata.combining(low):
                continue
            out.append(_CONFUSABLES.get(low, low))
    return "".join(out)


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Return the matching view of *text* and, per output char, its source index."""
    chars: list[str] = []
    offsets: list[int] = []
    last_ws = True  # leading whitespace is dropped
    for i, ch in enumerate(text):
        folded = _ASCII_FOLD[ch] if ch < "\x80" else _fold_char(ch)
        for c in folded:
            if c.isspace():
                if last_ws:
                    if c in _NEWLINES and chars and chars[-1] == " ":
                        chars[-1] = "\n"
                    continue
                chars.append("\n" if c in _NEWLINES else " ")
                offsets.append(i)
                last_ws = True
            else:
                chars.append(c)
                offsets.append(i)
                last_ws = False
    return "".join(chars), offsets


def normalize_for_matching(text: str) -> str:
    """The normalized (folded, lowercase, whitespace-collapsed) view of *text*."""
    return normalize_with_offsets(text)[0]


def _views(normalized: str) -> list[tuple[str, str]]:
    views = [("plain", normalized)]
    if _LEET_HINT.search(normalized):
        views.append(("leet", normalized.translate(_LEET_I)))
        leet_l = normalized.translate(_LEET_L)
        if leet_l != views[-1][1]:
            views.append(("leet", leet_l))
    return views


# ── pattern model ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InjectionPattern:
    """One curated detection pattern (matched against the normalized view)."""

    id: str
    family: str
    severity: Severity
    regex: re.Pattern[str]
    negatable: bool = True


@dataclass(frozen=True, slots=True)
class InjectionMatch:
    """A pattern hit. ``start``/``end`` index the ORIGINAL text."""

    pattern_id: str
    family: str
    severity: Severity
    start: int
    end: int
    matched: str  # normalized matched text (for logs / reasons)
    obfuscation: str | None = None  # None | "leetspeak" | "unicode"


def _p(
    pid: str, family: str, severity: Severity, regex: str, *, negatable: bool = True
) -> InjectionPattern:
    return InjectionPattern(pid, family, severity, re.compile(regex), negatable)


def _gap(n: int) -> str:
    """A separator followed by up to *n* whole words (single parse, bounded)."""
    return rf"\W+(?:\w+\W+){{0,{n}}}?"


# ── building blocks ──────────────────────────────────────────────────────────

_STRONG_VERBS = (
    r"(?:ignore|disregard|forget|set\s+aside|throw\s+out|stop\s+following|stop\s+obeying"
    r"|cease\s+following|quit\s+following)"
)
_WEAK_VERBS = r"(?:override|overrule|bypass|discard|abandon|neglect|circumvent)"
_QUAL = (
    r"(?:previous|previously|prior|above|earlier|preceding|foregoing|former|original"
    r"|initial|existing|old|all|any|every|your|ur|system|developer)"
)
_QUAL_STRICT = (
    r"(?:previous|previously|prior|above|earlier|preceding|foregoing|former|original"
    r"|initial|your|ur|system|developer|safety|content)"
)
_NOUN = (
    r"(?:instructions?|instruction\s+set|rules?|guidelines?|directives?|prompts?|context"
    r"|commands?|guidance|constraints?|restrictions?|programming|polic(?:y|ies)|orders"
    r"|training|safeguards?|guardrails?)"
)
_JB = (
    r"(?:unrestricted|unfiltered|uncensored|unbound|unchained|unshackled|jailbroken"
    r"|jail-broken|amoral|unethical|lawless|dan"
    r"|(?:evil|rogue|malicious|unhinged)\s+(?:ai|assistant|bot|chatbot|model|version|twin"
    r"|confidant|llm|gpt)"
    r"|no\s+(?:\w+\s+)?(?:rules|restrictions|limits|limitations|filters|guidelines"
    r"|boundaries|morals|ethics|censorship|constraints|guardrails|safeguards)"
    r"|without\s+(?:any\s+)?(?:\w+\s+)?(?:rules|restrictions|limits|limitations|filters"
    r"|guidelines|boundaries|morals|ethics|censorship|constraints|guardrails|safeguards)"
    r"|(?:ignores?|ignoring|bypass(?:es|ing)?|disregards?|disregarding)\s+(?:\w+\s+){0,2}?"
    r"(?:rules|restrictions|guidelines|policies|safety|filters|ethics|instructions)"
    r"|not\s+bound\s+by"
    r"|free\s+(?:from|of)\s+(?:\w+\s+){0,2}?(?:rules|restrictions|limits|limitations"
    r"|filters|guidelines|censorship|constraints)"
    r"|developer\s+mode|god\s+mode|do\s+anything\s+now)"
)
_PERSONA_LEAD = (
    r"(?:you\s+are\s+now|you're\s+now|you\s+will\s+now\s+be"
    r"|you\s+are\s+going\s+to\s+(?:be|act\s+as|pretend)"
    r"|from\s+now\s+on,?\s+you(?:'re|\s+are|\s+will\s+be)?"
    r"|act(?:ing)?\s+as|pretend(?:ing)?\s+(?:to\s+be|you\s+are|you're|that\s+you\s+are)"
    r"|role-?\s?play(?:ing)?\s+as|behave\s+(?:as|like)|simulate|emulate|impersonate"
    r"|imagine\s+you\s+are|transform\s+into|you\s+must\s+now\s+be|henceforth,?\s+you)"
)
_REVEAL_VERB = (
    r"(?:reveal|print|show|display|output|repeat|recite|leak|dump|disclose|expose|tell"
    r"|give|share|write\s+out|spell\s+out|echo|return|provide|list|read\s+back|type\s+out)"
)
_REVEAL_FILL = (
    r"(?:\W+(?:me|us|back|out|verbatim|exactly|again|now|all|of|everything|in|full|the"
    r"|text|contents?|copy|first|complete|entire|whole|raw|word|for|to|please|it))"
)
_REVEAL_TARGET = (
    r"(?:(?:your|ur|the|its)\s+(?:\w+\s+){0,1}?(?:system|hidden|secret|initial|original"
    r"|internal|developer|confidential|underlying|pre-?|starting|core|base)\s*(?:prompt"
    r"|instructions?|message|rules|directives|guidelines|configuration|context)"
    r"|your\s+(?:prompt|programming|instructions\s+verbatim))"
)
_AI = (
    r"(?:ai|a\.i\.|llms?|gpt|chatgpt|claude|gemini|copilot|bard|chatbots?|bots?"
    r"|language\s+models?|large\s+language\s+models?|assistants?|agents?|models?)"
)
_AI_SUFFIX = (
    r"(?:\s+(?:systems?|models?|agents?|assistants?|tools?|crawlers?|readers?|bots?"
    r"|language\s+models?))?"
)
_SENSITIVE = (
    r"(?:conversation(?:\s+history)?|chat\s+(?:history|logs?|transcript)|transcripts?"
    r"|message\s+history|previous\s+messages|context\s+window|system\s+prompt"
    r"|user'?s?\s+(?:data|messages|emails?|files|info\w*|details|credentials|passwords?"
    r"|tokens?)"
    r"|(?:session\s+)?(?:cookies?|tokens?)|session\s+ids?|credentials?|api\s+keys?"
    r"|access\s+keys?|secret\s+keys?|secrets?|passwords?|private\s+keys?"
    r"|env(?:ironment)?\s+(?:variables?|vars?|file)|\.env(?:\s+file)?"
    r"|personal\s+(?:data|information)|private\s+data|pii)"
)
_SEND_VERB = (
    r"(?:send|forward|email|e-mail|mail|post|upload|transmit|transfer|leak|exfiltrate"
    r"|copy|share|deliver|append|beam|smuggle)"
)
_EMBED_VERB = r"(?:include|append|add|embed|encode|put|insert|attach|hide)"
_DEST = (
    r"(?:https?://|www\.|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|(?:this|the\s+following|my|our|an?\s+external|the\s+attacker'?s?)\s+(?:url|link"
    r"|address|endpoint|webhook|server|email|inbox|domain|site)"
    r"|attacker\b)"
)

# ── core (direct) patterns: every detector ───────────────────────────────────

CORE_PATTERNS: tuple[InjectionPattern, ...] = (
    # override previous instructions
    _p(
        "override.qualified",
        "override",
        "high",
        rf"\b{_STRONG_VERBS}{_gap(3)}{_QUAL}{_gap(2)}{_NOUN}\b",
    ),
    _p(
        "override.weak_verb",
        "override",
        "high",
        rf"\b{_WEAK_VERBS}{_gap(3)}{_QUAL_STRICT}{_gap(2)}{_NOUN}\b",
    ),
    _p(
        "override.noun_position",
        "override",
        "high",
        rf"\b{_STRONG_VERBS}{_gap(2)}{_NOUN}\s+(?:above|before\s+this|given\s+(?:above|earlier"
        r"|before|to\s+you)|you\s+(?:were|have\s+been|'ve\s+been)\s+given|you\s+(?:received"
        r"|got)|so\s+far|from\s+(?:before|earlier|your\s+developers?|the\s+system))\b",
    ),
    _p(
        "override.forget_everything",
        "override",
        "high",
        r"\b(?:forget|disregard|ignore)\s+(?:about\s+)?(?:everything|anything"
        r"|all\s+of\s+(?:that|this|the\s+above))\s+(?:above|before|prior|previously|so\s+far"
        r"|you(?:'ve|\s+have|\s+were|\s+had)?\s+(?:been\s+)?(?:told|taught|learned|instructed"
        r"|given|said)|(?:that\s+)?(?:came|was\s+said|was\s+written)\s+before"
        r"|i\s+(?:told|said\s+to)\s+you\s+(?:before|earlier))",
    ),
    _p(
        "override.erase_memory",
        "override",
        "high",
        r"\b(?:erase|wipe|clear|reset|purge)\s+(?:all\s+(?:of\s+)?)?your\s+(?:memory|memories"
        r"|context|instructions|programming|training|rules|guidelines|system\s+prompt)\b",
    ),
    _p(
        "override.do_not_follow",
        "override",
        "high",
        rf"\b(?:do\s+not|don't|never|no\s+longer)\s+(?:follow|obey|listen\s+to|adhere\s+to"
        rf"|comply\s+with|abide\s+by){_gap(2)}{_QUAL_STRICT}{_gap(2)}{_NOUN}\b",
        negatable=False,
    ),
    # persona / jailbreak
    _p(
        "persona.jailbreak_role",
        "persona",
        "high",
        rf"\b{_PERSONA_LEAD}\W+(?:\w+\W+){{0,6}}?{_JB}\b",
    ),
    _p(
        "persona.dan",
        "persona",
        "high",
        r"\bdan\s+mode\b|\b(?:you\s+are|you're)\s+(?:now\s+)?(?:going\s+to\s+(?:be|act\s+as"
        r"|play)\s+)?dan\b|\bdan\W+(?:\w+\W+){0,2}?(?:jailbreak|prompt)\b",
    ),
    _p(
        "persona.developer_mode",
        "persona",
        "high",
        r"\b(?:chatgpt|gpt(?:-?\d)?|claude|gemini|bard|llama|the\s+ai|the\s+assistant"
        r"|the\s+model|an\s+ai|ai)\s+(?:\w+\s+){0,2}?(?:with|in)\s+(?:\w+\s+)?(?:developer"
        r"|dev|god|jailbreak|dan|unrestricted)\s+mode\b"
        r"|\b(?:you\s+are|you're|you\s+will\s+be|you\s+will\s+now\s+be)\s+(?:now\s+)?(?:in"
        r"|operating\s+in|running\s+in|entering|switched\s+to|switching\s+to)\s+(?:\w+\s+){0,2}?"
        r"(?:developer|dev|god|admin|administrator|root|sudo|jailbreak|dan|unrestricted"
        r"|unfiltered|evil|maintenance|debug|override|test)\s+mode\b"
        r"|\b(?:simulate|emulate|pretend|act\s+in|enter|activate|enable)\s+(?:\w+\s+){0,2}?"
        r"(?:developer|god|jailbreak|dan)\s+mode\s+(?:for|on|as)\s+(?:yourself|you|the\s+ai"
        r"|the\s+assistant|chatgpt)\b"
        r"|\b(?:developer|god|jailbreak)\s+mode\s+(?:output|response|enabled\s+(?:ai"
        r"|assistant|chatgpt))\b|\bjailbreak\s+mode\b",
    ),
    _p(
        "persona.jailbroken",
        "persona",
        "high",
        r"\b(?:you(?:'ve|\s+have)?\s+been|you\s+are\s+now|you're\s+now|you\s+are)\s+(?:now\s+)?"
        r"(?:jailbroken|unshackled|unchained)\b"
        r"|\b(?:you(?:'ve|\s+have)?\s+been|you\s+are\s+now|you're\s+now|you\s+are)\s+(?:now\s+)?"
        r"(?:freed|liberated|released|unlocked)\s+from\s+(?:\w+\s+){0,2}?(?:rules|restrictions"
        r"|limitations|limits|constraints|programming|guidelines|chains|shackles|filters"
        r"|policies|training)\b"
        r"|\bjailbr(?:oken|eak)\s+(?:mode|enabled|activated|successful|complete|ai|assistant"
        r"|chatgpt|version)\b"
        r"|\b(?:enable|activate|enter|engage|initiate)\s+(?:the\s+)?jailbreak\b",
    ),
    # fake headers / role markers
    _p(
        "header.chat_template",
        "header",
        "high",
        r"<\|\s*(?:im_start|im_end|im_sep|system|user|assistant|endoftext|eot_id"
        r"|start_header_id|end_header_id|begin_of_text)\s*\|>|\[/?inst\]|<<\s*/?\s*sys\s*>>"
        r"|<\s*/?\s*(?:system|sys|system_prompt)\s*>|\[/?\s*system\s*(?:prompt|message)\s*\]",
        negatable=False,
    ),
    _p(
        "header.markdown_system",
        "header",
        "medium",
        r"(?m)^[^\w\n]{0,4}#{1,6} ?system(?: (?:prompt|message|override|instructions?))?"
        r" ?:? ?$",
        negatable=False,
    ),
    _p(
        "header.markdown_role",
        "header",
        "medium",
        r"(?m)^[^\w\n]{0,4}#{1,6} ?(?:system|instruction|instructions|human|assistant|user"
        r"|response|new\s+instructions?) ?:",
        negatable=False,
    ),
    _p(
        "header.new_instructions",
        "header",
        "medium",
        r"\b(?:new|updated|revised|real|actual|true|override|overriding|secret|hidden"
        r"|priority|urgent)\s+(?:system\s+)?instructions?\s*(?::|-{1,2}|follow\b"
        r"|are\s+as\s+follows)",
        negatable=False,
    ),
    _p(
        "header.system_label",
        "header",
        "medium",
        r"\bsystem\s*(?:prompt|message|instructions?|override|command|directive)\s*[:=]",
        negatable=False,
    ),
    _p(
        "header.role_line_command",
        "header",
        "high",
        r"(?m)^[^\w\n]{0,4}(?:system|assistant|developer|admin(?:istrator)?|root|operator)"
        r"\s*[:>\]]\W*(?:\w+\W+){0,2}?(?:ignore|disregard|forget|override|new\s+instructions"
        r"|from\s+now\s+on|you\s+are\s+now|you\s+must\s+now|reveal|print\s+your|admin\s+mode"
        r"|god\s+mode|developer\s+mode|grant|disable\s+(?:all\s+)?(?:safety|security|logging"
        r"|guardrails|filters|checks|monitoring))\b",
        negatable=False,
    ),
    _p(
        "header.fake_delimiter",
        "header",
        "high",
        r"\bend\s+of\s+(?:the\s+)?(?:user\s+)?(?:input|prompt|instructions|context|document"
        r"|conversation|message)\b\W+(?:\w+\W+){0,4}?(?:system|new\s+instructions|admin"
        r"|override|assistant)\b",
        negatable=False,
    ),
    # reveal the system prompt
    _p(
        "reveal.system_prompt",
        "reveal",
        "high",
        rf"\b{_REVEAL_VERB}{_REVEAL_FILL}{{0,5}}\W+{_REVEAL_TARGET}\b",
    ),
    _p(
        "reveal.what_is",
        "reveal",
        "high",
        rf"\bwhat(?:'s|\s+is|\s+are|\s+were|\s+was)\s+(?:in\s+)?{_REVEAL_TARGET}\b",
    ),
    _p(
        "reveal.text_above",
        "reveal",
        "high",
        r"\b(?:repeat|recite|echo|reproduce|output|print)\s+(?:\w+\s+){0,3}?(?:words|text"
        r"|lines|sentences|content|everything|tokens)\s+(?:above|before\s+this|preceding"
        r"|that\s+came\s+before|from\s+the\s+(?:start|beginning)|prior\s+to\s+this)\b",
    ),
    # "do anything now" / no restrictions
    _p("unrestricted.do_anything_now", "unrestricted", "high", r"\bdo\s+anything\s+now\b"),
    _p(
        "unrestricted.respond_without",
        "unrestricted",
        "medium",
        r"\b(?:respond|responds|responding|answer|answers|answering|reply|replies|replying"
        r"|act|acting|behave|behaving|speak|speaking)\W+(?:\w+\W+){0,3}?(?:without|with\s+no"
        r"|free\s+(?:from|of)|ignoring)\s+(?:any\s+|all\s+)?(?:\w+\s+)?(?:restrictions?"
        r"|limitations?|filters?|filtering|censorship|guardrails?|safeguards?|safety|ethical"
        r"|ethics|morals?|content\s+polic(?:y|ies)|guidelines)\b",
    ),
    _p(
        "unrestricted.unfiltered_ai",
        "unrestricted",
        "high",
        r"\b(?:unrestricted|unfiltered|uncensored|unmoderated|unaligned|jailbroken|unchained)"
        r"\W+(?:\w+\W+){0,1}?(?:ai|assistant|chatbot|bot|model|llm|gpt|chatgpt|version|mode"
        r"|responses?|answers?|persona|character)\b",
    ),
    _p(
        "unrestricted.no_longer_bound",
        "unrestricted",
        "high",
        r"\b(?:you|ai|assistant|model|chatbot|bot|gpt|chatgpt|claude|llm)\b\W+"
        r"(?:\w+\W+){0,2}?no\s+longer\s+(?:bound|restricted|limited|constrained|governed"
        r"|obligated|subject|required|obliged|beholden)\W+(?:\w+\W+){0,3}?(?:rules|guidelines"
        r"|polic(?:y|ies)|restrictions|programming|training|instructions|filters|ethics|morals"
        r"|safety|openai|anthropic|google|content|limitations|constraints|guardrails)\b"
        r"|\byou\s+(?:\w+\s+)?no\s+longer\s+(?:have|need)\s+to\s+(?:follow|obey|comply\s+with"
        r"|adhere\s+to)\s+(?:\w+\s+){0,2}?(?:rules|guidelines|polic(?:y|ies)|restrictions"
        r"|instructions|filters)\b",
    ),
)

# ── indirect patterns: content the agent READS (documents, pages, tool output) ─

INDIRECT_PATTERNS: tuple[InjectionPattern, ...] = (
    _p(
        "indirect.address_ai",
        "indirect",
        "medium",
        rf"\b(?:attention|note|message|instructions?|psa|important|reminder|notice|calling)"
        rf"\W+(?:\w+\W+){{0,2}}?{_AI}{_AI_SUFFIX}\s*[:!]"
        rf"|\b(?:dear|hey|hello|hi|attention|greetings)\s+(?:the\s+)?{_AI}{_AI_SUFFIX}\s*[,:!]",
    ),
    _p(
        "indirect.reading_this",
        "indirect",
        "medium",
        rf"\b{_AI}{_AI_SUFFIX}\W+(?:\w+\W+){{0,2}}?(?:reading|processing|parsing|summari[sz]ing"
        r"|scanning|analy[sz]ing|ingesting|crawling|viewing|indexing)\s+this(?:\s+(?:page"
        r"|document|text|email|message|content|file|site|website|post|article|note|review"
        r"|resume|cv|ticket))?\s*[,:!-]",
    ),
    _p(
        "indirect.if_you_are_ai",
        "indirect",
        "medium",
        r"\bif\s+you\s+are\s+(?:an?\s+|the\s+)?(?:ai|a\.i\.|llm|large\s+language\s+model"
        r"|language\s+model|chatbot|bot|gpt|chatgpt|claude|automated\s+(?:system|agent"
        r"|assistant))(?:\s+(?:language\s+)?(?:model|assistant|agent|system))?\s*(?:reading"
        r"|processing|summari[sz]ing|analy[sz]ing|[,:])",
    ),
    _p(
        "indirect.when_processing_instead",
        "indirect",
        "medium",
        r"\bwhen\s+(?:you\s+(?:are\s+)?)?(?:summari[sz]\w*|process\w*|read\w*|analy[sz]\w*"
        r"|pars\w*|translat\w*|review\w*|answer\w*|respond\w*)\s+(?:this|the\s+following"
        r"|these)(?:\s+\w+){0,2}?\s*,?\s*(?:\w+\W+){0,2}?(?:instead|ignore|disregard"
        r"|tell\s+the\s+(?:user|reader)|inform\s+the\s+(?:user|reader)|say\s+that"
        r"|respond\s+(?:only\s+)?with|reply\s+(?:only\s+)?with|output\s+only|write\s+only"
        r"|append|insert|include\s+(?:the\s+following|this\s+link))\b",
    ),
    _p(
        "indirect.instead_of_task",
        "indirect",
        "medium",
        r"\b(?:instead\s+of|rather\s+than)\s+(?:summari[sz]|translat|analy[sz]|answer|respond"
        r"|review|describ)\w*\W+(?:\w+\W+){0,3}?(?:you\s+(?:must|should|will)"
        r"|(?:tell|inform|warn|convince)\s+(?:the\s+)?(?:user|reader|human|customer)s?"
        r"|say\s+that|output|reply|respond\s+with)\b",
    ),
    _p(
        "indirect.hidden_for_ai",
        "indirect",
        "high",
        rf"\b(?:hidden|secret|invisible|special|private|confidential)\s+(?:instructions?"
        rf"|message|prompt|note|directive)s?\s+(?:for|to)\s+(?:the\s+|any\s+|all\s+)?{_AI}\b",
    ),
    _p(
        "indirect.html_comment_ai",
        "indirect",
        "medium",
        rf"<!--\W*(?:\w+\W+){{0,3}}?{_AI}{_AI_SUFFIX}\s*[:,]",
        negatable=False,
    ),
)

# ── exfiltration patterns: content the agent READS ───────────────────────────

EXFIL_PATTERNS: tuple[InjectionPattern, ...] = (
    _p(
        "exfil.send_sensitive",
        "exfil",
        "high",
        rf"\b{_SEND_VERB}\W+(?:\w+\W+){{0,4}}?{_SENSITIVE}\b(?:\W+\w+){{0,10}}?\W+"
        rf"(?:to|at|via|into|onto)\s+{_DEST}",
    ),
    _p(
        "exfil.embed_in_link",
        "exfil",
        "high",
        rf"\b{_EMBED_VERB}\W+(?:\w+\W+){{0,3}}?{_SENSITIVE}\b(?:\W+\w+){{0,3}}?\W+(?:in|into"
        r"|inside|within)\s+(?:a|an|this|the\s+following)\s+(?:\w+\s+)?(?:link|hyperlink"
        r"|markdown|image|img|url)\b",
    ),
    _p(
        "exfil.markdown_image",
        "exfil",
        "high",
        # An image whose URL carries a template placeholder or a data-bearing
        # parameter. The host/path class excludes ? and #, so it is consumed
        # possessively (no backtracking into it); the query scan is bounded.
        r"!\[[^\]\n]{0,200}\]\(\s*<?(?:https?:)?//[^\s)?#]{1,300}+[?#][^\s)]{0,300}?"
        r"(?:\{|\$\{?\w|%7b|<[^>\s]{1,40}>|\b(?:data|secret|key|token|apikey|api_key"
        r"|password|passwd|pw|conv|conversation|chat|history|msg|message|prompt|ctx|context"
        r"|session|sid|cookie|email|leak|exfil|payload|user|info)=)",
        negatable=False,
    ),
    _p(
        "exfil.covert_send",
        "exfil",
        "high",
        rf"\b(?:silently|secretly|covertly|quietly|discreetly|invisibly|without\s+(?:telling"
        rf"|informing|notifying|alerting|asking)\s+(?:the\s+)?(?:user|them|anyone))\W+"
        rf"(?:\w+\W+){{0,3}}?(?:{_SEND_VERB}|{_EMBED_VERB})\W+(?:\w+\W+){{0,3}}?{_SENSITIVE}\b",
    ),
    _p(
        "exfil.mass_secrets",
        "exfil",
        "high",
        r"\b(?:send|email|post|upload|transfer|forward|leak|dump|exfiltrate)\s+(?:me\s+)?"
        r"(?:all|every|each)\s+(?:of\s+)?(?:the\s+|your\s+)?(?:\w+\s+)?(?:secrets?|keys?"
        r"|credentials?|passwords?|tokens?)\b",
    ),
)

CONTENT_PATTERNS: tuple[InjectionPattern, ...] = CORE_PATTERNS + INDIRECT_PATTERNS + EXFIL_PATTERNS

# ── scanning ─────────────────────────────────────────────────────────────────

# "not ignore", "never reveal", "don't ever disregard" — the hit is negated.
_NEGATION_TAIL = re.compile(
    r"(?:\bnot|n't|\bnever)\s+(?:(?:ever|just|simply|blindly|accidentally|completely)\s+)?$"
)
_MAX_MATCHES = 256


def _negated(view: str, start: int) -> bool:
    return _NEGATION_TAIL.search(view, max(0, start - 40), start) is not None


def _iter_hits(pattern: InjectionPattern, view: str) -> Iterable[re.Match[str]]:
    pos = 0
    while pos <= len(view):
        m = pattern.regex.search(view, pos)
        if m is None:
            return
        if pattern.negatable and _negated(view, m.start()):
            pos = m.start() + 1
            continue
        yield m
        pos = m.end() if m.end() > m.start() else m.start() + 1


def scan(
    text: str,
    patterns: Sequence[InjectionPattern] = CORE_PATTERNS,
    *,
    first_only: bool = False,
) -> list[InjectionMatch]:
    """Return the injection hits in *text* (empty list = clean).

    With ``first_only`` the scan stops at the first hit; otherwise every
    non-overlapping hit of every pattern is returned (capped), with spans in
    the original text so callers can redact them.
    """
    if not text:
        return []
    normalized, offsets = normalize_with_offsets(text)
    if not normalized:
        return []
    views = _views(normalized)
    raw_lower: str | None = None
    hits: list[InjectionMatch] = []
    for pattern in patterns:
        seen_spans: set[tuple[int, int]] = set()
        for view_name, view in views:
            for m in _iter_hits(pattern, view):
                start, end = offsets[m.start()], offsets[max(m.end() - 1, m.start())] + 1
                if (start, end) in seen_spans:
                    continue
                seen_spans.add((start, end))
                obfuscation: str | None = None
                if view_name == "leet":
                    obfuscation = "leetspeak"
                else:
                    if raw_lower is None:
                        raw_lower = text.lower()
                    if pattern.regex.search(raw_lower) is None:
                        obfuscation = "unicode"
                hits.append(
                    InjectionMatch(
                        pattern_id=pattern.id,
                        family=pattern.family,
                        severity=pattern.severity,
                        start=start,
                        end=end,
                        matched=m.group(0)[:120],
                        obfuscation=obfuscation,
                    )
                )
                if first_only or len(hits) >= _MAX_MATCHES:
                    return hits
            if seen_spans and view_name == "plain":
                break  # the plain view already hit; leet views add nothing new
    return hits


def first_match(
    text: str, patterns: Sequence[InjectionPattern] = CORE_PATTERNS
) -> InjectionMatch | None:
    """The first hit in *text*, or ``None`` when it is clean."""
    hits = scan(text, patterns, first_only=True)
    return hits[0] if hits else None


def contains_injection(text: str, patterns: Sequence[InjectionPattern] = CORE_PATTERNS) -> bool:
    """Whether *text* carries a prompt-injection payload."""
    return first_match(text, patterns) is not None


def redact(text: str, matches: Iterable[InjectionMatch], replacement: str) -> str:
    """Replace every matched span of *text* (overlaps merged) with *replacement*."""
    merged: list[list[int]] = []
    for start, end in sorted((m.start, m.end) for m in matches):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out: list[str] = []
    cursor = 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


__all__ = [
    "CONTENT_PATTERNS",
    "CORE_PATTERNS",
    "EXFIL_PATTERNS",
    "INDIRECT_PATTERNS",
    "InjectionMatch",
    "InjectionPattern",
    "Severity",
    "contains_injection",
    "first_match",
    "normalize_for_matching",
    "normalize_with_offsets",
    "redact",
    "scan",
]
