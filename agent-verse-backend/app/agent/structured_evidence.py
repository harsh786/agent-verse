"""Structured tool results as grounding evidence (GRD-1).

A write tool answers with a structure, not prose: ``{'acknowledged': True,
'deleted_count': 1}``, ``{"status_code": 204}``, ``{'inserted_id': '66f1…'}``,
``{"rowcount": 3}``. The final-answer grounding gates compared the agent's
sentence ("The order was deleted.") with that structure by word overlap —
"deleted" is not a word of "deleted_count", and a ``None`` / ``False`` value
read as a negation — so a true answer was judged unsupported or contradicted
and a high-risk goal replanned (asking a human again) until it failed.

This module normalises such results into checkable facts:

- per-operation counts (``deleted_count=1`` → "1 deleted"; deleted / inserted /
  updated, plus generic affected-row counts),
- success / failure signals (``ok`` / ``success`` / ``acknowledged`` flags, an
  HTTP status code, a non-empty ``error``, a tool call that failed),
- ids (``inserted_id``).

:func:`judge_claim` decides a claim about an operation or an outcome against
those facts: supported → ``ENTAILS``; refuted (a count the result does not
have, success over an error, deletion when nothing was deleted, an id that
appears nowhere in the evidence) → ``CONTRADICTS``; anything it cannot decide
is left to the general heuristic. :class:`StructuredFactNLI` plugs that into
:func:`app.intelligence.grounding_verification.verify_grounding`.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.intelligence.nli_checker import NLIChecker, NLIResult, NLIVerdict

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

# ── keys ────────────────────────────────────────────────────────────────────

_OP_KEYS: dict[str, frozenset[str]] = {
    "deleted": frozenset(
        {
            "deleted", "deletedcount", "deletes", "removed", "removedcount",
            "numdeleted", "ndeleted", "deletedrows", "rowsdeleted", "deletedrecords",
            "deleteddocuments",
        }
    ),
    "inserted": frozenset(
        {
            "inserted", "insertedcount", "created", "createdcount", "numinserted",
            "ninserted", "rowsinserted", "upserted", "upsertedcount",
        }
    ),
    "updated": frozenset(
        {
            "modified", "modifiedcount", "nmodified", "updated", "updatedcount",
            "numupdated", "rowsupdated", "upsertedcount",
        }
    ),
}
# Rows touched by a statement whose operation the result does not name.
_AFFECTED_KEYS = frozenset(
    {"rowcount", "rowsaffected", "affectedrows", "numaffected", "changes", "affected"}
)
_ID_KEYS = frozenset(
    {"insertedid", "insertedids", "upsertedid", "createdid", "newid"}
)
_SUCCESS_FLAG_KEYS = frozenset({"success", "succeeded", "ok", "acknowledged"})
_STATUS_KEYS = frozenset({"status", "statuscode", "httpstatus", "httpstatuscode"})
_ERROR_KEYS = frozenset(
    {"error", "errors", "errormessage", "err", "exception", "failure", "writeerrors"}
)
_SUCCESS_STATUS_WORDS = frozenset(
    {"ok", "success", "succeeded", "successful", "completed", "complete", "done"}
)
_FAILURE_STATUS_WORDS = frozenset({"error", "failed", "failure", "errored", "fail"})

_HTTP_STATUS_TEXT = re.compile(r"\bHTTP(?:/[\d.]+)?\s+([1-5]\d\d)\b")
# key: value in JSON, a Python repr or "key=value" text — used when the result
# does not parse (a tool record keeps only the first few hundred characters).
_KV_TEXT = re.compile(
    r"""["']?([A-Za-z_][\w]*)["']?\s*[:=]\s*"""
    r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?\b|true\b|false\b|True\b"""
    r"""|False\b|None\b|null\b|\[\s*\]|\{\s*\}|\[|\{|ObjectId\(\s*['"][^'"]*['"]\s*\))"""
)
_MAX_PARSE_CHARS = 200_000
_MAX_NODES = 5_000
_MAX_DEPTH = 12
# A bare "created" / "updated" key also names a timestamp; no write touches a
# billion rows, so such a value is not a count.
_MAX_COUNT = 1_000_000_000


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


@dataclass
class StructuredFacts:
    """Checkable facts read from structured tool results."""

    counts: dict[str, list[int]] = field(default_factory=dict)  # op → counts seen
    successes: int = 0
    failures: list[str] = field(default_factory=list)
    status_codes: list[int] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.counts or self.successes or self.failures or self.ids)

    def add_count(self, op: str, value: int) -> None:
        self.counts.setdefault(op, []).append(value)

    def merge(self, other: StructuredFacts) -> None:
        for op, values in other.counts.items():
            self.counts.setdefault(op, []).extend(values)
        self.successes += other.successes
        self.failures.extend(other.failures)
        self.status_codes.extend(other.status_codes)
        self.ids.extend(i for i in other.ids if i not in self.ids)

    def positive(self, op: str) -> bool:
        """At least one result shows ``op`` (or a generic row change) happened."""
        return any(c > 0 for c in self._op_counts(op))

    def _op_counts(self, op: str) -> list[int]:
        return [*self.counts.get(op, []), *self.counts.get("affected", [])]

    def has_count_for(self, op: str) -> bool:
        return bool(self._op_counts(op))

    def count_matches(self, op: str, value: int) -> bool:
        return value in self._op_counts(op)

    def render(self) -> str:
        """The facts as plain sentences (an evidence chunk for the gates)."""
        if self.empty:
            return ""
        parts: list[str] = []
        for op in ("deleted", "inserted", "updated", "affected"):
            for value in dict.fromkeys(self.counts.get(op, [])):
                noun = "record" if value == 1 else "records"
                parts.append(f"{value} {op} ({value} {noun} {op})")
        if self.successes:
            parts.append("the operation succeeded")
        for code in dict.fromkeys(self.status_codes):
            parts.append(f"status {code} {'succeeded' if code < 400 else 'failed'}")
        for ident in self.ids[:5]:
            parts.append(f"id {ident}")
        for err in self.failures[:3]:
            parts.append(f"failed: {err[:120]}")
        return "Structured tool result facts: " + "; ".join(parts) + "."


# ── extraction ──────────────────────────────────────────────────────────────


def _parse(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[" or len(stripped) > _MAX_PARSE_CHARS:
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    try:
        return ast.literal_eval(stripped)
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        return None


def _as_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < _MAX_COUNT else None
    if isinstance(value, float) and value.is_integer() and 0 <= value < _MAX_COUNT:
        return int(value)
    return None


def _non_empty_error(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("", "none", "null", "false")
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _visit(key: str, value: Any, facts: StructuredFacts, *, top_level: bool) -> None:
    nk = _norm_key(key)
    count = _as_count(value)
    for op, keys in _OP_KEYS.items():
        if nk in keys and count is not None:
            facts.add_count(op, count)
    if nk in _AFFECTED_KEYS and count is not None:
        facts.add_count("affected", count)
    if nk in _ID_KEYS:
        ids = value if isinstance(value, (list, tuple)) else [value]
        found = [str(i) for i in ids if i not in (None, "")]
        if found:
            facts.ids.extend(i for i in found if i not in facts.ids)
            if nk != "upsertedid" and not facts.counts.get("inserted"):
                facts.add_count("inserted", len(found))
    if nk in _SUCCESS_FLAG_KEYS:
        if value is True or (count is not None and count > 0):
            facts.successes += 1
        elif (value is False or count == 0) and nk != "acknowledged":
            # An unacknowledged write is unknown, not failed.
            facts.failures.append(f"{key} is {value}")
    if nk in _STATUS_KEYS:
        if count is not None and 100 <= count <= 599:
            facts.status_codes.append(count)
            if count < 400:
                facts.successes += 1
            else:
                facts.failures.append(f"status {count}")
        elif isinstance(value, str) and top_level:
            # Only a result's own status: a record's "status": "completed" (an
            # order's state in a find result) says nothing about this call.
            word = value.strip().lower()
            if word in _SUCCESS_STATUS_WORDS:
                facts.successes += 1
            elif word in _FAILURE_STATUS_WORDS:
                facts.failures.append(f"status {word}")
    if nk in _ERROR_KEYS and _non_empty_error(value):
        facts.failures.append(str(value)[:200])


def _walk(
    node: Any, facts: StructuredFacts, depth: int = 0, budget: list[int] | None = None
) -> None:
    budget = budget if budget is not None else [_MAX_NODES]
    if depth > _MAX_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(node, dict):
        for key, value in node.items():
            _visit(str(key), value, facts, top_level=depth == 0)
            _walk(value, facts, depth + 1, budget)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, facts, depth + 1, budget)
    elif isinstance(node, str):
        # MCP wraps results as text content: {"content": [{"text": "{...}"}]}.
        # The embedded text is the result itself: its keys are top level again
        # (the node budget still bounds the walk).
        inner = _parse(node)
        if inner is not None:
            _walk(inner, facts, 0, budget)


def _scan_text(text: str, facts: StructuredFacts) -> None:
    """Key/value scan for results that do not parse (e.g. truncated reprs)."""
    for m in _KV_TEXT.finditer(text[:_MAX_PARSE_CHARS]):
        key, raw = m.group(1), m.group(2)
        value: Any
        low = raw.lower()
        if raw[0] in "\"'":
            value = raw[1:-1]
        elif low == "true":
            value = True
        elif low == "false":
            value = False
        elif low in ("none", "null") or raw.replace(" ", "") in ("[]", "{}"):
            value = None
        elif raw in ("[", "{"):
            value = [raw]  # a non-empty container we cannot see into
        elif raw.startswith("ObjectId"):
            value = raw.split("(", 1)[1].strip(" )'\"")
        else:
            value = float(raw) if "." in raw else int(raw)
        _visit(key, value, facts, top_level=False)


def extract_structured_facts(output: Any) -> StructuredFacts:
    """Normalise one tool result (object or its text) into :class:`StructuredFacts`."""
    facts = StructuredFacts()
    if output is None or output == "":
        return facts
    if isinstance(output, (dict, list, tuple)):
        _walk(output, facts)
        return facts
    text = str(output)
    parsed = _parse(text)
    if parsed is not None:
        _walk(parsed, facts)
    elif text.lstrip()[:1] in ("{", "["):
        _scan_text(text, facts)
    for m in _HTTP_STATUS_TEXT.finditer(text[:_MAX_PARSE_CHARS]):
        code = int(m.group(1))
        facts.status_codes.append(code)
        if code < 400:
            facts.successes += 1
        else:
            facts.failures.append(f"status {code}")
    return facts


def facts_from_tool_calls(tool_calls: Iterable[Any]) -> StructuredFacts:
    """Facts of every recorded tool call: its output plus its own success / error."""
    facts = StructuredFacts()
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        facts.merge(extract_structured_facts(tc.get("output")))
        error = tc.get("error")
        if tc.get("success") is False or _non_empty_error(error):
            facts.failures.append(str(error or f"{tc.get('tool_name') or 'tool'} failed")[:200])
    return facts


# ── judging a claim ─────────────────────────────────────────────────────────

_OP_VERBS: dict[str, frozenset[str]] = {
    "deleted": frozenset(
        {"deleted", "removed", "purged", "erased", "delete", "remove", "purge", "erase"}
    ),
    "inserted": frozenset({"inserted", "created", "insert", "create"}),
    "updated": frozenset({"updated", "modified", "update", "modify"}),
}
_OP_STEMS: dict[str, tuple[str, ...]] = {
    "deleted": ("delet", "remov", "purg", "eras"),
    "inserted": ("insert", "creat", "add"),
    "updated": ("updat", "modif", "chang", "set"),
}
_SUCCESS_WORDS = frozenset(
    {"succeeded", "successful", "successfully", "success", "completed", "acknowledged"}
)
_FAILURE_WORDS = frozenset({"failed", "failure", "error", "errored", "unsuccessful", "unable"})
_ZERO_WORDS = frozenset({"nothing", "zero"})
# Words that name the kind of thing touched, not a fact about it.
_GENERIC_WORDS = frozenset(
    {
        "record", "row", "document", "doc", "item", "entry", "entries", "operation",
        "request", "call", "tool", "result", "total", "all", "exactly", "only",
        "also", "then", "now", "id", "ids", "via", "mongodb", "database", "db",
        "query", "statement", "command", "write", "http", "api", "response", "status",
        "user", "requested",
    }
)
_WORD = re.compile(r"[a-z0-9]+")
_BLOB = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")
_ID_LIKE = re.compile(r"(?<![\w-])(?=[\w-]*\d)(?=[\w-]*[a-zA-Z])[\w-]{6,}(?![\w-])")
_ALL_VERBS = "|".join(sorted({v for vs in _OP_VERBS.values() for v in vs}, key=len, reverse=True))
_AUX = r"(?:was|were|is|are|has been|have been|had been|got|been)"
_COUNT_AFTER_VERB = re.compile(
    rf"\b(?:{_ALL_VERBS})\s+(?:a total of\s+|exactly\s+|only\s+|all\s+)?(\d[\d,]*)\b(?![-\w])",
    re.I,
)
_COUNT_BEFORE_VERB = re.compile(
    rf"(?<![\w#/:-])(\d[\d,]*)\s+(?!{_AUX}\b)[a-z][\w-]*\s+(?:[a-z][\w-]*\s+)?"
    rf"(?:{_AUX}\s+)?(?:successfully\s+)?(?:{_ALL_VERBS})\b",
    re.I,
)
_COUNT_AT_START = re.compile(rf"^\W*(\d[\d,]*)\s+(?:{_AUX}\s+)?(?:{_ALL_VERBS})\b", re.I)
_SUPPORT_COVERAGE = 0.75


def _strip_blobs(text: str) -> str:
    for _ in range(6):
        stripped = _BLOB.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    return text


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _claimed_counts(text: str) -> list[int]:
    found: list[int] = []
    for pattern in (_COUNT_AFTER_VERB, _COUNT_BEFORE_VERB, _COUNT_AT_START):
        for m in pattern.finditer(text):
            found.append(int(m.group(1).replace(",", "")))
    return list(dict.fromkeys(found))


def _subject_covered(words: list[str], ops: set[str], evidence_lower: str) -> bool:
    """The claim's remaining content words (its subject) appear in the evidence."""
    skip = {v for op in ops for v in _OP_VERBS[op]} | _SUCCESS_WORDS | _FAILURE_WORDS
    content = [
        w
        for w in words
        if len(w) >= 2
        and not w.isdigit()
        and w not in skip
        and w not in _GENERIC_WORDS
        and _stem(w) not in _GENERIC_WORDS
        and NLIChecker._content_tokens(w)  # not a stopword / negation
    ]
    if not content:
        return True
    evidence_words = {_stem(w) for w in _WORD.findall(evidence_lower)}
    hits = sum(1 for w in content if _stem(w) in evidence_words)
    return hits / len(content) >= _SUPPORT_COVERAGE


def judge_claim(claim: str, facts: StructuredFacts, evidence_text: str) -> NLIVerdict | None:
    """Decide a claim about an operation / outcome against structured facts.

    Returns ``None`` when the claim says nothing the facts can decide (no
    operation verb and no success / failure wording, or no facts at all).
    """
    if facts.empty:
        return None
    text = _strip_blobs(claim)
    words = _WORD.findall(text.lower().replace("'", "").replace("\u2019", ""))
    word_set = set(words)
    ops = {op for op, verbs in _OP_VERBS.items() if verbs & word_set}
    says_success = bool(_SUCCESS_WORDS & word_set)
    says_failure = bool(_FAILURE_WORDS & word_set) or "could not" in text.lower()
    if not ops and not says_success and not says_failure:
        return None

    evidence_lower = evidence_text.lower()
    counts = _claimed_counts(text)
    negated = (
        says_failure
        or bool(_ZERO_WORDS & word_set)
        or NLIChecker._negations(text) % 2 == 1
        or (bool(counts) and all(c == 0 for c in counts))
    )
    any_positive = any(facts.positive(op) for op in ("deleted", "inserted", "updated"))
    succeeded = facts.successes > 0 or any_positive
    failed_only = bool(facts.failures) and not succeeded

    # An id the claim names must appear somewhere in the evidence.
    for ident in _ID_LIKE.findall(text):
        if ident.lower() not in evidence_lower:
            return "CONTRADICTS"

    supported: bool | None = None
    if ops:
        for op in ops:
            if (
                counts
                and facts.has_count_for(op)
                and not any(facts.count_matches(op, c) for c in counts)
            ):
                return "CONTRADICTS"  # "5 deleted" when the result says 1
            happened = facts.positive(op)
            if negated:
                if happened:
                    return "CONTRADICTS"  # "not deleted" when it was
                if facts.has_count_for(op) or facts.failures:
                    supported = True
                continue
            if facts.has_count_for(op):
                if not happened:
                    return "CONTRADICTS"  # "deleted" when nothing was
                supported = True
            elif failed_only:
                return "CONTRADICTS"  # "deleted" when the call failed
            elif facts.successes and any(s in evidence_lower for s in _OP_STEMS[op]):
                # A bare success (HTTP 204) for an operation the goal / tool names.
                supported = True
    elif negated:
        if succeeded and not facts.failures:
            return "CONTRADICTS"  # "failed" when it succeeded
        supported = bool(facts.failures) or None
    else:
        if failed_only:
            return "CONTRADICTS"  # "succeeded" when it failed
        supported = succeeded or None

    if supported and _subject_covered(words, ops, evidence_lower):
        return "ENTAILS"
    return None


class StructuredFactNLI(NLIChecker):
    """NLI checker that decides operation / outcome claims from structured facts.

    Claims the facts cannot decide go to the inherited checker unchanged.
    ``evidence_text`` is the full evidence (the claim checker only sees a
    truncated prefix of it), used for ids and the claim's subject.
    """

    def __init__(self, facts: StructuredFacts, evidence_text: str) -> None:
        super().__init__()
        self._facts = facts
        self._evidence_text = evidence_text

    def _decide(self, claim: str, evidence: str) -> NLIResult | None:
        verdict = judge_claim(claim, self._facts, self._evidence_text)
        if verdict is None:
            return None
        return NLIResult(
            verdict=verdict,
            confidence=self._confidence.get(verdict, 0.5),
            claim=claim,
            evidence=evidence,
        )

    async def check_consistency(
        self,
        claim: str,
        evidence: str,
        provider: LLMProvider | None = None,
    ) -> NLIResult:
        decided = self._decide(claim, evidence)
        if decided is not None:
            return decided
        return await super().check_consistency(claim, evidence, provider)

    def check_consistency_sync(self, claim: str, evidence: str) -> NLIResult:
        decided = self._decide(claim, evidence)
        if decided is not None:
            return decided
        return super().check_consistency_sync(claim, evidence)
