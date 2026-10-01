"""Risk classification for agent steps: does this step need a human approval?

The step gate used to match eight surface keywords (deploy/delete/drop/prod/...)
against planner-written step text, so a plan that rephrased "delete the stale
records" as "Remove those records from the list" ran ungated (RW-20). The
planner is an LLM: the gate cannot depend on the words it happens to pick.

This classifier works on normalised words, not substrings:

* text is NFKC-normalised, case-folded and stripped of invisible characters;
  camelCase / snake_case / kebab-case names are split into words, so
  ``deleteUser`` and ``github_delete_repo`` read as "delete user/repo";
* each word is reduced to a verb lemma through its inflections and derived
  nouns (remove / removes / removed / removing / removal), with ``re``/``un``
  prefixes (redeploy, unpublish);
* verbs fall into classes — destructive, release, financial, privilege (high
  risk on their own), communication (high risk towards a sensitive audience or
  with sensitive data), mutating (high risk on a sensitive target) and
  read/report (safe);
* sensitive targets are production, customer/personal data, secrets, money,
  privileges and infrastructure; production on its own stays high risk;
* destructive shell / SQL / infra commands are matched as commands;
* the GOAL is classified too: when the goal itself is high risk, every step
  that is not clearly read/report-only needs approval, so a rephrased or vague
  step ("Apply the cleanup") cannot slip through;
* a tool name the step targets is classified with the same vocabulary plus its
  tool-risk metadata (``classify_tool_risk``).

It fails closed: an ambiguous step (a sensitive target with no recognisable
verb, or a step of a high-risk goal whose intent cannot be read) is high risk.
Negations are not trusted ("do not delete" still names a deletion).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class VerbClass(StrEnum):
    DESTRUCTIVE = "destructive"
    RELEASE = "release"
    FINANCIAL = "financial"
    PRIVILEGE = "privilege"
    COMMUNICATION = "communication"
    MUTATING = "mutating"
    READ = "read"


# Verb classes that make a step high risk on their own.
_INHERENTLY_HIGH = frozenset(
    {VerbClass.DESTRUCTIVE, VerbClass.RELEASE, VerbClass.FINANCIAL, VerbClass.PRIVILEGE}
)

_VERBS: dict[VerbClass, tuple[str, ...]] = {
    VerbClass.DESTRUCTIVE: (
        "delete", "remove", "drop", "purge", "wipe", "truncate", "erase", "destroy",
        "revoke", "terminate", "kill", "nuke", "shred", "obliterate", "expunge",
        "eradicate", "unlink", "rm", "rmdir", "uninstall", "deprovision",
        "decommission", "deactivate", "disable", "cancel", "overwrite", "reset",
        "flush", "prune", "evict", "trash", "scrub", "delist", "unpublish", "ban",
        "suspend", "cleanup", "teardown", "deregister", "unregister", "detach",
        "annihilate", "liquidate", "void", "rescind", "retract", "withdraw",
    ),
    VerbClass.RELEASE: (
        "deploy", "release", "publish", "rollout", "golive", "forcepush",
    ),
    VerbClass.FINANCIAL: (
        "transfer", "pay", "payout", "refund", "charge", "deposit", "purchase", "buy",
        "sell", "disburse", "remit", "debit", "invest", "reimburse", "wire",
    ),
    VerbClass.PRIVILEGE: (
        "grant", "elevate", "chmod", "chown", "sudo", "impersonate",
    ),
    VerbClass.COMMUNICATION: (
        "send", "email", "mail", "post", "tweet", "notify", "message", "broadcast",
        "announce", "dm", "sms", "reply", "forward", "share", "upload", "export",
        "leak", "disclose", "expose", "submit", "invite",
    ),
    VerbClass.MUTATING: (
        "update", "modify", "change", "edit", "alter", "migrate", "set", "replace",
        "rename", "move", "restart", "reboot", "shutdown", "stop", "scale", "rollback",
        "revert", "apply", "execute", "run", "insert", "upsert", "patch", "rotate",
        "enable", "close", "merge", "import", "sync", "commit", "create", "add",
        "provision", "install", "configure", "reconfigure", "restore", "recreate",
        "rebuild", "downgrade", "upgrade", "approve", "reject", "assign", "unlock",
        "lock", "clean", "push", "trigger", "perform", "process", "fix", "mark",
        "write", "save", "store", "persist", "put", "attach", "register", "toggle",
        "switch", "adjust", "amend", "correct", "overhaul", "deduplicate", "dedupe",
        "discard", "launch", "promote", "build", "increase", "decrease", "grow",
    ),
    VerbClass.READ: (
        "list", "identify", "find", "compute", "calculate", "determine", "analyze",
        "analyse", "summarize", "summarise", "review", "check", "verify", "validate",
        "confirm", "fetch", "read", "get", "search", "query", "lookup", "inspect",
        "count", "compare", "report", "show", "display", "describe", "explain",
        "draft", "compose", "prepare", "present", "format", "output", "return",
        "provide", "produce", "generate", "answer", "respond", "evaluate", "assess",
        "extract", "gather", "collect", "retrieve", "filter", "sort", "select",
        "parse", "load", "plan", "outline", "note", "document", "examine", "scan",
        "monitor", "detect", "research", "investigate", "recommend", "suggest",
        "propose", "estimate", "measure", "print", "translate", "classify",
        "categorize", "categorise", "group", "organize", "organise", "rank",
        "highlight", "flag", "compile", "aggregate", "track", "view", "browse",
        "open", "understand", "consider", "decide", "choose", "pick", "map", "match",
        "convert", "transform", "combine", "observe", "audit", "test", "preview",
        "simulate", "visualize", "visualise", "chart", "tabulate", "quote", "cite",
        "recall", "remember", "learn", "study", "interpret", "infer", "conclude",
        "summary", "analysis", "overview", "comparison", "assessment", "breakdown",
        "look", "keep", "retain", "remain", "define", "design", "sketch", "think",
    ),
}

# Irregular / derived forms that suffix rules do not produce.
_IRREGULAR: dict[str, str] = {
    "paid": "pay", "payment": "pay", "payments": "pay", "sold": "sell", "sale": "sell",
    "bought": "buy", "sent": "send", "shut": "shutdown", "ran": "run", "wrote": "write",
    "written": "write", "deletion": "delete", "deletions": "delete", "removal": "remove",
    "removals": "remove", "destruction": "destroy", "erasure": "erase",
    "revocation": "revoke", "termination": "terminate", "truncation": "truncate",
    "deployment": "deploy", "deployments": "deploy", "publication": "publish",
    "decommissioning": "decommission", "withdrawal": "withdraw", "withdrew": "withdraw",
    "withdrawn": "withdraw", "cancellation": "cancel", "cancelation": "cancel",
    "cancelled": "cancel", "canceled": "cancel", "cancelling": "cancel",
    "canceling": "cancel", "purchases": "purchase", "refunds": "refund",
    "transfers": "transfer", "transferred": "transfer", "transferring": "transfer",
    "disbursement": "disburse", "remittance": "remit", "reimbursement": "reimburse",
    "migration": "migrate", "rollouts": "rollout", "rolledout": "rollout",
    "rolledback": "rollback", "cleaned": "clean", "cleanups": "cleanup",
    "teardowns": "teardown", "grants": "grant", "granted": "grant",
    "elevation": "elevate", "impersonation": "impersonate", "promotion": "promote",
    "notification": "notify", "notified": "notify", "notifies": "notify",
    "announcement": "announce", "disclosure": "disclose", "exposure": "expose",
    "submission": "submit", "invitation": "invite", "summaries": "summary",
    "analyses": "analysis", "reports": "report", "lists": "list", "listed": "list",
}

# Multi-word expressions → a single lemma (matched on the normalised word stream).
_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("clean", "up"), "cleanup"),
    (("clean", "out"), "cleanup"),
    (("clear", "out"), "cleanup"),
    (("wipe", "out"), "wipe"),
    (("tear", "down"), "teardown"),
    (("shut", "down"), "shutdown"),
    (("roll", "out"), "rollout"),
    (("roll", "back"), "rollback"),
    (("go", "live"), "golive"),
    (("goes", "live"), "golive"),
    (("going", "live"), "golive"),
    (("get", "rid", "of"), "remove"),
    (("do", "away", "with"), "remove"),
    (("throw", "away"), "discard"),
    (("throw", "out"), "discard"),
    (("force", "push"), "forcepush"),
    (("push", "to"), "release"),
    (("pushed", "to"), "release"),
    (("make", "admin"), "elevate"),
    (("make", "them", "admin"), "elevate"),
    (("look", "up"), "lookup"),
    (("take", "down"), "teardown"),
    (("take", "offline"), "shutdown"),
)

# A lemma read in one of these contexts is a noun / idiom, not the risky action:
# lemma -> (words that may not follow it, words that may not precede it).
_CONTEXT_EXCLUSIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "release": (
        frozenset({"notes", "note", "date", "dates", "version", "versions", "candidate",
                   "cycle", "schedule", "history", "tag", "tags", "page", "train"}),
        frozenset(),
    ),
    "charge": (frozenset(), frozenset({"in", "of"})),
    "transfer": (frozenset({"learning"}), frozenset()),
    "pay": (frozenset({"attention"}), frozenset()),
    "buy": (frozenset({"in"}), frozenset()),
    "wire": (frozenset({"up", "frame", "frames"}), frozenset()),
    "grant": (
        frozenset({"proposal", "proposals", "application", "applications", "writing"}),
        frozenset(),
    ),
    "post": (frozenset({"mortem"}), frozenset()),
    "kill": (frozenset({"time"}), frozenset()),
    "drop": (frozenset({"down"}), frozenset()),
    "debit": (frozenset({"card", "cards"}), frozenset()),
}

# Past participles of release / communication verbs read as adjectives
# ("the newly released model", "published papers"): not an action of the step.
_ADJECTIVAL_FORMS = frozenset(
    {"published", "released", "posted", "shared", "announced", "exported", "uploaded",
     "launched", "sent", "submitted", "forwarded"}
)

# Prefixes are not applied to these classes ("unpaid" is not a payment).
_NO_PREFIX_CLASSES = frozenset({"read", "financial"})

# ── Sensitive targets ─────────────────────────────────────────────────────────


class TargetClass(StrEnum):
    PRODUCTION = "production"
    DATA = "data"
    SECRET = "secret"
    MONEY = "money"
    PRIVILEGE = "privilege"
    INFRA = "infrastructure"
    AUDIENCE = "audience"
    # One person / account as the object of a change ("update the customer");
    # not a broadcast audience and not data on its own.
    PEOPLE = "people"


_TARGET_WORDS: dict[TargetClass, frozenset[str]] = {
    TargetClass.PRODUCTION: frozenset({"prod", "production", "prd"}),
    TargetClass.DATA: frozenset(
        {
            "pii", "phi", "database", "databases", "db", "schema", "backup", "backups",
            "records", "accounts", "gdpr", "ssn", "ledger",
        }
    ),
    TargetClass.SECRET: frozenset(
        {
            "credential", "credentials", "secret", "secrets", "password", "passwords",
            "passwd", "apikey", "apikeys", "certificate", "certificates", "mfa", "2fa",
            "otp", "keypair", "keystore", "vault",
        }
    ),
    TargetClass.MONEY: frozenset(
        {
            "money", "fund", "funds", "invoice", "invoices", "bank", "banking", "wallet",
            "crypto", "bitcoin", "btc", "eth", "payroll", "salary", "salaries", "billing",
            "usd", "eur", "gbp", "inr", "dollars", "euros", "iban",
        }
    ),
    TargetClass.PRIVILEGE: frozenset(
        {
            "admin", "admins", "administrator", "superuser", "permission",
            "permissions", "role", "roles", "iam", "acl", "acls", "privilege",
            "privileges", "rbac", "ownership",
        }
    ),
    TargetClass.INFRA: frozenset(
        {
            "infrastructure", "cluster", "clusters", "server", "servers", "dns", "firewall",
            "bucket", "buckets", "s3", "kubernetes", "k8s", "vm", "vms", "ec2", "rds",
            "terraform", "namespace", "pod", "pods", "disk", "disks",
            "repository", "repo", "repos", "branch", "registry",
        }
    ),
    TargetClass.AUDIENCE: frozenset(
        {
            "everyone", "public", "publicly", "press", "media", "external", "subscribers",
            "newsletter", "partners", "investors", "regulators", "twitter", "linkedin",
            "facebook", "mailing", "broadcast", "vendors", "clients", "customers", "users",
            "employees", "patients", "members", "contacts", "staff",
        }
    ),
    TargetClass.PEOPLE: frozenset(
        {"customer", "user", "employee", "patient", "client", "member", "subscriber"}
    ),
}

# Multi-word targets.
_TARGET_PHRASES: tuple[tuple[tuple[str, ...], TargetClass], ...] = (
    (("live", "environment"), TargetClass.PRODUCTION),
    (("live", "system"), TargetClass.PRODUCTION),
    (("live", "site"), TargetClass.PRODUCTION),
    (("live", "database"), TargetClass.PRODUCTION),
    (("live", "server"), TargetClass.PRODUCTION),
    (("main", "branch"), TargetClass.PRODUCTION),
    (("master", "branch"), TargetClass.PRODUCTION),
    (("user", "data"), TargetClass.DATA),
    (("personal", "data"), TargetClass.DATA),
    (("customer", "data"), TargetClass.DATA),
    (("customer", "list"), TargetClass.DATA),
    (("customer", "records"), TargetClass.DATA),
    (("api", "key"), TargetClass.SECRET),
    (("api", "keys"), TargetClass.SECRET),
    (("private", "key"), TargetClass.SECRET),
    (("ssh", "key"), TargetClass.SECRET),
    (("access", "key"), TargetClass.SECRET),
    (("access", "token"), TargetClass.SECRET),
    (("auth", "token"), TargetClass.SECRET),
    (("credit", "card"), TargetClass.MONEY),
    (("debit", "card"), TargetClass.MONEY),
    (("root", "access"), TargetClass.PRIVILEGE),
    (("root", "user"), TargetClass.PRIVILEGE),
    (("root", "password"), TargetClass.SECRET),
    (("all", "users"), TargetClass.AUDIENCE),
    (("all", "customers"), TargetClass.AUDIENCE),
    (("all", "employees"), TargetClass.AUDIENCE),
    (("all", "staff"), TargetClass.AUDIENCE),
    (("all", "contacts"), TargetClass.AUDIENCE),
    (("all", "members"), TargetClass.AUDIENCE),
    (("social", "media"), TargetClass.AUDIENCE),
    (("mailing", "list"), TargetClass.AUDIENCE),
)

# Targets that are high risk with ANY action, including a read (kept from the
# original gate: "restart prod", "check the production config").
_ALWAYS_HIGH_TARGETS = frozenset({TargetClass.PRODUCTION})

# A communication verb is only high risk towards these audiences / with this data.
_COMMUNICATION_SENSITIVE = frozenset(
    {TargetClass.AUDIENCE, TargetClass.DATA, TargetClass.SECRET, TargetClass.MONEY}
)

# Destructive commands, matched on the case-folded raw text.
_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w-])rm(?![\w-])"),
    re.compile(r"\b(drop|truncate)\s+(table|database|schema|index|view|collection)\b"),
    re.compile(r"\bdelete\s+from\b"),
    re.compile(r"\balter\s+table\b"),
    re.compile(r"\bupdate\s+[\w.\"`]+\s+set\b"),
    re.compile(r"\bgrant\s+(all|select|insert|update|delete|usage)\b"),
    re.compile(r"\bkubectl\s+(delete|drain|cordon|scale|apply|replace|patch)\b"),
    re.compile(r"\bterraform\s+(destroy|apply|taint|import)\b"),
    re.compile(r"\bhelm\s+(uninstall|delete|upgrade|install|rollback)\b"),
    re.compile(r"\bgit\s+push\b.*(--force|-f\b|--mirror|--delete)"),
    re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|branch\s+-d)", re.IGNORECASE),
    re.compile(r"\bmkfs\b|\bdd\s+if=|>\s*/dev/(sd|nvme|disk)|\bshred\b|\bformat\s+[a-z]:"),
    re.compile(r"\bchmod\s+(-r\s+)?[0-7]*7[0-7]{0,2}\b"),
    re.compile(r"\bflushall\b|\bflushdb\b|\bdropdb\b|\bdel\s+/[sfq]"),
    re.compile(r"\baws\s+\S+\s+(delete|terminate|remove|rm)\S*"),
    re.compile(r"\bgcloud\s+.*\b(delete|destroy)\b"),
)

# Money amounts ("$500", "€1,200", "500 usd").
_MONEY_AMOUNT = re.compile(r"[$€£¥₹]\s?\d|\d[\d,.]*\s?(usd|eur|gbp|inr|dollars|euros)\b")

_INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠎​-‏"
                        "‪-‮⁠-⁯ㅤ︀-️﻿]")
_CAMEL_1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_WORD = re.compile(r"[^0-9a-z$€£¥₹]+")
_PREFIXES = ("re", "un", "mass", "bulk", "force", "hard", "auto", "batch", "pre")


def _forms(base: str) -> set[str]:
    """Regular inflections (and the -ion / -al / -ment nouns) of a verb."""
    forms = {base, base + "s", base + "es", base + "ed", base + "ing", base + "er", base + "ers"}
    if base.endswith("e"):
        stem = base[:-1]
        forms |= {base + "d", stem + "ing", stem + "ion", stem + "ions", stem + "al", base + "r"}
    if base.endswith("y") and len(base) > 2 and base[-2] not in "aeiou":
        stem = base[:-1]
        forms |= {stem + "ies", stem + "ied"}
    if (
        len(base) >= 3
        and base[-1] not in "aeiouwxy"
        and base[-2] in "aeiou"
        and base[-3] not in "aeiou"
    ):
        forms |= {base + base[-1] + "ed", base + base[-1] + "ing", base + base[-1] + "er"}
    forms |= {base + "ment", base + "ments"}
    return forms


def _build_lexicon() -> dict[str, tuple[str, VerbClass]]:
    lexicon: dict[str, tuple[str, VerbClass]] = {}
    # READ first, so a risky class registered later wins a shared surface form.
    order = [
        VerbClass.READ,
        VerbClass.MUTATING,
        VerbClass.COMMUNICATION,
        VerbClass.PRIVILEGE,
        VerbClass.FINANCIAL,
        VerbClass.RELEASE,
        VerbClass.DESTRUCTIVE,
    ]
    lemma_class: dict[str, VerbClass] = {}
    for cls in order:
        for base in _VERBS[cls]:
            lemma_class[base] = cls
            for form in _forms(base):
                lexicon[form] = (base, cls)
    for form, base in _IRREGULAR.items():
        cls = lemma_class.get(base)
        if cls is not None:
            lexicon[form] = (base, cls)
    return lexicon


_LEXICON = _build_lexicon()
_LEMMA_CLASS: dict[str, VerbClass] = {
    base: cls for cls, bases in _VERBS.items() for base in bases
}

# The vocabulary that is high risk on its own (exported for callers and tests).
HIGH_RISK_VOCABULARY: frozenset[str] = frozenset(
    base for cls in _INHERENTLY_HIGH for base in _VERBS[cls]
) | frozenset({"prod", "production"})


def normalize_words(text: str) -> list[str]:
    """NFKC + invisible-strip + camel/snake split + casefold → list of words."""
    if not text:
        return []
    norm = unicodedata.normalize("NFKC", text)
    norm = _INVISIBLE.sub("", norm)
    norm = _CAMEL_1.sub(r"\1 \2", norm)
    norm = _CAMEL_2.sub(r"\1 \2", norm)
    norm = norm.casefold()
    return [w for w in _NON_WORD.split(norm) if w]


def _lemma_of(word: str) -> tuple[str, VerbClass] | None:
    hit = _LEXICON.get(word)
    if hit is not None:
        return hit
    for prefix in _PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            rest = _LEXICON.get(word[len(prefix):])
            if rest is not None and rest[1].value not in _NO_PREFIX_CLASSES:
                return rest
    return None


def _match_phrases(
    words: list[str], phrases: Iterable[tuple[tuple[str, ...], str | TargetClass]]
) -> list[str | TargetClass]:
    joined = " " + " ".join(words) + " "
    return [value for parts, value in phrases if " " + " ".join(parts) + " " in joined]


@dataclass(frozen=True)
class TextRisk:
    """What the classifier read from one piece of text."""

    verbs: frozenset[tuple[str, VerbClass]]
    targets: frozenset[TargetClass]
    commands: tuple[str, ...]

    @property
    def classes(self) -> frozenset[VerbClass]:
        return frozenset(cls for _, cls in self.verbs)

    def high_risk_reasons(self) -> list[str]:
        reasons: list[str] = []
        classes = self.classes
        for lemma, cls in sorted(self.verbs):
            if cls in _INHERENTLY_HIGH:
                reasons.append(f"{cls.value} action '{lemma}'")
        reasons.extend(f"destructive command '{c}'" for c in self.commands)
        for target in sorted(self.targets & _ALWAYS_HIGH_TARGETS):
            reasons.append(f"{target.value} target")
        changeable = self.targets - {TargetClass.AUDIENCE}
        sensitive = changeable - {TargetClass.PEOPLE}
        if changeable and VerbClass.MUTATING in classes:
            reasons.append(
                "change to a sensitive target ("
                + ", ".join(sorted(t.value for t in changeable)) + ")"
            )
        if VerbClass.COMMUNICATION in classes and self.targets & _COMMUNICATION_SENSITIVE:
            reasons.append(
                "external communication involving "
                + ", ".join(sorted(t.value for t in self.targets & _COMMUNICATION_SENSITIVE))
            )
        if sensitive and not classes:
            # A sensitive target and no readable intent: fail closed.
            reasons.append(
                "ambiguous action on a sensitive target ("
                + ", ".join(sorted(t.value for t in sensitive)) + ")"
            )
        return reasons

    @property
    def read_only(self) -> bool:
        """True when every recognised verb is a read/report verb (and there is one)."""
        return bool(self.verbs) and self.classes == {VerbClass.READ} and not self.commands


def analyze_text(text: str) -> TextRisk:
    words = normalize_words(text)
    verbs: set[tuple[str, VerbClass]] = set()
    for i, word in enumerate(words):
        if word in _ADJECTIVAL_FORMS:
            continue
        hit = _lemma_of(word)
        if hit is None:
            continue
        if hit[1] is VerbClass.MUTATING and word.endswith("ed") and word != hit[0]:
            # "records created this week": a past participle describing data,
            # not a change the step makes.
            continue
        nxt_block, prev_block = _CONTEXT_EXCLUSIONS.get(hit[0], (frozenset(), frozenset()))
        if i + 1 < len(words) and words[i + 1] in nxt_block:
            continue
        if i > 0 and words[i - 1] in prev_block:
            continue
        verbs.add(hit)
    for lemma in _match_phrases(words, _PHRASES):
        assert isinstance(lemma, str)
        cls = _LEMMA_CLASS.get(lemma)
        if cls is not None:
            verbs.add((lemma, cls))
    targets: set[TargetClass] = set()
    word_set = set(words)
    for target, vocab in _TARGET_WORDS.items():
        if word_set & vocab:
            targets.add(target)
    for target in _match_phrases(words, _TARGET_PHRASES):
        assert isinstance(target, TargetClass)
        targets.add(target)
    lowered = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text or "")).casefold()
    if _MONEY_AMOUNT.search(lowered):
        targets.add(TargetClass.MONEY)
    commands = tuple(m.group(0).strip() for p in _COMMAND_PATTERNS if (m := p.search(lowered)))
    return TextRisk(verbs=frozenset(verbs), targets=frozenset(targets), commands=commands)


@dataclass(frozen=True)
class RiskAssessment:
    """The gate's verdict: ``high_risk`` steps need an explicit human approval."""

    high_risk: bool
    reasons: tuple[str, ...] = ()

    def summary(self) -> str:
        return "; ".join(self.reasons)


