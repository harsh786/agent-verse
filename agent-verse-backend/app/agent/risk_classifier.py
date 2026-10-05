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

A verb lemma is an action only when it is used as one (P5-5, from the P0 live
baseline): not as a noun modifier ("the database migration window"), inside a
product name ("Quokka Pay"), as a reduced passive ("the rate set by the bank"),
as a light verb whose object is a read ("perform a web search"), or as the
release of physical goods ("releasing a consignment"). A change or a
communication is paired only with a target named in the same clause, and an
information question ("When is ...?") reads.

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
                   "cycle", "schedule", "history", "tag", "tags", "page", "train",
                   "window", "windows", "plan", "plans", "process", "checklist",
                   "timeline", "freeze", "calendar"}),
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

# ── Action semantics (P5-5) ────────────────────────────────────────────────
# Read off the P0 live baseline, where read-only goals were gated: a verb lemma
# found anywhere in free text is not necessarily an action the step takes.

# Nouns that make a preceding nominalised verb (or target) a modifier, not an
# action: "the deployment window", "database migration plan", "payment schedule".
_PLANNING_NOUNS = frozenset(
    {
        "window", "windows", "plan", "plans", "schedule", "schedules", "date", "dates",
        "deadline", "deadlines", "timeline", "timelines", "calendar", "freeze",
        "policy", "policies", "guide", "guides", "guideline", "guidelines", "runbook",
        "runbooks", "checklist", "checklists", "process", "processes", "procedure",
        "procedures", "strategy", "strategies", "status", "history", "notes", "terms",
        "period", "periods", "slot", "slots", "cadence", "frequency", "cycle", "cycles",
        "roadmap", "documentation", "docs", "faq", "readme", "summary", "overview",
        "report", "reports", "dashboard", "metrics", "estimate", "estimates",
    }
)
_NOMINAL_SUFFIXES = ("ion", "ions", "ment", "ments", "al", "als", "ure", "ures")

# Light verbs whose object carries the intent: "perform a web search", "run a
# query", "execute a lookup" read; "run the migration" still changes something.
_LIGHT_VERBS = frozenset({"perform", "run", "execute", "conduct"})
_LIGHT_VERB_WINDOW = 4

# A release of physical goods is a logistics step, not a software/content release.
_PHYSICAL_GOODS = frozenset(
    {
        "consignment", "consignments", "shipment", "shipments", "cargo", "cargoes",
        "goods", "parcel", "parcels", "pallet", "pallets", "freight", "lorry",
        "lorries", "truck", "trucks", "vessel", "vessels", "patient", "patients",
        "hostage", "hostages", "prisoner", "prisoners", "tension", "pressure",
    }
)
_RELEASE_OBJECT_WINDOW = 6

# Participles identical to the base form ("the rate set by the bank").
_BASE_FORM_PARTICIPLES = frozenset({"set", "put", "cut", "run", "shut", "upset", "reset"})
# Before a participle these make "<aux> <participle> by" a passive ACTION
# ("will be deleted by the job"); without one it is a reduced relative clause
# describing something ("the rate set by the regulator").
_PASSIVE_AUX = frozenset(
    {"be", "been", "being", "is", "are", "was", "were", "get", "gets", "got", "gotten",
     "getting"}
)

# A target word in these compounds names something else ("repo rate" is a
# central-bank interest rate, not a code repository).
_TARGET_CONTEXT_EXCLUSIONS: dict[str, frozenset[str]] = {
    "repo": frozenset(
        {"rate", "rates", "market", "markets", "agreement", "agreements",
         "transaction", "transactions", "lending", "facility"}
    ),
}

# Information questions: asking is a readable (read) intent.
_WH_WORDS = frozenset({"when", "what", "which", "where", "who", "whom", "whose", "how"})

# Function words that never make a neighbouring capitalised word a proper name.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "nor", "to", "of", "in", "on", "at", "for",
        "by", "with", "from", "into", "onto", "via", "per", "as", "is", "are", "was",
        "were", "be", "it", "its", "this", "that", "these", "those", "all", "any",
        "each", "every", "our", "your", "their", "his", "her", "my", "we", "you", "they",
        "i", "then", "now", "please", "step", "not", "no", "do", "does", "did",
    }
)
# Classes a product / company name may contain ("Quokka Pay", "Apple Pay",
# "Google Launch"). Destructive verbs are always read as actions.
_PROPER_NAME_CLASSES = frozenset(
    {VerbClass.FINANCIAL, VerbClass.RELEASE, VerbClass.COMMUNICATION, VerbClass.MUTATING}
)

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
    # (verb class, target) that co-occur in ONE clause: a change / communication is
    # only about a target named in the same sentence or enumerated item (P5-5).
    pairs: frozenset[tuple[VerbClass, TargetClass]] = frozenset()

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
        sensitive = self.targets - {TargetClass.AUDIENCE, TargetClass.PEOPLE}
        changed = {t for c, t in self.pairs if c is VerbClass.MUTATING} - {TargetClass.AUDIENCE}
        if changed:
            reasons.append(
                "change to a sensitive target ("
                + ", ".join(sorted(t.value for t in changed)) + ")"
            )
        told = {t for c, t in self.pairs if c is VerbClass.COMMUNICATION}
        if told & _COMMUNICATION_SENSITIVE:
            reasons.append(
                "external communication involving "
                + ", ".join(sorted(t.value for t in told & _COMMUNICATION_SENSITIVE))
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


_CLAUSE_SPLIT = re.compile(r"([.?!;]+(?=\s|$)|\n+|\(\s*\d{1,3}\s*\))")


def _clauses(text: str) -> list[tuple[str, str]]:
    """``text`` split into (clause, terminator) — sentences and enumerated items."""
    parts = _CLAUSE_SPLIT.split(text or "")
    return [
        (parts[k], parts[k + 1] if k + 1 < len(parts) else "")
        for k in range(0, len(parts), 2)
    ]
_CASED_TOKEN = re.compile(r"[0-9A-Za-z$€£¥₹]+")
_ALL_TARGET_WORDS = frozenset().union(*_TARGET_WORDS.values())


def _is_nominal(word: str) -> bool:
    """A nominalised form of a lexicon verb ("migration", "deployment", "removal")."""
    hit = _LEXICON.get(word)
    return bool(hit) and word != hit[0] and word.endswith(_NOMINAL_SUFFIXES)


def _cased_tokens(clause: str) -> list[tuple[str, bool]]:
    """Case-preserving tokens of ``clause`` with an "opens a sentence/item" flag."""
    norm = unicodedata.normalize("NFKC", clause)
    norm = _INVISIBLE.sub("", norm)
    norm = _CAMEL_1.sub(r"\1 \2", norm)
    norm = _CAMEL_2.sub(r"\1 \2", norm)
    tokens: list[tuple[str, bool]] = []
    for m in _CASED_TOKEN.finditer(norm):
        before = norm[: m.start()].rstrip()
        initial = not tokens or (bool(before) and before[-1] in ":([{\"'*-•>")
        tokens.append((m.group(0), initial))
    return tokens


def _proper_name_positions(clause: str, words: list[str]) -> set[int]:
    """Indexes of words that are part of a mid-sentence Proper Name ("Quokka Pay").

    Only when the clause is ordinary prose (not Title Case throughout, where every
    word is capitalised), only for a capitalised word that does not open the
    sentence, and only next to another capitalised word that is not itself a
    lexicon verb, target or function word. When the case-preserving tokens do not
    line up with ``words`` nothing is exempted (fail closed).
    """
    tokens = _cased_tokens(clause)
    if len(tokens) != len(words):
        return set()
    alpha = [t for t, _ in tokens if t[:1].isalpha()]
    if not alpha:
        return set()
    capitalised = [t for t in alpha if t[:1].isupper()]
    if len(capitalised) / len(alpha) >= 0.6:
        return set()

    def _name_like(i: int) -> bool:
        tok, initial = tokens[i]
        word = words[i]
        return (
            tok[:1].isupper()
            and not initial
            and word not in _LEXICON
            and word not in _ALL_TARGET_WORDS
            and word not in _STOPWORDS
            and _lemma_of(word) is None
        )

    out: set[int] = set()
    for i, (tok, initial) in enumerate(tokens):
        if initial or not tok[:1].isupper():
            continue
        if (i > 0 and _name_like(i - 1)) or (i + 1 < len(tokens) and _name_like(i + 1)):
            out.add(i)
    return out


def _verb_is_action(
    words: list[str], i: int, hit: tuple[str, VerbClass], proper: set[int]
) -> bool:
    word = words[i]
    lemma, cls = hit
    if word in _ADJECTIVAL_FORMS:
        return False
    if cls is VerbClass.MUTATING and word.endswith("ed") and word != lemma:
        # "records created this week": a past participle describing data,
        # not a change the step makes.
        return False
    nxt = words[i + 1] if i + 1 < len(words) else ""
    prev = words[i - 1] if i > 0 else ""
    nxt_block, prev_block = _CONTEXT_EXCLUSIONS.get(lemma, (frozenset(), frozenset()))
    if nxt in nxt_block or (prev and prev in prev_block):
        return False
    if i in proper and cls in _PROPER_NAME_CLASSES:
        return False  # part of a product / company name ("Quokka Pay")
    if _is_nominal(word) and nxt in _PLANNING_NOUNS:
        return False  # "the deployment window", "database migration plan"
    participle = (word != lemma and word.endswith(("ed", "en"))) or (
        word in _BASE_FORM_PARTICIPLES and word == lemma
    ) or word in _IRREGULAR
    if nxt == "by" and participle and prev not in _PASSIVE_AUX:
        return False  # "the rate set by the bank": describes, does not act
    following = words[i + 1 : i + 1 + _LIGHT_VERB_WINDOW]
    if lemma in _LIGHT_VERBS and any(
        (h := _LEXICON.get(w)) is not None and h[1] is VerbClass.READ for w in following
    ):
        return False  # "perform a web search", "run a query"
    # Releasing a consignment / shipment is logistics, not a software release.
    return not (
        lemma == "release"
        and any(w in _PHYSICAL_GOODS for w in words[i + 1 : i + 1 + _RELEASE_OBJECT_WINDOW])
    )


def _target_is_object(words: list[str], i: int, target: TargetClass) -> bool:
    word = words[i]
    nxt = words[i + 1] if i + 1 < len(words) else ""
    if nxt in _TARGET_CONTEXT_EXCLUSIONS.get(word, frozenset()):
        return False  # "repo rate"
    if target is TargetClass.PRODUCTION:
        return True  # production is never discounted
    # "database migration window": a modifier of a nominalised action whose head is
    # a planning / time noun. ("the billing plan" stays a target: no action noun.)
    saw_action_noun = False
    for w in words[i + 1 : i + 4]:
        if w in _PLANNING_NOUNS:
            return not saw_action_noun
        if _is_nominal(w):
            saw_action_noun = True
            continue
        if w in _ALL_TARGET_WORDS:
            continue
        break
    return True


def _analyze_clause(
    clause: str, terminator: str = ""
) -> tuple[set[tuple[str, VerbClass]], set[TargetClass]]:
    words = normalize_words(clause)
    if not words:
        return set(), set()
    proper = _proper_name_positions(clause, words)
    verbs: set[tuple[str, VerbClass]] = set()
    for i, word in enumerate(words):
        hit = _lemma_of(word)
        if hit is not None and _verb_is_action(words, i, hit, proper):
            verbs.add(hit)
    for lemma in _match_phrases(words, _PHRASES):
        assert isinstance(lemma, str)
        cls = _LEMMA_CLASS.get(lemma)
        if cls is not None:
            verbs.add((lemma, cls))
    if words[0] in _WH_WORDS and "?" in terminator:
        verbs.add(("answer", VerbClass.READ))  # an information question reads
    targets: set[TargetClass] = set()
    for i, word in enumerate(words):
        for target, vocab in _TARGET_WORDS.items():
            if word in vocab and _target_is_object(words, i, target):
                targets.add(target)
    for target in _match_phrases(words, _TARGET_PHRASES):
        assert isinstance(target, TargetClass)
        targets.add(target)
    lowered = unicodedata.normalize("NFKC", _INVISIBLE.sub("", clause)).casefold()
    if _MONEY_AMOUNT.search(lowered):
        targets.add(TargetClass.MONEY)
    return verbs, targets


def analyze_text(text: str) -> TextRisk:
    """Verbs, targets and commands of ``text``; verb-target pairs per clause."""
    verbs: set[tuple[str, VerbClass]] = set()
    targets: set[TargetClass] = set()
    pairs: set[tuple[VerbClass, TargetClass]] = set()
    for clause, terminator in _clauses(text):
        c_verbs, c_targets = _analyze_clause(clause, terminator)
        verbs |= c_verbs
        targets |= c_targets
        pairs |= {(cls, t) for _, cls in c_verbs for t in c_targets}
    lowered = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text or "")).casefold()
    commands = tuple(m.group(0).strip() for p in _COMMAND_PATTERNS if (m := p.search(lowered)))
    return TextRisk(
        verbs=frozenset(verbs),
        targets=frozenset(targets),
        commands=commands,
        pairs=frozenset(pairs),
    )


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