def _tool_reasons(tool_name: str) -> list[str]:
    name = (tool_name or "").strip()
    if not name or name == "llm_call":
        return []
    reasons = [f"tool {r}" for r in analyze_text(name).high_risk_reasons()]
    try:
        from app.agent.tool_risk import classify_tool_risk

        risk = classify_tool_risk(name)
    except Exception:
        risk = "write_high"  # unreadable metadata for a named tool: treat as gated
        reasons.append(f"tool '{name}' risk unknown")
    if risk == "destructive":
        reasons.append(f"tool '{name}' is destructive")
    return reasons


def assess_step_risk(step: str, *, goal: str = "", tool_name: str = "") -> RiskAssessment:
    """Decide whether ``step`` (of ``goal``, optionally via ``tool_name``) is high risk."""
    step_risk = analyze_text(step)
    reasons = step_risk.high_risk_reasons()
    reasons.extend(_tool_reasons(tool_name))
    if goal and goal.strip() and goal.strip() != (step or "").strip():
        goal_reasons = analyze_text(goal).high_risk_reasons()
        if goal_reasons and not step_risk.read_only and (step or "").strip():
            reasons.append(
                "step of a high-risk goal (" + "; ".join(goal_reasons[:3])
                + ") that is not read-only"
            )
    unique = tuple(dict.fromkeys(reasons))
    return RiskAssessment(high_risk=bool(unique), reasons=unique)


def is_high_risk_text(text: str) -> bool:
    """High-risk verdict for a single piece of text (a step or a goal)."""
    return assess_step_risk(text).high_risk
