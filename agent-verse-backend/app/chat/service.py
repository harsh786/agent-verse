"""ChatService — session CRUD, message dispatch, and streaming.

This is the core orchestrator that:
1. Creates / retrieves sessions
2. Saves messages to DB
3. Classifies intent and routes to QA stream or goal execution
4. Tracks per-message token usage
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib as _hashlib
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.chat.clarify_store import ClarifyRoundStore, InMemoryClarifyRoundStore
from app.chat.context import ConversationContext
from app.chat.intent import Intent, IntentRouter
from app.chat.ownership import SYSTEM_SCOPE, ChatFolderNotFoundError, ChatScope
from app.observability.logging import get_logger

_logger = get_logger(__name__)

# Explicit "remember this" directives → the salient fact to persist.
_MEMORY_DIRECTIVE = re.compile(
    r"^\s*(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)\s+(?:that\s+)?(.+)",
    re.IGNORECASE,
)


def _extract_memory_directive(message: str) -> str | None:
    """Return the fact a user asked to be remembered, or None."""
    m = _MEMORY_DIRECTIVE.match(message or "")
    if not m:
        return None
    fact = m.group(1).strip().rstrip(".").strip()
    return fact or None


def _humanize_value(value: Any) -> str | None:
    """Turn a structured goal result into readable prose, never raw JSON.

    Returns None when there is no sensible human string to extract (so the caller
    falls back to a clean completion line rather than dumping a JSON blob).
    """
    import json as _json

    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "["):  # looks like JSON — parse and humanize
            with contextlib.suppress(Exception):
                return _humanize_value(_json.loads(s))
        return s or None
    if isinstance(value, dict):
        # A success/reason verifier shape → a friendly one-liner.
        if "success" in value and isinstance(value.get("reason"), str):
            ok = bool(value.get("success"))
            return f"{'✅' if ok else '⚠️'} {value['reason'].strip()}"
        # Common prose-bearing keys.
        for k in ("answer", "summary", "message", "text", "output", "content", "reason"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # A plan/steps list → a readable checklist.
        steps = value.get("steps")
        if isinstance(steps, list) and steps:
            lines = [str(s).strip() for s in steps if str(s).strip()]
            if lines:
                return "Here's the plan:\n" + "\n".join(f"- {ln}" for ln in lines)
        return None  # unknown shape — do NOT surface raw JSON
    if isinstance(value, list):
        parts = [p for p in (_humanize_value(x) for x in value) if p]
        return "\n".join(parts) if parts else None
    return str(value)


_THINK_TAG = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)
_THINK_PROSE = re.compile(
    r"^\s*(?:thinking\s+process|reasoning|let me think|chain[- ]of[- ]thought)\s*:.*?"
    r"(?:\n\s*\n|\bfinal answer\s*:|\banswer\s*:)",
    re.IGNORECASE | re.DOTALL,
)


_REASON_START = re.compile(
    r"^\s*(<think>|thinking\s+process\s*:|reasoning\s*:|let me think|chain[- ]of[- ]thought\s*:)",
    re.IGNORECASE,
)
_ANSWER_DELIM = re.compile(r"\n\s*\n|final answer\s*:|(?:^|\n)\s*answer\s*:", re.IGNORECASE)


async def _count_chars(source: Any, counter: list[int]) -> Any:
    """Pass *source* through, adding each chunk's length to ``counter[0]``."""
    async for chunk in source:
        counter[0] += len(chunk)
        yield chunk


async def _split_reasoning_stream(source: Any) -> Any:
    """Split a model stream into ('reasoning', chunk) and ('answer', chunk) parts.

    Detects a leading ``<think>…</think>`` block or a "Thinking Process:"/"Reasoning:"
    prose preamble and routes it to the reasoning channel; everything after (the real
    answer) goes to the answer channel. Buffers only the small head needed to decide,
    then streams the rest — so the clean answer still streams live.
    """
    buffer = ""
    mode = "detect"  # detect | think_tag | think_prose | answer
    decided = False
    async for chunk in source:
        buffer += chunk
        while buffer:
            if mode == "detect":
                if not decided and len(buffer.strip()) < 20 and "\n" not in buffer:
                    break  # need more to decide
                m = _REASON_START.match(buffer)
                if m:
                    tok = m.group(1).lower()
                    if tok == "<think>":
                        buffer = buffer[m.end():]
                        mode = "think_tag"
                    else:
                        buffer = buffer[m.end():]
                        mode = "think_prose"
                else:
                    mode = "answer"
                decided = True
                continue
            if mode == "think_tag":
                end = buffer.lower().find("</think>")
                if end == -1:
                    emit, buffer = (buffer[:-8], buffer[-8:]) if len(buffer) > 8 else ("", buffer)
                    if emit:
                        yield ("reasoning", emit)
                    break
                if buffer[:end]:
                    yield ("reasoning", buffer[:end])
                buffer = buffer[end + len("</think>"):]
                mode = "answer"
                continue
            if mode == "think_prose":
                m = _ANSWER_DELIM.search(buffer)
                if not m:
                    keep = 16
                    if len(buffer) > keep:
                        emit, buffer = buffer[:-keep], buffer[-keep:]
                    else:
                        emit = ""
                    if emit:
                        yield ("reasoning", emit)
                    break
                if buffer[: m.start()]:
                    yield ("reasoning", buffer[: m.start()])
                buffer = buffer[m.end():]
                mode = "answer"
                continue
            # mode == "answer"
            yield ("answer", buffer)
            buffer = ""
            break
    if buffer:
        yield (("reasoning" if mode in ("think_tag", "think_prose") else "answer"), buffer)


_LLM_STALL_TIMEOUT_ENV = "AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS"
_DEFAULT_LLM_STALL_TIMEOUT = 60.0


def _llm_stall_timeout_seconds() -> float:
    """Max seconds to wait for the next chunk/response from the LLM provider.

    Reuses the same env var as the agent loop's circuit breaker
    (``app.providers.circuit_breaker``) so operators tune one knob for both.
    """
    import os as _os

    try:
        return float(_os.getenv(_LLM_STALL_TIMEOUT_ENV, str(_DEFAULT_LLM_STALL_TIMEOUT)))
    except ValueError:
        return _DEFAULT_LLM_STALL_TIMEOUT


async def _iter_with_stall_timeout(source: Any, timeout: float) -> AsyncIterator[Any]:
    """Wrap an async iterator so a hung upstream can't keep a chat SSE connection
    open forever. Raises ``TimeoutError`` if no new item arrives within
    *timeout* seconds of the previous one (a per-chunk stall timeout, not an
    overall deadline — a long-but-actively-streaming answer is fine)."""
    it = source.__aiter__()
    while True:
        try:
            # PEP 479: StopAsyncIteration must be caught here, not let escape
            # this generator frame, or it will surface as a RuntimeError.
            item = await asyncio.wait_for(it.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        yield item


def _strip_reasoning(text: str) -> str:
    """Remove a reasoning-model's chain-of-thought so only the answer shows.

    Strips ``<think>…</think>`` blocks and a leading "Thinking Process:"/"Reasoning:"
    preamble up to the first blank line or an explicit "Answer:" marker. Best-effort
    and safe: if nothing matches, the text is returned trimmed and unchanged.
    """
    if not text:
        return ""
    out = _THINK_TAG.sub("", text)
    out = _THINK_OPEN.sub("", out)  # unclosed <think> (truncated stream)
    stripped = _THINK_PROSE.sub("", out)
    # Only accept the prose-strip if it left a real answer behind.
    out = stripped if stripped.strip() else out
    out = re.sub(r"^\s*(?:final answer|answer)\s*:\s*", "", out.strip(), flags=re.IGNORECASE)
    return out.strip()


def humanize_goal_result(goal_text: str, event: dict[str, Any]) -> str:
    """Produce a human-readable assistant message for a completed goal.

    Prefers prose fields, humanizes a structured ``result`` (so the user never sees
    a raw ``{"success": true, ...}`` blob), and falls back to a clean completion line.
    """
    for k in ("answer", "cited_answer", "summary", "message"):
        v = event.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    humanized = _humanize_value(event.get("result"))
    if humanized:
        return humanized
    return f"✅ Completed: {goal_text}"


def extract_delivery_target(execution_context: dict[str, Any] | None) -> dict[str, str] | None:
    """Read the chat delivery binding from a goal/trigger's execution_context.

    ``run_goal`` binds ``{source: "chat", session_id, message_id}`` so that when
    the goal completes asynchronously (Phase 2) the result can be posted back into
    the originating conversation. Returns ``None`` when there is nothing to deliver
    to (non-chat source or no session).
    """
    if not execution_context or execution_context.get("source") != "chat":
        return None
    session_id = execution_context.get("session_id") or execution_context.get("conversation_id")
    if not session_id:
        return None
    target = {
        "session_id": str(session_id),
        "message_id": str(execution_context.get("message_id", "")),
    }
    # Carry the origin channel so an async result can also be pushed there (e.g.
    # WhatsApp) when the user isn't watching the web stream (Phase 2, scenario 11).
    channel = execution_context.get("channel")
    if channel:
        target["channel"] = str(channel)
        cuid = execution_context.get("channel_user_id")
        if cuid:
            target["channel_user_id"] = str(cuid)
    return target


def _now() -> datetime:
    return datetime.now(UTC)


def _hex() -> str:
    return uuid.uuid4().hex


# ── In-memory store (replaced by DB in production) ────────────────────────────


@dataclass
class _Session:
    id: str
    tenant_id: str
    title: str = "New Chat"
    pinned: bool = False
    ttl_days: int | None = None
    system_prompt: str | None = None
    agent_id: str | None = None
    folder_id: str | None = None
    show_reasoning: bool = False
    proactive_suggestions: bool = True
    preferred_model: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # The person (SSO user id) who created the session; None for an API key or a
    # channel. Only owned sessions can become knowledge (owner decision 7).
    owner_user_id: str | None = None
    # The principal that owns the session (CHAT-SEC-1: ``user:<id>`` or
    # ``key:<api key id>``); None for a channel or an older session (admin only).
    owner_principal: str | None = None


@dataclass
class _Message:
    id: str
    session_id: str
    tenant_id: str
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    branch_id: str | None = None
    parent_message_id: str | None = None
    goal_id: str | None = None
    intent: str | None = None
    created_at: datetime = field(default_factory=_now)


@dataclass
class _Folder:
    id: str
    tenant_id: str
    name: str
    color: str = "#6366f1"
    position: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # CHAT-D-1: the principal that owns the folder (as for sessions).
    owner_principal: str | None = None


class ChatFolderLimitError(ValueError):
    """The principal already has the maximum number of chat folders."""


@dataclass
class _Feedback:
    """One person's feedback on one chat reply (CHAT-D-2)."""

    message_id: str
    session_id: str
    tenant_id: str
    owner_principal: str
    rating: int
    comment: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class _Artifact:
    id: str
    session_id: str
    tenant_id: str
    message_id: str | None
    title: str
    language: str
    content: str
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class _Usage:
    id: str
    message_id: str
    session_id: str
    tenant_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model: str = ""
    created_at: datetime = field(default_factory=_now)


@dataclass
class _ChatAsyncJob:
    """Tracks an acknowledge-now / deliver-later request (Phase 2, R14).

    So a follow-up lands in the right conversation (and origin channel) even hours
    later or after the user goes offline. Durable store swaps in later.
    """

    id: str
    session_id: str
    tenant_id: str
    status: str = "running"  # running | done | error
    origin_message_id: str | None = None
    channel: str | None = None
    channel_user_id: str | None = None
    result_ref: str | None = None  # message id or artifact id of the delivered result
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


class ChatService:
    """In-memory ChatService — suitable for unit tests and the in-memory app path.

    The production lifespan swaps this for the DB-backed version.
    """

    def __init__(
        self,
        goal_service: Any = None,
        answer_generator: Any = None,
        memory_recall: Any = None,
        memory_writer: Any = None,
        nl_scheduler: Any = None,
        schedule_store: Any = None,
        skill_registry: Any = None,
        repository: Any = None,
    ) -> None:
        self._sessions: dict[str, _Session] = {}
        self._messages: dict[str, _Message] = {}
        self._folders: dict[str, _Folder] = {}
        # In-memory feedback store: (tenant, message_id, principal) -> feedback.
        self._feedback: dict[tuple[str, str, str], _Feedback] = {}
        self._artifacts: dict[str, _Artifact] = {}
        self._usage: dict[str, _Usage] = {}
        self._router = IntentRouter()
        self._ctx = ConversationContext()
        # Phase 1: summarization caches.
        #  - _summary_cache: one-shot summaries keyed by a content hash (whole-session
        #    distillation / no rolling context).
        #  - _rolling_summary: per-session (covered_message_count, summary_text) so a
        #    long session summarizes only the *delta* each turn and returns the stored
        #    summary with zero LLM calls when the older prefix is unchanged.
        self._summary_cache: dict[str, str] = {}
        self._rolling_summary: dict[str, tuple[int, str]] = {}
        # Phase 11: per-principal personalization (tone / standing instructions /
        # preferences), injected into every QA turn. In-memory now; durable later.
        from app.chat.personalization import InMemoryPersonalizationStore

        self._personalization: Any = InMemoryPersonalizationStore()
        # Clarify-round counter per (tenant, session). The async dispatch path uses
        # ``_clarify_store`` (Redis-backed once the lifespan wires Redis, so every
        # replica and a restart see the same streak); the legacy sync ``dispatch``
        # serves only the in-memory session store and keeps a local dict.
        self._clarify_store: ClarifyRoundStore = InMemoryClarifyRoundStore()
        self._clarify_rounds: dict[str, int] = {}
        # Phase 3: (tenant, channel, channel_user_id) -> session_id — ONLY the no-DB
        # fallback of aget_or_create_channel_session. With a repository wired the
        # mapping is durable (chat_channel_sessions) and this map is unused.
        self._channel_sessions: dict[tuple[str, str, str], str] = {}
        # Per-(tenant, channel, channel_user_id) lock guarding the get-or-create in
        # aget_or_create_channel_session, so two concurrent inbound messages from the
        # same external chat can't both miss the cache and each create their own
        # session (forking the conversation in two).
        self._channel_session_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        # Phase 3 (cross-channel continuity): principal_id -> session_id, so a thread
        # started on one channel continues on another as the SAME conversation once
        # identities are linked. Populated when an IdentityService is wired.
        self._principal_sessions: dict[str, str] = {}
        self._identity: Any = None
        # Phase 2: acknowledge-now / deliver-later jobs (in-memory; durable later).
        self._async_jobs: dict[str, _ChatAsyncJob] = {}
        # Optional async hook to push a delivered result to the origin channel
        # (WhatsApp/Telegram/…): (channel, channel_user_id, text) -> awaitable.
        self._channel_deliver: Any = None
        # Real-engine dependencies (injected in the lifespan; None on the pure
        # in-memory/unit-test path). ``goal_service`` drives GOAL turns through the
        # real AgentGraph; ``answer_generator`` produces real QA answers.
        self._goal_service = goal_service
        self._answer_generator = answer_generator
        # Optional async hook: tenant_id -> the tenant's own (BYOK) provider or
        # None. Raises when a BYOK config exists but cannot be read/used, so a
        # turn fails closed instead of silently spending on the platform key.
        self._provider_resolver: Any = None
        # Optional async hook: (query, tenant_id) -> list[str] of relevant memories,
        # recalled per QA turn and injected into the LLM context (Phase 1).
        self._memory_recall = memory_recall
        # Optional async hook: (fact, tenant_id) -> None, storing an explicit
        # "remember that ..." fact so future conversations recall it (Phase 1).
        self._memory_writer = memory_writer
        # Real scheduling engine (Phase 2): NL -> TriggerSpecs -> persisted schedules.
        self._nl_scheduler = nl_scheduler
        self._schedule_store = schedule_store
        # Command-surface skill registry (Phase 5): "anything via chat".
        self._skill_registry = skill_registry
        # Durable persistence (Phase 0.3d swap): a PostgresChatRepository. When set,
        # the async a* methods read/write the DB; otherwise they fall back to the
        # in-memory dicts. Migration proceeds by moving the router to the a* methods.
        self._repository = repository

    # ── Session CRUD ──────────────────────────────────────────────────────────

    def create_session(
        self,
        tenant_id: str,
        title: str = "New Chat",
        system_prompt: str | None = None,
        agent_id: str | None = None,
        folder_id: str | None = None,
        owner_user_id: str | None = None,
        owner_principal: str | None = None,
    ) -> _Session:
        if folder_id is not None:
            self._require_folder(folder_id, tenant_id, owner_principal)
        sid = _hex()
        session = _Session(
            id=sid,
            tenant_id=tenant_id,
            title=title,
            system_prompt=system_prompt,
            agent_id=agent_id,
            folder_id=folder_id,
            owner_user_id=owner_user_id,
            owner_principal=owner_principal,
        )
        self._sessions[sid] = session
        return session

    def get_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> _Session | None:
        s = self._sessions.get(session_id)
        if s and s.tenant_id == tenant_id and scope.allows(s.owner_principal):
            return s
        return None

    def list_sessions(
        self, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> list[_Session]:
        sessions = [
            s
            for s in self._sessions.values()
            if s.tenant_id == tenant_id and scope.allows(s.owner_principal)
        ]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    # Fields a caller may change (owner fields never: see assign_unowned_session).
    _UPDATABLE = frozenset(
        {
            "title", "pinned", "ttl_days", "system_prompt", "agent_id", "folder_id",
            "show_reasoning", "proactive_suggestions", "preferred_model",
        }
    )

    def update_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE, **kwargs: Any
    ) -> _Session | None:
        s = self.get_session(session_id, tenant_id, scope=scope)
        if not s:
            return None
        if kwargs.get("folder_id") is not None:
            self._require_folder(str(kwargs["folder_id"]), tenant_id, s.owner_principal)
        for k, v in kwargs.items():
            if k in self._UPDATABLE:
                setattr(s, k, v)
        s.updated_at = _now()
        return s

    def delete_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> bool:
        s = self.get_session(session_id, tenant_id, scope=scope)
        if not s:
            return False
        del self._sessions[session_id]
        # Delete associated messages
        for mid in [m.id for m in self._messages.values() if m.session_id == session_id]:
            self._messages.pop(mid, None)
        return True

    def pin_session(self, session_id: str, tenant_id: str, pinned: bool) -> _Session | None:
        return self.update_session(session_id, tenant_id, pinned=pinned)

    # ── Message CRUD ──────────────────────────────────────────────────────────

    def save_message(
        self,
        session_id: str,
        tenant_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        branch_id: str | None = None,
        parent_message_id: str | None = None,
        goal_id: str | None = None,
        intent: str | None = None,
    ) -> _Message:
        msg = _Message(
            id=_hex(),
            session_id=session_id,
            tenant_id=tenant_id,
            role=role,
            content=content,
            metadata=metadata or {},
            branch_id=branch_id,
            parent_message_id=parent_message_id,
            goal_id=goal_id,
            intent=intent,
        )
        self._messages[msg.id] = msg
        # Update session timestamp
        if session_id in self._sessions:
            self._sessions[session_id].updated_at = _now()
        return msg

    def list_messages(
        self,
        session_id: str,
        tenant_id: str,
        limit: int = 100,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> list[_Message]:
        if self.get_session(session_id, tenant_id, scope=scope) is None and (
            scope.kind != "system"
        ):
            return []
        msgs = [
            m
            for m in self._messages.values()
            if m.session_id == session_id and m.tenant_id == tenant_id
        ]
        return sorted(msgs, key=lambda m: m.created_at)[-limit:]

    def edit_message(
        self,
        message_id: str,
        tenant_id: str,
        new_content: str,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> tuple[_Message | None, list[str]]:
        """Edit a user message and return (updated_message, pruned_message_ids).

        All messages after this one in the same branch are pruned.
        """
        msg = self._messages.get(message_id)
        if not msg or msg.tenant_id != tenant_id or msg.role != "user":
            return None, []
        if self.get_session(msg.session_id, tenant_id, scope=scope) is None:
            return None, []

        msg.content = new_content
        # Prune subsequent messages in same session
        all_msgs = sorted(
            [m for m in self._messages.values() if m.session_id == msg.session_id],
            key=lambda m: m.created_at,
        )
        pruned: list[str] = []
        found = False
        for m in all_msgs:
            if found:
                pruned.append(m.id)
                del self._messages[m.id]
            if m.id == message_id:
                found = True

        return msg, pruned

    def delete_message(
        self,
        message_id: str,
        tenant_id: str,
        *,
        session_id: str | None = None,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> bool:
        msg = self._messages.get(message_id)
        if not msg or msg.tenant_id != tenant_id:
            return False
        if session_id is not None and msg.session_id != session_id:
            return False
        session = self.get_session(msg.session_id, tenant_id, scope=scope)
        if session is None and scope.kind != "system":
            return False
        del self._messages[message_id]
        if session is not None:
            session.updated_at = _now()
        return True

    # ── Usage tracking ────────────────────────────────────────────────────────

    def record_usage(
        self,
        message_id: str,
        session_id: str,
        tenant_id: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        model: str,
    ) -> _Usage:
        u = _Usage(
            id=_hex(),
            message_id=message_id,
            session_id=session_id,
            tenant_id=tenant_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            model=model,
        )
        self._usage[u.id] = u
        return u

    def session_usage_summary(self, session_id: str, tenant_id: str) -> dict[str, Any]:
        records = [
            u
            for u in self._usage.values()
            if u.session_id == session_id and u.tenant_id == tenant_id
        ]
        return {
            "session_id": session_id,
            "total_tokens": sum(u.tokens_in + u.tokens_out for u in records),
            "total_tokens_in": sum(u.tokens_in for u in records),
            "total_tokens_out": sum(u.tokens_out for u in records),
            "total_cost_usd": sum(u.cost_usd for u in records),
            "llm_calls": len(records),
        }

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def adispatch(
        self,
        session_id: str,
        tenant_id: str,
        user_message: str,
        *,
        author_user_id: str | None = None,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> dict[str, Any]:
        """Async, repository-backed dispatch (persistence swap stage 3c).

        Mirrors ``dispatch`` but reads history and persists the user message via
        the async a* methods, so a chat turn is durable. Intent is classified
        before the save, so the stored message carries it, and the person who
        wrote it (``metadata.author_user_id``) when one did.
        """
        if await self.aget_session(session_id, tenant_id, scope=scope) is None:
            raise ValueError(f"Session {session_id} not found")

        history = [
            {"role": m.role, "content": m.content}
            for m in await self.alist_messages(session_id, tenant_id, limit=1000, scope=scope)
        ]
        clarify_round = await self._clarify_store.get(tenant_id, session_id)
        intent = self._router.classify(
            user_message, history=history, clarify_round=clarify_round
        )
        user_msg = await self.asave_message(
            session_id=session_id, tenant_id=tenant_id, role="user",
            content=user_message, intent=intent.value,
            metadata={"author_user_id": author_user_id} if author_user_id else None,
            scope=scope,
        )
        result: dict[str, Any] = {
            "intent": intent.value,
            "message_id": user_msg.id,
            "session_id": session_id,
            "clarify_request": None,
            "schedule_confirmation": None,
        }
        if intent == Intent.CLARIFY:
            new_round = await self._clarify_store.increment(tenant_id, session_id)
            result["clarify_request"] = self._router.generate_clarifying_question(
                user_message, history=history, round=new_round
            )
        elif intent == Intent.SCHEDULE:
            result["schedule_confirmation"] = self._router.generate_schedule_confirmation(
                user_message, history=history
            )
        else:
            await self._clarify_store.reset(tenant_id, session_id)
        return result

    def dispatch(
        self,
        session_id: str,
        tenant_id: str,
        user_message: str,
    ) -> dict[str, Any]:
        """Classify intent and return dispatch metadata (not the stream itself).

        Returns:
            {
                "intent": str,
                "message_id": str,
                "session_id": str,
                "clarify_request": ClarifyRequest | None,
                "schedule_confirmation": ScheduleConfirmation | None,
            }
        """
        session = self.get_session(session_id, tenant_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Build history
        history_msgs = self.list_messages(session_id, tenant_id)
        history = [{"role": m.role, "content": m.content} for m in history_msgs]

        # Save user message
        user_msg = self.save_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="user",
            content=user_message,
        )

        # Classify
        clarify_round = self._clarify_rounds.get(session_id, 0)
        intent = self._router.classify(user_message, history=history, clarify_round=clarify_round)

        # Update message with intent
        user_msg.intent = intent.value

        result: dict[str, Any] = {
            "intent": intent.value,
            "message_id": user_msg.id,
            "session_id": session_id,
            "clarify_request": None,
            "schedule_confirmation": None,
        }

        if intent == Intent.CLARIFY:
            self._clarify_rounds[session_id] = clarify_round + 1
            result["clarify_request"] = self._router.generate_clarifying_question(
                user_message, history=history, round=clarify_round + 1
            )
        elif intent == Intent.SCHEDULE:
            result["schedule_confirmation"] = self._router.generate_schedule_confirmation(
                user_message, history=history
            )
        else:
            # Reset clarify round on successful classification
            self._clarify_rounds.pop(session_id, None)

        return result

    def attach_engine(
        self,
        goal_service: Any = None,
        answer_generator: Any = None,
        memory_recall: Any = None,
        memory_writer: Any = None,
        nl_scheduler: Any = None,
        schedule_store: Any = None,
        skill_registry: Any = None,
        repository: Any = None,
        personalization_store: Any = None,
        identity_service: Any = None,
        channel_deliver: Any = None,
        provider_resolver: Any = None,
        clarify_store: ClarifyRoundStore | None = None,
    ) -> None:
        """Wire real-engine dependencies AFTER construction.

        The lifespan builds ``ChatService`` early (router registration) but only
        constructs ``GoalService`` + providers later, then swaps in DB/Redis-backed
        services. This lets the lifespan attach those dependencies onto the same
        instance without rebuilding it. Only non-None args are applied, so partial
        wiring (e.g. goal_service now, answer_generator later) is safe.
        """
        if goal_service is not None:
            self._goal_service = goal_service
        if answer_generator is not None:
            self._answer_generator = answer_generator
        if memory_recall is not None:
            self._memory_recall = memory_recall
        if memory_writer is not None:
            self._memory_writer = memory_writer
        if nl_scheduler is not None:
            self._nl_scheduler = nl_scheduler
        if schedule_store is not None:
            self._schedule_store = schedule_store
        if skill_registry is not None:
            self._skill_registry = skill_registry
        if repository is not None:
            self._repository = repository
        if personalization_store is not None:
            self._personalization = personalization_store
        if identity_service is not None:
            self._identity = identity_service
        if channel_deliver is not None:
            self._channel_deliver = channel_deliver
        if provider_resolver is not None:
            self._provider_resolver = provider_resolver
        if clarify_store is not None:
            self._clarify_store = clarify_store

    async def _qa_provider(self, tenant_id: str) -> Any:
        """The provider a tenant's chat LLM calls use: its BYOK one, else the platform's."""
        if self._provider_resolver is not None:
            byok = await self._provider_resolver(tenant_id)
            if byok is not None:
                return byok
        return self._answer_generator

    @staticmethod
    def _principal_id(tenant_id: str, user_id: str | None = None) -> str:
        """Resolve the personalization principal.

        Defaults to the tenant; once the dual-mode identity layer lands, a
        standalone individual's ``user_id`` scopes their personal profile within
        the tenant (``tenant:user``).
        """
        return f"{tenant_id}:{user_id}" if user_id else tenant_id

    # ── Real-engine capability flags ───────────────────────────────────────────

    @property
    def can_run_goals(self) -> bool:
        """True when a real GoalService is wired (GOAL turns hit the real engine)."""
        return self._goal_service is not None

    @property
    def can_generate_answers(self) -> bool:
        """True when a real answer generator is wired (QA turns get real answers)."""
        return self._answer_generator is not None

    @property
    def can_recall_memory(self) -> bool:
        """True when a memory-recall hook is wired (QA injects long-term memory)."""
        return self._memory_recall is not None

    def list_skills(self, scopes: frozenset[str] | None = None) -> list[dict[str, Any]]:
        """Discover the chat command-surface skills (Phase 5), scope-filtered."""
        if self._skill_registry is None:
            return []
        out: list[dict[str, Any]] = []
        for skill in self._skill_registry.list():
            if scopes is not None and skill.scope is not None and skill.scope not in scopes:
                continue
            out.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "args": skill.args,
                    "scope": skill.scope,
                }
            )
        return out

    @property
    def can_schedule(self) -> bool:
        """True when the real scheduling engine is wired (SCHEDULE creates triggers)."""
        return self._nl_scheduler is not None and self._schedule_store is not None

    async def create_schedule(
        self, *, tenant_ctx: Any, message: str, agent_id: str | None = None
    ) -> list[str]:
        """Parse a NL scheduling request and persist the real schedule(s).

        Returns the created schedule ids. Replaces the old preview-only path where
        a SCHEDULE-intent chat turn produced a confirmation but created nothing.
        """
        if not self.can_schedule:
            raise RuntimeError("chat scheduling requires nl_scheduler + schedule_store wired")
        import secrets

        from app.triggers.models import TriggerType
        from app.triggers.store import create_schedules_atomically
        from app.triggers.validation import creatable_error

        specs = await self._nl_scheduler.parse(message, tenant_ctx=tenant_ctx)
        # TRG-10: the same gate and plan quota as POST /schedules. This persisted
        # every parsed spec unchecked, so an unsupported type or a "once" with no
        # time was stored and never fired, and PLAN_MAX_TRIGGERS was bypassed.
        if not specs:
            raise ValueError("no schedule could be understood from the message")
        plan = str(getattr(tenant_ctx, "plan", "free") or "free")
        for spec in specs:
            if spec.trigger_type == TriggerType.WEBHOOK and not spec.webhook_token:
                spec.webhook_token = secrets.token_hex(16)
        problems = [
            f"{spec.trigger_type.value}: {reason}"
            for spec in specs
            if (reason := creatable_error(spec, plan=plan)) is not None
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return await create_schedules_atomically(
            self._schedule_store,
            specs,
            goal_id=message,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id or "",
            goal_template=message,
            quota_plan=plan,
        )

    def deliver_result(
        self,
        *,
        session_id: str,
        tenant_id: str,
        content: str,
        goal_id: str | None = None,
    ) -> _Message | None:
        """Post an async goal/schedule result back into a conversation (Phase 2).

        Called when work bound to this conversation completes out-of-band, so the
        user sees the answer in the thread even if they weren't watching the live
        stream. Returns None if the session no longer exists.
        """
        if self.get_session(session_id, tenant_id) is None:
            return None
        return self.save_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            content=content,
            goal_id=goal_id,
            metadata={"delivery": "async"},
        )

    async def adeliver_result(
        self,
        *,
        session_id: str,
        tenant_id: str,
        content: str,
        goal_id: str | None = None,
    ) -> _Message | None:
        """Durable variant of :meth:`deliver_result`.

        Writes through the repository when wired so an out-of-band goal/schedule
        result lands in the *same* persisted thread the web/API UI reads back;
        falls back to the in-memory path otherwise.
        """
        if await self.aget_session(session_id, tenant_id) is None:
            return None
        msg = await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            content=content,
            goal_id=goal_id,
            metadata={"delivery": "async"},
        )
        return msg

    # ── Acknowledge-now / deliver-later (Phase 2, R14 — the "recipe" pattern) ───

    _DEFAULT_ACK = "On it — I'll send it here when it's ready."

    async def acknowledge_async(
        self,
        *,
        session_id: str,
        tenant_id: str,
        ack_text: str | None = None,
        origin_message_id: str | None = None,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> tuple[_ChatAsyncJob, _Message | None]:
        """Reply immediately and open a tracked async job.

        Posts an "On it — I'll send it when ready" assistant message now, and returns
        a job record so the eventual result can be delivered back into this same
        thread (and origin channel) even hours later or after the user goes offline.
        """
        ack_msg = await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            content=ack_text or self._DEFAULT_ACK,
            metadata={"delivery": "ack"},
        )
        job = _ChatAsyncJob(
            id=_hex(),
            session_id=session_id,
            tenant_id=tenant_id,
            origin_message_id=origin_message_id,
            channel=channel,
            channel_user_id=channel_user_id,
        )
        self._async_jobs[job.id] = job
        return job, ack_msg

    def get_async_job(self, job_id: str, tenant_id: str) -> _ChatAsyncJob | None:
        job = self._async_jobs.get(job_id)
        return job if job and job.tenant_id == tenant_id else None

    async def complete_async_job(
        self,
        *,
        job_id: str,
        tenant_id: str,
        content: str,
        goal_id: str | None = None,
        artifact_id: str | None = None,
    ) -> _Message | None:
        """Deliver an async job's result back into its thread (and origin channel).

        Posts the result to the originating conversation and, when the job was bound
        to an external channel, pushes it there too (so an offline user still gets it
        on WhatsApp/Telegram). Marks the job done.
        """
        job = self._async_jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            return None
        msg = await self.adeliver_result(
            session_id=job.session_id, tenant_id=tenant_id, content=content, goal_id=goal_id
        )
        if job.channel and self._channel_deliver is not None:
            with contextlib.suppress(Exception):
                await self._channel_deliver(job.channel, job.channel_user_id, content)
        job.status = "done"
        job.result_ref = artifact_id or (msg.id if msg else None)
        job.updated_at = _now()
        return msg

    async def deliver_proactive(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        message: str,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> _Message | None:
        """Deliver a proactive (agent-initiated) message into a principal's thread.

        Posts an assistant message into the principal's existing conversation
        (creating one if none is open yet) so a proactive nudge lands in the same
        place the user already talks to the assistant, and pushes to the origin
        channel when one is bound. Metadata marks it ``delivery=proactive`` so the UI
        can badge it distinctly. Returns the delivered message (or None on failure).
        """
        session_id = self._principal_sessions.get(principal_id)
        if session_id is None or await self.aget_session(session_id, tenant_id) is None:
            title = f"Proactive · {channel or 'assistant'}"
            session = await self.acreate_session(tenant_id, title=title)
            session_id = session.id
            self._principal_sessions[principal_id] = session_id
        msg = await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            content=message,
            metadata={"delivery": "proactive"},
        )
        if channel and self._channel_deliver is not None:
            with contextlib.suppress(Exception):
                await self._channel_deliver(channel, channel_user_id, message)
        return msg

    async def fail_async_job(self, *, job_id: str, tenant_id: str, error: str) -> None:
        job = self._async_jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            return
        job.status = "error"
        job.result_ref = error[:500]
        job.updated_at = _now()

    # ── Async, repository-backed CRUD (Phase 0.3d durable persistence) ─────────
    # When a repository is wired these read/write Postgres; otherwise they fall
    # back to the in-memory sync methods so unit tests keep working. The router
    # migrates to these a* methods to make chat durable across restarts.

    @staticmethod
    def _session_from_row(row: dict[str, Any]) -> _Session:
        return _Session(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            title=row.get("title") or "New Chat",
            pinned=bool(row.get("pinned", False)),
            ttl_days=row.get("ttl_days"),
            system_prompt=row.get("system_prompt"),
            agent_id=row.get("agent_id"),
            folder_id=row.get("folder_id"),
            show_reasoning=bool(row.get("show_reasoning", False)),
            proactive_suggestions=bool(row.get("proactive_suggestions", True)),
            preferred_model=row.get("preferred_model"),
            created_at=row.get("created_at") or _now(),
            updated_at=row.get("updated_at") or _now(),
            owner_user_id=row.get("owner_user_id"),
            owner_principal=row.get("owner_principal"),
        )

    async def acreate_session(
        self,
        tenant_id: str,
        title: str = "New Chat",
        system_prompt: str | None = None,
        agent_id: str | None = None,
        folder_id: str | None = None,
        owner_user_id: str | None = None,
        owner_principal: str | None = None,
    ) -> _Session:
        if self._repository is None:
            return self.create_session(
                tenant_id, title=title, system_prompt=system_prompt,
                agent_id=agent_id, folder_id=folder_id, owner_user_id=owner_user_id,
                owner_principal=owner_principal,
            )
        sid = _hex()
        await self._repository.create_session(
            session_id=sid, tenant_id=tenant_id, title=title,
            system_prompt=system_prompt, agent_id=agent_id, folder_id=folder_id,
            owner_user_id=owner_user_id, owner_principal=owner_principal,
        )
        row = await self._repository.get_session(sid, tenant_id, scope=SYSTEM_SCOPE)
        return self._session_from_row(row) if row else _Session(
            id=sid, tenant_id=tenant_id, title=title, system_prompt=system_prompt,
            agent_id=agent_id, folder_id=folder_id, owner_user_id=owner_user_id,
            owner_principal=owner_principal,
        )

    async def aget_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> _Session | None:
        if self._repository is None:
            return self.get_session(session_id, tenant_id, scope=scope)
        row = await self._repository.get_session(session_id, tenant_id, scope=scope)
        return self._session_from_row(row) if row else None

    async def alist_sessions(
        self, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> list[_Session]:
        if self._repository is None:
            return self.list_sessions(tenant_id, scope=scope)
        rows = await self._repository.list_sessions(tenant_id, scope=scope)
        return [self._session_from_row(r) for r in rows]

    async def aupdate_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE, **kwargs: Any
    ) -> _Session | None:
        if self._repository is None:
            return self.update_session(session_id, tenant_id, scope=scope, **kwargs)
        await self._repository.update_session(session_id, tenant_id, scope=scope, **kwargs)
        return await self.aget_session(session_id, tenant_id, scope=scope)

    async def adelete_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> bool:
        if self._repository is None:
            return self.delete_session(session_id, tenant_id, scope=scope)
        return bool(await self._repository.delete_session(session_id, tenant_id, scope=scope))

    async def aassign_unowned_session(
        self, session_id: str, tenant_id: str, *, owner_user_id: str
    ) -> _Session | None:
        """Give a session with no owner to a person; None when it is not unowned."""
        if self._repository is None:
            s = self._sessions.get(session_id)
            if s is None or s.tenant_id != tenant_id or s.owner_principal is not None:
                return None
            s.owner_principal = f"user:{owner_user_id}"
            s.owner_user_id = owner_user_id
            s.updated_at = _now()
            return s
        if not await self._repository.assign_unowned_session(
            session_id, tenant_id, owner_user_id=owner_user_id
        ):
            return None
        return await self.aget_session(session_id, tenant_id)

    @staticmethod
    def _message_from_row(row: dict[str, Any]) -> _Message:
        return _Message(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            tenant_id=str(row["tenant_id"]),
            role=str(row.get("role", "user")),
            content=row.get("content") or "",
            metadata=row.get("metadata") or {},
            branch_id=row.get("branch_id"),
            parent_message_id=row.get("parent_message_id"),
            goal_id=row.get("goal_id"),
            intent=row.get("intent"),
            created_at=row.get("created_at") or _now(),
        )

    async def asave_message(
        self,
        *,
        session_id: str,
        tenant_id: str,
        role: str,
        content: str,
        goal_id: str | None = None,
        intent: str | None = None,
        metadata: dict[str, Any] | None = None,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> _Message:
        if self._repository is None:
            if scope.kind != "system" and self.get_session(
                session_id, tenant_id, scope=scope
            ) is None:
                raise LookupError(f"chat session {session_id} not found")
            return self.save_message(
                session_id=session_id, tenant_id=tenant_id, role=role,
                content=content, goal_id=goal_id, intent=intent, metadata=metadata,
            )
        message_id = _hex()
        await self._repository.save_message(
            message_id=message_id, session_id=session_id, tenant_id=tenant_id,
            role=role, content=content, intent=intent, goal_id=goal_id, metadata=metadata,
            scope=scope,
        )
        await self._notify_transcript(tenant_id, session_id)
        return _Message(
            id=message_id, session_id=session_id, tenant_id=tenant_id, role=role,
            content=content, goal_id=goal_id, intent=intent, metadata=metadata or {},
        )

    async def _notify_transcript(self, tenant_id: str, session_id: str) -> None:
        """CHAT-KB: a consented session changed — its transcript is re-indexed."""
        from app.ingestion.agent_generated_events import notify_chat_transcript

        await notify_chat_transcript(
            tenant_id, session_id, db_factory=getattr(self._repository, "_sf", None)
        )

    async def alist_messages(
        self,
        session_id: str,
        tenant_id: str,
        limit: int = 100,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> list[_Message]:
        if self._repository is None:
            return self.list_messages(session_id, tenant_id, limit=limit, scope=scope)
        rows = await self._repository.list_messages(session_id, tenant_id, scope=scope)
        msgs = [self._message_from_row(r) for r in rows]
        return msgs[-limit:] if limit else msgs

    async def aget_message(
        self, session_id: str, message_id: str, tenant_id: str, *, scope: ChatScope
    ) -> _Message | None:
        """One message of one session, within the caller's scope."""
        if self._repository is None:
            msg = self._messages.get(message_id)
            if msg is None or msg.tenant_id != tenant_id or msg.session_id != session_id:
                return None
            return msg if self.get_session(session_id, tenant_id, scope=scope) else None
        row = await self._repository.get_message(message_id, tenant_id, scope=scope)
        if row is None or str(row.get("session_id")) != session_id:
            return None
        return self._message_from_row(row)

    async def adelete_message(
        self, session_id: str, message_id: str, tenant_id: str, *, scope: ChatScope
    ) -> bool:
        """Hard-delete one message (CHAT-SEC-2): the row leaves the database."""
        if self._repository is None:
            return self.delete_message(
                message_id, tenant_id, session_id=session_id, scope=scope
            )
        deleted = bool(
            await self._repository.delete_message(
                session_id, message_id, tenant_id, scope=scope
            )
        )
        if deleted:  # the session changed: a consented transcript is re-indexed
            await self._notify_transcript(tenant_id, session_id)
        return deleted

    async def aedit_message(
        self,
        message_id: str,
        tenant_id: str,
        new_content: str,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> tuple[_Message | None, list[str]]:
        """Durable edit of a user message returning (updated_message, pruned_ids).

        All messages created after the edited one in the same session are pruned
        (branch pruning). Falls back to the in-memory path when no repository is
        wired.
        """
        if self._repository is None:
            return self.edit_message(message_id, tenant_id, new_content, scope=scope)
        row = await self._repository.get_message(message_id, tenant_id, scope=scope)
        if row is None or str(row.get("role")) != "user":
            return None, []
        if not await self._repository.update_message_content(
            message_id, tenant_id, new_content, scope=scope
        ):
            return None, []
        pruned = await self._repository.delete_messages_after(
            str(row["session_id"]), tenant_id, row["created_at"], scope=scope
        )
        await self._notify_transcript(tenant_id, str(row["session_id"]))
        row["content"] = new_content
        return self._message_from_row(row), pruned

    async def attach_file(
        self,
        *,
        session_id: str,
        tenant_id: str,
        content_bytes: bytes,
        filename: str = "document",
        author_user_id: str | None = None,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> _Message | None:
        """Parse an uploaded file and store it as conversation context (Phase 4).

        The extracted text is saved as a user message so it's naturally included
        in the next turn's history (no run_qa/run_goal change needed). Returns None
        if the session doesn't exist.
        """
        if await self.aget_session(session_id, tenant_id, scope=scope) is None:
            return None
        from app.chat.attachments import format_attachment_context, parse_attachment

        parsed = await parse_attachment(content_bytes, filename)
        context = format_attachment_context(parsed)
        return await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="user",
            content=context,
            metadata={
                "attachment": filename,
                "kind": "attachment",
                **({"author_user_id": author_user_id} if author_user_id else {}),
            },
            scope=scope,
        )

    # ── Feedback on replies (CHAT-D-2) ────────────────────────────────────────
    # Thumbs (-1/0/1) and an optional comment, one per person per reply: rating
    # again edits it. Durable in chat_message_feedback when a repository is
    # wired; a reply that carries a goal also feeds goal_feedback (the
    # self-improvement signal) in the same transaction.

    @staticmethod
    def _feedback_from_row(row: dict[str, Any]) -> _Feedback:
        return _Feedback(
            message_id=str(row["message_id"]),
            session_id=str(row["session_id"]),
            tenant_id=str(row["tenant_id"]),
            owner_principal=str(row["owner_principal"]),
            rating=int(row["rating"]),
            comment=row.get("comment"),
            created_at=row.get("created_at") or _now(),
            updated_at=row.get("updated_at") or _now(),
        )

    async def asubmit_feedback(
        self,
        *,
        session_id: str,
        message_id: str,
        tenant_id: str,
        rating: int,
        comment: str | None,
        scope: ChatScope,
    ) -> _Feedback | None:
        """Save or edit the caller's feedback; None when the reply is not theirs.

        Raises ChatFeedbackTargetError for a message that is not an assistant
        reply.
        """
        from app.chat.repository import ChatFeedbackTargetError

        if scope.kind != "principal":
            raise ValueError("feedback is given by a principal")
        if self._repository is None:
            msg = await self.aget_message(session_id, message_id, tenant_id, scope=scope)
            if msg is None:
                return None
            if msg.role != "assistant":
                raise ChatFeedbackTargetError("feedback is given on assistant replies only")
            key = (tenant_id, message_id, str(scope.principal))
            fb = self._feedback.get(key)
            if fb is None:
                fb = _Feedback(
                    message_id=message_id, session_id=session_id, tenant_id=tenant_id,
                    owner_principal=str(scope.principal), rating=rating, comment=comment,
                )
                self._feedback[key] = fb
            else:
                fb.rating, fb.comment, fb.updated_at = rating, comment, _now()
            return fb
        row = await self._repository.upsert_feedback(
            tenant_id=tenant_id, session_id=session_id, message_id=message_id,
            rating=rating, comment=comment, scope=scope,
        )
        return self._feedback_from_row(row) if row is not None else None

    async def adelete_feedback(
        self, *, session_id: str, message_id: str, tenant_id: str, scope: ChatScope
    ) -> bool:
        if scope.kind != "principal":
            return False
        if self._repository is None:
            key = (tenant_id, message_id, str(scope.principal))
            fb = self._feedback.get(key)
            if fb is None or fb.session_id != session_id:
                return False
            del self._feedback[key]
            return True
        return bool(
            await self._repository.delete_feedback(
                tenant_id=tenant_id, session_id=session_id, message_id=message_id, scope=scope
            )
        )

    async def alist_feedback(
        self,
        *,
        session_id: str,
        tenant_id: str,
        message_ids: list[str],
        scope: ChatScope,
    ) -> dict[str, _Feedback]:
        """The caller's feedback on these messages of a session, by message id."""
        if scope.kind != "principal" or not message_ids:
            return {}
        if self._repository is None:
            found = (
                self._feedback.get((tenant_id, mid, str(scope.principal)))
                for mid in message_ids
            )
            return {f.message_id: f for f in found if f is not None and f.session_id == session_id}
        rows = await self._repository.list_feedback(
            tenant_id=tenant_id, session_id=session_id, message_ids=message_ids, scope=scope
        )
        return {str(r["message_id"]): self._feedback_from_row(r) for r in rows}

    # ── Real GOAL execution (replaces the old simulated stream) ────────────────

    async def run_goal(
        self,
        *,
        session_id: str,
        tenant_id: str,
        tenant_ctx: Any,
        message_id: str,
        user_message: str,
        agent_id: str | None = None,
    ) -> str:
        """Submit a GOAL-intent chat turn to the real GoalService and return its
        ``goal_id``. Binds the conversation via ``execution_context`` so async
        completion can be delivered back into this thread (Phase 2).

        CHAT-D-3: the goal runs with the session's configured agent, read from
        the same store the session lives in (the database when a repository is
        wired — it used to read process memory, find nothing, and let the goal
        auto-route to another agent). A session that no longer exists fails
        closed (LookupError) instead of running on a default agent. The caller
        has already authorized the session, so this read is not owner-narrowed.
        """
        if self._goal_service is None:
            raise RuntimeError("chat GOAL execution requires a GoalService to be wired")
        session = await self.aget_session(session_id, tenant_id)
        if session is None:
            raise LookupError(f"chat session {session_id} not found")
        result = await self._goal_service.submit_goal(
            goal=user_message,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id or session.agent_id,
            execution_context={
                "source": "chat",
                "conversation_id": session_id,
                "session_id": session_id,
                "message_id": message_id,
            },
        )
        return str(result.get("goal_id") or result.get("id") or "")

    async def stream_goal(
        self,
        *,
        goal_id: str,
        tenant_ctx: Any,
        session_id: str,
        message_id: str,
    ) -> AsyncIterator[str]:
        """Stream a running goal's REAL events back as canonical chat SSE frames."""
        if self._goal_service is None:
            raise RuntimeError("chat GOAL streaming requires a GoalService to be wired")
        from app.chat.goal_bridge import bridge_goal_events

        async for frame in bridge_goal_events(
            self._goal_service.subscribe_events(goal_id, tenant_ctx),
            session_id=session_id,
            message_id=message_id,
        ):
            yield frame

    async def run_qa(
        self,
        *,
        session_id: str,
        tenant_id: str,
        message_id: str,
        user_message: str,
    ) -> AsyncIterator[str]:
        """Stream a REAL LLM answer for a QA-intent turn and persist it.

        Builds context from stored history via ``ConversationContext`` (windowing
        + long-session compression) — replacing the old ``"Answering: <msg>"``
        echo. ``user_message`` is already saved by ``dispatch``; it is present in
        the history this reads.
        """
        del user_message  # already persisted; read from history
        if self._answer_generator is None:
            raise RuntimeError("chat QA requires an answer generator (LLM provider) to be wired")
        from app.chat.events import ChatEventType, sse_event
        from app.providers.base import CompletionRequest, Message

        session = await self.aget_session(session_id, tenant_id)
        # Fetch a generous window so long sessions trigger summarization (the
        # default list_messages limit is small); ConversationContext then windows
        # and compresses it down to what actually enters the prompt.
        history = [
            {"role": m.role, "content": m.content}
            for m in await self.alist_messages(session_id, tenant_id, limit=1000)
        ]
        system_prompt = session.system_prompt if session else None
        if len(history) > self._ctx.COMPRESS_THRESHOLD:
            # Long session: summarize the older portion with a real LLM call and
            # keep only the recent window verbatim (replaces the static placeholder).
            old = history[: -self._ctx.MAX_TURNS]
            recent = history[-self._ctx.MAX_TURNS :]
            summary = await self._summarize_history(
                old, session_id=session_id, tenant_id=tenant_id
            )
            turns = self._ctx.build_for_qa(recent, session_system_prompt=system_prompt)
            if summary:
                turns = [
                    {"role": "system", "content": f"[Earlier conversation summary]\n{summary}"},
                    *turns,
                ]
        else:
            turns = self._ctx.build_for_qa(history, session_system_prompt=system_prompt)
        latest_user = next(
            (m["content"] for m in reversed(history) if m.get("role") == "user"), ""
        )
        # Phase 11 (learn): capture a durable standing instruction ("always ...",
        # "from now on ...") stated in passing, into the principal's profile.
        principal_id = self._principal_id(tenant_id)
        with contextlib.suppress(Exception):
            from app.chat.personalization import extract_standing_instruction

            instruction = extract_standing_instruction(latest_user)
            if instruction:
                await self._personalization.add_standing_instruction(principal_id, instruction)
        # Phase 11 (apply): inject the principal's personalization first so tone +
        # standing instructions + preferences govern the whole reply.
        with contextlib.suppress(Exception):
            from app.chat.personalization import render_personalization_block

            profile = await self._personalization.get(principal_id)
            block = render_personalization_block(profile)
            if block:
                turns = self._ctx.inject_personalization(block, turns)
        # Phase 1 (write): persist an explicit "remember that ..." fact so future
        # conversations recall it.
        if self._memory_writer is not None:
            fact = _extract_memory_directive(latest_user)
            if fact:
                with contextlib.suppress(Exception):
                    await self._memory_writer(fact, tenant_id)
        # Phase 1 (read): recall relevant long-term/episodic memories for the latest
        # user message and inject them so the chat "remembers" across sessions/delays.
        if self._memory_recall is not None:
            try:
                memories = await self._memory_recall(latest_user, tenant_id)
            except Exception:
                memories = []
            if memories:
                turns = self._ctx.inject_long_term_memory([str(m) for m in memories], turns)
        # Reasoning models (Qwen3 / kimi) otherwise dump a long "Thinking Process"
        # into the chat — slow and noisy. Steer them to a direct final answer
        # (Qwen3 honours the "/no_think" hint) and cap the length.
        chat_msgs = [Message(role=t["role"], content=t["content"]) for t in turns]
        chat_msgs.insert(
            0,
            Message(
                role="system",
                content=(
                    "You are a helpful chat assistant. Reply with a direct, concise final "
                    "answer only. Do NOT include chain-of-thought, analysis, or a "
                    "'Thinking Process' section. /no_think"
                ),
            ),
        )
        request = CompletionRequest(messages=chat_msgs, model="", max_tokens=1024)

        yield sse_event(ChatEventType.MESSAGE_STARTED, session_id=session_id, message_id=message_id)
        from app.providers.guarded_completion import (
            DecisionBudgetExceededError,
            charge_streamed,
            complete_decision,
            preflight_decision,
        )

        # PROV-02: the tenant's own provider (BYOK) and a budget preflight BEFORE
        # any token is generated; chat used to be free, unmetered platform spend.
        try:
            generator = await self._qa_provider(tenant_id)
            await preflight_decision(role="chat_qa", tenant_id=tenant_id)
        except DecisionBudgetExceededError as exc:
            yield sse_event(
                ChatEventType.ERROR,
                session_id=session_id,
                message_id=message_id,
                code="llm_budget_exhausted",
                message=f"LLM budget exhausted, so no answer was generated ({exc}).",
            )
            yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)
            return
        except Exception as exc:  # BYOK config unreadable / unusable: never the platform key
            _logger.warning("chat_qa_provider_unavailable", error=str(exc)[:200])
            yield sse_event(
                ChatEventType.ERROR,
                session_id=session_id,
                message_id=message_id,
                code="llm_provider_unavailable",
                message="Your LLM provider configuration could not be used. Please check it.",
            )
            yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)
            return
        parts: list[str] = []
        stall_timeout = _llm_stall_timeout_seconds()
        stalled = False
        provider_failed = False
        streamed = False
        # Every character the provider streamed — reasoning AND answer (PROV-29:
        # charging only answer chunks made reasoning-model chat under-counted, and
        # free when the stream stalled before the answer began).
        generated = [0]
        streamer = getattr(generator, "stream_complete", None)
        try:
            if callable(streamer):
                # Split reasoning-model output: chain-of-thought → collapsible
                # reasoning panel; only the clean answer streams as the message.
                # Wrapped in a per-chunk stall timeout so a hung provider can't
                # keep this SSE connection (and the client waiting on it) open
                # forever — there is no timeout at the provider-call layer for
                # this path (unlike the agent loop's circuit breaker).
                streamed = True
                source = _count_chars(
                    _iter_with_stall_timeout(streamer(request), stall_timeout), generated
                )
                async for kind, chunk in _split_reasoning_stream(source):
                    if kind == "reasoning":
                        yield sse_event(
                            ChatEventType.REASONING, token=chunk, message_id=message_id
                        )
                    else:
                        parts.append(chunk)
                        yield sse_event(ChatEventType.TOKEN, token=chunk, message_id=message_id)
            else:
                # Provider without a streaming API — one-shot complete().
                resp = await asyncio.wait_for(
                    complete_decision(
                        generator,
                        request,
                        role="chat_qa",
                        tenant_id=tenant_id,
                        timeout_seconds=stall_timeout,
                    ),
                    timeout=stall_timeout,
                )
                text = getattr(resp, "content", "") or ""
                clean = _strip_reasoning(text)
                parts.append(clean)
                yield sse_event(ChatEventType.TOKEN, token=clean, message_id=message_id)
        except TimeoutError:
            stalled = True
            _logger.warning(
                "chat_qa_llm_stall_timeout", session_id=session_id, timeout=stall_timeout
            )
            yield sse_event(
                ChatEventType.ERROR,
                session_id=session_id,
                message_id=message_id,
                message=(
                    "The language model took too long to respond, so I stopped "
                    "waiting. Please try again."
                ),
            )
        except Exception as exc:
            # A provider failure is reported as an error event — a partial
            # answer is never saved as the assistant's reply.
            provider_failed = True
            _logger.warning(
                "chat_qa_llm_failed", session_id=session_id, error=str(exc)[:200]
            )
            yield sse_event(
                ChatEventType.ERROR,
                session_id=session_id,
                message_id=message_id,
                message="The language model request failed. Please try again.",
            )
        answer = _strip_reasoning("".join(parts))
        if streamed and generated[0] > 0:
            # Streams carry no usage object: charge an estimate (≈4 chars/token) of
            # the prompt and of everything generated (reasoning included, also for
            # a stalled or failed stream), so the turn reaches the tenant budget
            # and the ledger. A refusal here only stops later turns.
            prompt_chars = sum(len(str(m.content)) for m in chat_msgs)
            usage = SimpleNamespace(
                model=str(getattr(generator, "_default_model", "") or ""),
                input_tokens=max(1, prompt_chars // 4),
                output_tokens=max(1, generated[0] // 4),
            )
            try:
                await charge_streamed(usage, role="chat_qa", tenant_id=tenant_id)
            except DecisionBudgetExceededError:
                _logger.info("chat_qa_budget_exhausted_by_turn", session_id=session_id)
            except Exception as exc:
                _logger.warning("chat_qa_charge_failed", error=str(exc)[:200])
        if not provider_failed and (answer or not stalled):
            await self.asave_message(
                session_id=session_id, tenant_id=tenant_id, role="assistant", content=answer
            )
        yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)

    async def _summarize_history(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """LLM-summarize an older slice of a long conversation (Phase 1), cached.

        When ``session_id`` is given, summarization is **rolling/incremental**: the
        per-session ``(covered_count, summary)`` is kept, so an unchanged older
        prefix returns the stored summary with **zero** LLM calls, and a grown
        prefix summarizes only the *delta* and merges it into the running summary
        (bounded per-turn cost instead of re-summarizing everything each turn).
        Without a session it falls back to a content-hash one-shot cache.
        """
        if not messages or self._answer_generator is None:
            return ""

        if session_id is not None:
            prev = self._rolling_summary.get(session_id)
            if prev is not None:
                prev_count, prev_summary = prev
                if prev_count == len(messages):
                    return prev_summary  # exact cache hit — nothing new to summarize
                if 0 < prev_count < len(messages):
                    merged = await self._merge_summary(
                        prev_summary, messages[prev_count:], tenant_id=tenant_id
                    )
                    if merged:
                        self._rolling_summary[session_id] = (len(messages), merged)
                    return merged or prev_summary
            summary = await self._llm_summarize(messages, tenant_id=tenant_id)
            if summary:
                self._rolling_summary[session_id] = (len(messages), summary)
            return summary

        # One-shot (whole-session distillation): content-hash cache.
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        cache_key = _hashlib.sha256(convo.encode("utf-8")).hexdigest()
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached
        summary = await self._llm_summarize(messages, tenant_id=tenant_id)
        if summary:
            if len(self._summary_cache) >= 512:
                self._summary_cache.pop(next(iter(self._summary_cache)), None)
            self._summary_cache[cache_key] = summary
        return summary

    async def _llm_summarize(
        self, messages: list[dict[str, Any]], *, tenant_id: str | None = None
    ) -> str:
        """Raw LLM summarization of a message slice (no caching)."""
        from app.providers.base import CompletionRequest, Message

        convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        request = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Summarize this earlier part of a conversation in 3-5 sentences. "
                        "Preserve key facts, decisions, names, numbers and open threads."
                    ),
                ),
                Message(role="user", content=convo[:8000]),
            ],
            model="",
            max_tokens=300,
            temperature=0.0,
        )
        try:
            from app.providers.guarded_completion import complete_decision

            generator = await self._qa_provider(tenant_id) if tenant_id else self._answer_generator
            resp = await complete_decision(
                generator, request, role="chat_summary", tenant_id=tenant_id
            )
            return (getattr(resp, "content", "") or "").strip()
        except Exception:
            return ""

    async def _merge_summary(
        self, prior: str, new_messages: list[dict[str, Any]], *, tenant_id: str | None = None
    ) -> str:
        """Update a running summary with only the newer messages (incremental)."""
        from app.providers.base import CompletionRequest, Message

        delta = "\n".join(f"{m['role']}: {m['content']}" for m in new_messages)
        request = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You maintain a running summary of an earlier conversation. Update it "
                        "to incorporate the NEW messages, staying 3-6 sentences. Preserve key "
                        "facts, decisions, names, numbers and open threads."
                    ),
                ),
                Message(
                    role="user",
                    content=f"Running summary so far:\n{prior}\n\nNew messages:\n{delta[:8000]}",
                ),
            ],
            model="",
            max_tokens=300,
            temperature=0.0,
        )
        try:
            from app.providers.guarded_completion import complete_decision

            generator = await self._qa_provider(tenant_id) if tenant_id else self._answer_generator
            resp = await complete_decision(
                generator, request, role="chat_summary", tenant_id=tenant_id
            )
            return (getattr(resp, "content", "") or "").strip()
        except Exception:
            return ""

    async def extract_learnings_on_close(self, session_id: str, tenant_id: str) -> int:
        """On session close / idle, distill the conversation into durable memory.

        Summarizes the whole session and writes it as a long-term learning via the
        wired ``memory_writer`` so a future conversation recalls what happened here.
        No-op (returns 0) when no writer is wired or there is nothing to learn.
        Returns the number of learnings written.
        """
        if self._memory_writer is None:
            return 0
        history = [
            {"role": m.role, "content": m.content}
            for m in await self.alist_messages(session_id, tenant_id, limit=1000)
        ]
        if not history:
            return 0
        summary = await self._summarize_history(history, tenant_id=tenant_id)
        if not summary:
            return 0
        with contextlib.suppress(Exception):
            await self._memory_writer(summary, tenant_id)
            return 1
        return 0

    async def aget_or_create_channel_session(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        title: str | None = None,
    ) -> _Session:
        """Durable, identity-aware channel session resolution (Phase 3).

        When an IdentityService is wired, the (channel, channel_user_id) is resolved
        to a *principal* and the principal's existing open thread is preferred — so a
        conversation started on the web continues on WhatsApp (or months later on any
        channel) as the SAME session once identities are linked (R8). Otherwise the
        per-(tenant, channel, channel_user_id) mapping decides.

        Persistence: when the service is DB-backed (``repository`` wired) both
        mappings live in the repository (``chat_channel_sessions`` /
        ``chat_principal_sessions``), so every replica — and the process after a
        restart — resolves the SAME session. The repository creates the session and
        claims the mapping in one transaction with ``INSERT ... ON CONFLICT DO
        NOTHING``, so replicas racing on a user's first message converge on one
        session. The in-memory dicts are only the no-DB fallback.

        Fails closed: identity / store errors propagate. Swallowing them would fall
        through to creating a fresh session and silently fork the conversation.
        """
        principal_id: str | None = None
        if self._identity is not None:
            principal = await self._identity.resolve_principal(
                tenant_id=tenant_id, channel=channel, channel_user_id=channel_user_id
            )
            principal_id = str(principal.id)
            existing_id = await self._get_principal_session_id(tenant_id, principal_id)
            if existing_id is not None:
                existing = await self.aget_session(existing_id, tenant_id)
                if existing is not None:
                    return existing

        key = (tenant_id, channel, channel_user_id)
        # Serialize resolution per (tenant, channel, channel_user_id) within this
        # process. Without it, two inbound messages from the same external chat
        # arriving concurrently (a user double-sending, overlapping webhook
        # deliveries) could both miss the mapping and each create a session,
        # forking the conversation. Across replicas the DB unique key does the same
        # job. ``setdefault`` on a plain dict has no ``await`` point.
        lock = self._channel_session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._repository is not None:
                session = await self._resolve_durable_channel_session(
                    tenant_id=tenant_id,
                    channel=channel,
                    channel_user_id=channel_user_id,
                    title=title,
                )
            else:
                session = await self._resolve_memory_channel_session(
                    key, tenant_id=tenant_id, title=title
                )
            if principal_id is not None:
                await self._claim_principal_session(tenant_id, principal_id, session.id)
            return session

    async def _resolve_durable_channel_session(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        title: str | None,
    ) -> _Session:
        session_id = await self._repository.resolve_channel_session(
            tenant_id=tenant_id,
            channel=channel,
            channel_user_id=channel_user_id,
            new_session_id=_hex(),
            title=title or f"{channel}:{channel_user_id}",
        )
        session = await self.aget_session(str(session_id), tenant_id)
        if session is None:
            # The mapping row cascades away with its session, so this means the
            # store is inconsistent. Never paper over it with a fresh session.
            raise RuntimeError(
                f"channel mapping for {channel!r} resolved to missing chat session "
                f"{session_id!r}"
            )
        return session

    async def _resolve_memory_channel_session(
        self, key: tuple[str, str, str], *, tenant_id: str, title: str | None
    ) -> _Session:
        _, channel, channel_user_id = key
        existing_id = self._channel_sessions.get(key)
        if existing_id is not None:
            existing = await self.aget_session(existing_id, tenant_id)
            if existing is not None:
                return existing
        session = await self.acreate_session(
            tenant_id, title=title or f"{channel}:{channel_user_id}"
        )
        self._channel_sessions[key] = session.id
        return session

    async def _get_principal_session_id(self, tenant_id: str, principal_id: str) -> str | None:
        if self._repository is not None:
            found = await self._repository.get_principal_session(tenant_id, principal_id)
            return None if found is None else str(found)
        return self._principal_sessions.get(principal_id)

    async def _claim_principal_session(
        self, tenant_id: str, principal_id: str, session_id: str
    ) -> None:
        """Bind the principal to ``session_id`` unless it already has a live thread.

        Only reached when the principal had no (live) thread, so the in-memory
        fallback may overwrite a stale entry; the DB path keeps the first claim
        (``ON CONFLICT DO NOTHING``) — a deleted session cascades its row away.
        """
        if self._repository is not None:
            await self._repository.claim_principal_session(
                tenant_id=tenant_id, principal_id=principal_id, session_id=session_id
            )
            return
        self._principal_sessions[principal_id] = session_id

    async def ahandle_channel_message(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Durable unified entry point for an inbound channel message (Phase 3).

        Resolves the identity-aware durable session and runs the SAME async dispatch
        as web chat, so WhatsApp/Telegram/API and the web UI share one persisted
        pipeline with cross-channel continuity.
        """
        session = await self.aget_or_create_channel_session(
            tenant_id=tenant_id, channel=channel, channel_user_id=channel_user_id
        )
        result = await self.adispatch(session.id, tenant_id, text)
        result["channel"] = channel
        return result

    async def achannel_turn(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Handle an inbound channel message AND produce a reply to send back.

        Routes through the unified pipeline (same as web/voice), then derives the
        reply the channel should speak/send: a clarifying question, a schedule
        confirmation, a generated QA answer, or a goal acknowledgement — never a raw
        JSON blob. Returns ``{session_id, intent, reply, channel}``.
        """
        session = await self.aget_or_create_channel_session(
            tenant_id=tenant_id, channel=channel, channel_user_id=channel_user_id
        )
        # Persist the inbound user turn, then fulfill (decompose → execute each action).
        await self.asave_message(
            session_id=session.id, tenant_id=tenant_id, role="user",
            content=text, metadata={"channel": channel},
        )
        result = await self.afulfill(session_id=session.id, tenant_id=tenant_id, message=text)
        return {
            "session_id": session.id, "channel": channel,
            "reply": result["reply"], "actions": result.get("actions", []),
        }

    async def afulfill(
        self,
        *,
        session_id: str,
        tenant_id: str,
        message: str,
        tenant_ctx: Any = None,
    ) -> dict[str, Any]:
        """Decompose a message into actions and EXECUTE each (world-class handling).

        Splits compound requests ("schedule X and remember Y and answer Z"), then
        for each action: creates a DURABLE schedule (so it actually fires), stores a
        memory, submits a goal, or answers a question — combining the outcomes into
        one natural reply. Uses the wired LLM for understanding when available, with
        a deterministic fallback otherwise.
        """
        from app.chat.understanding import (
            GoalAction,
            QAAction,
            RememberAction,
            ScheduleAction,
            decompose,
        )

        try:
            _understanding_llm = await self._qa_provider(tenant_id)
        except Exception:  # BYOK unusable: deterministic understanding, no platform spend
            _understanding_llm = None
        actions = await decompose(message, llm=_understanding_llm, tenant_id=tenant_id)
        replies: list[str] = []
        executed: list[str] = []
        for act in actions:
            if isinstance(act, ScheduleAction):
                replies.append(await self._fulfill_schedule(act, tenant_id, tenant_ctx))
                executed.append("schedule")
            elif isinstance(act, RememberAction):
                replies.append(await self._fulfill_remember(act, tenant_id))
                executed.append("remember")
            elif isinstance(act, GoalAction):
                replies.append(
                    await self._fulfill_goal(act, tenant_id, tenant_ctx, session_id=session_id)
                )
                executed.append("goal")
            elif isinstance(act, QAAction):
                replies.append(
                    await self._collect_qa_reply(
                        session_id=session_id, tenant_id=tenant_id,
                        message_id=_hex(), text=act.question,
                    )
                )
                executed.append("qa")
        reply = "\n".join(r for r in replies if r).strip() or "Done."
        # A lone QA already persisted its answer via run_qa; only persist the combined
        # reply for schedule/remember/goal/compound turns (keeps the thread complete
        # without duplicating a pure-QA answer).
        pure_qa = executed == ["qa"]
        if not pure_qa:
            with contextlib.suppress(Exception):
                await self.asave_message(
                    session_id=session_id, tenant_id=tenant_id, role="assistant",
                    content=reply, metadata={"fulfilled": executed},
                )
        return {"session_id": session_id, "reply": reply, "actions": executed}

    async def _fulfill_schedule(self, act: Any, tenant_id: str, tenant_ctx: Any) -> str:
        """Create a durable schedule from a ScheduleAction of a compound turn.

        a06-F102-08: the same gate and plan quota as POST /schedules and the
        SCHEDULE intent (:meth:`create_schedule`). This path used to store the
        spec with no ``creatable_error`` check and no ``quota_plan`` (bypassing
        ``PLAN_MAX_TRIGGERS``), and any failure was answered "I'll ...", so a
        refused or failed create looked scheduled. It now says it scheduled only
        when the schedule was stored.
        """
        if not self.can_schedule:
            return (
                f"⚠️ I could not schedule: {act.task} {act.human} "
                "(scheduling is not available here)."
            )
        ctx = tenant_ctx or self._tenant_ctx(tenant_id)
        plan = str(getattr(ctx, "plan", "free") or "free")
        from app.triggers import validation
        from app.triggers.models import TriggerSpec, TriggerType
        from app.triggers.quota import TriggerQuotaExceeded

        try:
            spec = TriggerSpec(
                trigger_type=TriggerType.ONCE if act.once else TriggerType.CRON,
                description=act.human,
                goal_template=act.task,
                cron_expression="" if act.once else act.cron,
                fire_at_iso=act.fire_at_iso if act.once else "",
            )
            reason = validation.creatable_error(spec, plan=plan)
            if reason is not None:
                return f"⚠️ I could not schedule: {act.task} {act.human} ({reason})."
            schedule_id = await self._schedule_store.create_async(
                goal_id=act.task, spec=spec, tenant_ctx=ctx,
                agent_id=None, goal_template=act.task, quota_plan=plan,
            )
        except TriggerQuotaExceeded as exc:
            return f"⚠️ I could not schedule: {act.task} {act.human} ({exc})."
        except Exception as exc:
            _logger.warning(
                "chat_schedule_action_not_created", tenant_id=tenant_id,
                error=type(exc).__name__,
            )
            # The driver error stays in the log (it never reaches a channel).
            return f"⚠️ I could not schedule: {act.task} {act.human}. Please try again later."
        return f"✅ Scheduled — I'll {act.task} {act.human} (id {str(schedule_id)[:8]})."

    async def _fulfill_remember(self, act: Any, tenant_id: str) -> str:
        if self._memory_writer is None:
            return f"📝 Noted: {act.fact}"
        with contextlib.suppress(Exception):
            await self._memory_writer(act.fact, tenant_id)
        return f"✅ Got it — I'll remember: {act.fact}"

    async def _fulfill_goal(
        self, act: Any, tenant_id: str, tenant_ctx: Any, *, session_id: str
    ) -> str:
        """Submit a goal action of a channel turn with the session's agent (CHAT-D-3).

        Says it started only when the goal was accepted: a failed submit (budget,
        unknown agent, outage) is reported, never claimed as started.
        """
        if self._goal_service is None:
            return f"I can't run goals here yet, so I couldn't start: {act.goal}"
        ctx = tenant_ctx or self._tenant_ctx(tenant_id)
        try:
            session = await self.aget_session(session_id, tenant_id)
            if session is None:
                raise LookupError(f"chat session {session_id} not found")
            await self._goal_service.submit_goal(
                goal=act.goal, priority="normal", dry_run=False, tenant_ctx=ctx,
                agent_id=session.agent_id,
                execution_context={
                    "source": "chat", "conversation_id": session_id, "session_id": session_id,
                },
            )
        except Exception as exc:
            _logger.warning(
                "chat_goal_action_not_started", session_id=session_id,
                error=type(exc).__name__,
            )
            # The reason stays in the log (a driver error never reaches a channel).
            return f"⚠️ I could not start: {act.goal}. Please try again later."
        return f"🚀 On it — I've started working on: {act.goal}. I'll follow up here."

    @staticmethod
    def _tenant_ctx(tenant_id: str) -> Any:
        from app.tenancy.context import PlanTier, TenantContext

        return TenantContext(tenant_id=tenant_id, api_key_id="chat", plan=PlanTier.FREE)

    @staticmethod
    def _derive_channel_reply(dispatch: dict[str, Any]) -> str | None:
        """Reply string for a dispatch, or None when a QA answer must be generated."""
        clarify = dispatch.get("clarify_request")
        if clarify is not None:
            q = getattr(clarify, "question", None) or (
                clarify.get("question") if isinstance(clarify, dict) else None
            )
            return str(q or "Could you clarify that?")
        sched = dispatch.get("schedule_confirmation")
        if sched is not None:
            hs = getattr(sched, "human_schedule", None) or (
                sched.get("human_schedule") if isinstance(sched, dict) else None
            )
            return f"✅ Scheduled — I'll run that {hs or 'as requested'}."
        if str(dispatch.get("intent") or "").upper() == "GOAL":
            return "On it — I'm working on that and will follow up here."
        return None  # QA

    async def _collect_qa_reply(
        self, *, session_id: str, tenant_id: str, message_id: str, text: str
    ) -> str:
        """Run the real QA path and collect its streamed answer as a single string."""
        if self._answer_generator is None:
            return "I've received your message."
        import json as _json

        parts: list[str] = []
        with contextlib.suppress(Exception):
            async for frame in self.run_qa(
                session_id=session_id, tenant_id=tenant_id,
                message_id=message_id, user_message=text,
            ):
                s = frame[6:].strip() if frame.startswith("data: ") else frame.strip()
                with contextlib.suppress(Exception):
                    ev = _json.loads(s)
                    if ev.get("type") == "token":
                        parts.append(str(ev.get("token") or ""))
        answer = _strip_reasoning("".join(parts))
        if not answer:
            return "I've received your message."
        # Never send a raw JSON blob to a channel — humanize it.
        if answer[:1] in ("{", "["):
            return _humanize_value(answer) or answer
        return answer

    # ── Folder CRUD (CHAT-D-1) ────────────────────────────────────────────────
    # Folders are owned like sessions. The sync methods are the in-memory store;
    # the a* methods use the Postgres repository when one is wired (durable and
    # shared by every replica), else the in-memory store.

    def _require_folder(self, folder_id: str, tenant_id: str, owner: str | None) -> _Folder:
        """The folder a session of ``owner`` may be filed into, or ChatFolderNotFoundError."""
        f = self._folders.get(folder_id)
        if f is None or f.tenant_id != tenant_id or f.owner_principal != owner:
            raise ChatFolderNotFoundError(folder_id)
        return f

    def create_folder(
        self,
        tenant_id: str,
        name: str,
        color: str = "#6366f1",
        *,
        owner_principal: str | None = None,
    ) -> _Folder:
        from app.chat.repository import MAX_FOLDERS_PER_OWNER

        mine = [
            f for f in self._folders.values()
            if f.tenant_id == tenant_id and f.owner_principal == owner_principal
        ]
        if len(mine) >= MAX_FOLDERS_PER_OWNER:
            raise ChatFolderLimitError(f"at most {MAX_FOLDERS_PER_OWNER} folders")
        f = _Folder(
            id=_hex(), tenant_id=tenant_id, name=name, color=color,
            position=max((m.position for m in mine), default=-1) + 1,
            owner_principal=owner_principal,
        )
        self._folders[f.id] = f
        return f

    def list_folders(self, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE) -> list[_Folder]:
        folders = [
            f for f in self._folders.values()
            if f.tenant_id == tenant_id and scope.allows(f.owner_principal)
        ]
        return sorted(folders, key=lambda f: (f.position, f.created_at, f.id))

    def _visible_folder(
        self, folder_id: str, tenant_id: str, scope: ChatScope
    ) -> _Folder | None:
        f = self._folders.get(folder_id)
        if f is None or f.tenant_id != tenant_id or not scope.allows(f.owner_principal):
            return None
        return f

    def update_folder(
        self,
        folder_id: str,
        tenant_id: str,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
        name: str | None = None,
        color: str | None = None,
    ) -> _Folder | None:
        f = self._visible_folder(folder_id, tenant_id, scope)
        if f is None:
            return None
        if name is not None:
            f.name = name
        if color is not None:
            f.color = color
        f.updated_at = _now()
        return f

    def delete_folder(
        self, folder_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> bool:
        if self._visible_folder(folder_id, tenant_id, scope) is None:
            return False
        del self._folders[folder_id]
        # Unfile its sessions (the database does this with ON DELETE SET NULL).
        for s in self._sessions.values():
            if s.folder_id == folder_id:
                s.folder_id = None
        return True

    def move_session_to_folder(
        self,
        session_id: str,
        tenant_id: str,
        folder_id: str | None,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> _Session | None:
        """File a session into a folder of its own owner (None unfiles it).

        None when the session is not the caller's; ChatFolderNotFoundError when
        the folder is not the session owner's.
        """
        return self.update_session(session_id, tenant_id, scope=scope, folder_id=folder_id)

    @staticmethod
    def _folder_from_row(row: dict[str, Any]) -> _Folder:
        return _Folder(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            name=str(row.get("name") or ""),
            color=str(row.get("color") or "#6366f1"),
            position=int(row.get("position") or 0),
            created_at=row.get("created_at") or _now(),
            updated_at=row.get("updated_at") or row.get("created_at") or _now(),
            owner_principal=row.get("owner_principal"),
        )

    async def acreate_folder(
        self, tenant_id: str, name: str, color: str = "#6366f1", *, scope: ChatScope
    ) -> _Folder:
        """Create a folder owned by the caller's principal (a principal scope only)."""
        if scope.kind != "principal":
            raise ValueError("a chat folder is created by a principal")
        if self._repository is None:
            return self.create_folder(tenant_id, name, color, owner_principal=scope.principal)
        row = await self._repository.create_folder(
            folder_id=_hex(), tenant_id=tenant_id, name=name, color=color, scope=scope
        )
        if row is None:
            from app.chat.repository import MAX_FOLDERS_PER_OWNER

            raise ChatFolderLimitError(f"at most {MAX_FOLDERS_PER_OWNER} folders")
        return self._folder_from_row(row)

    async def alist_folders(self, tenant_id: str, *, scope: ChatScope) -> list[_Folder]:
        if self._repository is None:
            return self.list_folders(tenant_id, scope=scope)
        rows = await self._repository.list_folders(tenant_id, scope=scope)
        return [self._folder_from_row(r) for r in rows]

    async def aupdate_folder(
        self,
        folder_id: str,
        tenant_id: str,
        *,
        scope: ChatScope,
        name: str | None = None,
        color: str | None = None,
    ) -> _Folder | None:
        if self._repository is None:
            return self.update_folder(folder_id, tenant_id, scope=scope, name=name, color=color)
        row = await self._repository.update_folder(
            folder_id, tenant_id, scope=scope, name=name, color=color
        )
        return self._folder_from_row(row) if row is not None else None

    async def adelete_folder(self, folder_id: str, tenant_id: str, *, scope: ChatScope) -> bool:
        if self._repository is None:
            return self.delete_folder(folder_id, tenant_id, scope=scope)
        return bool(await self._repository.delete_folder(folder_id, tenant_id, scope=scope))

    async def amove_session_to_folder(
        self, session_id: str, tenant_id: str, folder_id: str | None, *, scope: ChatScope
    ) -> _Session | None:
        """Durable :meth:`move_session_to_folder` (same None / error contract)."""
        if self._repository is None:
            return self.move_session_to_folder(session_id, tenant_id, folder_id, scope=scope)
        if not await self._repository.update_session(
            session_id, tenant_id, scope=scope, folder_id=folder_id
        ):
            return None
        return await self.aget_session(session_id, tenant_id, scope=scope)

    # ── Artifact CRUD ─────────────────────────────────────────────────────────

    def create_artifact(
        self,
        session_id: str,
        tenant_id: str,
        title: str,
        language: str,
        content: str,
        message_id: str | None = None,
    ) -> _Artifact:
        a = _Artifact(
            id=_hex(),
            session_id=session_id,
            tenant_id=tenant_id,
            message_id=message_id,
            title=title,
            language=language,
            content=content,
        )
        self._artifacts[a.id] = a
        return a

    def list_artifacts(self, session_id: str, tenant_id: str) -> list[_Artifact]:
        return [
            a
            for a in self._artifacts.values()
            if a.session_id == session_id and a.tenant_id == tenant_id
        ]

    def update_artifact(self, artifact_id: str, tenant_id: str, content: str) -> _Artifact | None:
        a = self._artifacts.get(artifact_id)
        if not a or a.tenant_id != tenant_id:
            return None
        a.content = content
        a.updated_at = _now()
        return a

    def delete_artifact(self, artifact_id: str, tenant_id: str) -> bool:
        a = self._artifacts.get(artifact_id)
        if not a or a.tenant_id != tenant_id:
            return False
        del self._artifacts[artifact_id]
        return True

    # ── Durable artifact CRUD (ORG-42) ────────────────────────────────────────
    # With a repository attached (the lifespan path) session artifacts live in
    # ``chat_artifacts`` under RLS: every replica sees them and they survive a
    # restart. The sync methods above remain the in-memory/unit-test path.

    @staticmethod
    def _artifact_from_row(row: dict[str, Any]) -> _Artifact:
        return _Artifact(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            tenant_id=str(row["tenant_id"]),
            message_id=row.get("message_id"),
            title=str(row["title"]),
            language=str(row.get("language") or ""),
            content=bytes(row["content"]).decode("utf-8", errors="replace"),
            created_at=row.get("created_at") or _now(),
            updated_at=row.get("updated_at") or _now(),
        )

    async def acreate_artifact(
        self,
        session_id: str,
        tenant_id: str,
        title: str,
        language: str,
        content: str,
        message_id: str | None = None,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> _Artifact:
        if self._repository is None:
            return self.create_artifact(
                session_id, tenant_id, title, language, content, message_id
            )
        artifact_id = _hex()
        await self._repository.put_artifact(
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            kind="snippet",
            title=title,
            mime="text/plain; charset=utf-8",
            language=language,
            content=content.encode("utf-8"),
            session_id=session_id,
            message_id=message_id,
            scope=scope,
        )
        row = await self._repository.get_artifact(
            artifact_id, tenant_id, scope=scope, kind="snippet"
        )
        if row is None:
            raise RuntimeError("chat artifact was not readable after insert")
        return self._artifact_from_row(row)

    async def alist_artifacts(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> list[_Artifact]:
        if self._repository is None:
            if self.get_session(session_id, tenant_id, scope=scope) is None:
                return []
            return self.list_artifacts(session_id, tenant_id)
        rows = await self._repository.list_session_artifacts(session_id, tenant_id, scope=scope)
        return [self._artifact_from_row(r) for r in rows]

    async def aupdate_artifact(
        self,
        artifact_id: str,
        session_id: str,
        tenant_id: str,
        content: str,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> _Artifact | None:
        if self._repository is None:
            a = self._artifacts.get(artifact_id)
            if a is None or a.session_id != session_id:
                return None
            if self.get_session(session_id, tenant_id, scope=scope) is None:
                return None
            return self.update_artifact(artifact_id, tenant_id, content)
        row = await self._repository.update_session_artifact(
            artifact_id, session_id, tenant_id, content.encode("utf-8"), scope=scope
        )
        return self._artifact_from_row(row) if row is not None else None

    async def adelete_artifact(
        self,
        artifact_id: str,
        session_id: str,
        tenant_id: str,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> bool:
        if self._repository is None:
            a = self._artifacts.get(artifact_id)
            if a is None or a.session_id != session_id:
                return False
            if self.get_session(session_id, tenant_id, scope=scope) is None:
                return False
            return self.delete_artifact(artifact_id, tenant_id)
        return bool(
            await self._repository.delete_session_artifact(
                artifact_id, session_id, tenant_id, scope=scope
            )
        )

    # ── Search ────────────────────────────────────────────────────────────────

    def search_messages(
        self,
        tenant_id: str,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> list[_Message]:
        """Simple substring search — production uses Postgres FTS index."""
        q = query.lower()
        results = [
            m
            for m in self._messages.values()
            if m.tenant_id == tenant_id
            and (session_id is None or m.session_id == session_id)
            and q in m.content.lower()
            and self.get_session(m.session_id, tenant_id, scope=scope) is not None
        ]
        return results[:limit]

    async def asearch_messages(
        self,
        tenant_id: str,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
        *,
        scope: ChatScope = SYSTEM_SCOPE,
    ) -> list[_Message]:
        """Durable search (ORG-32): Postgres full-text search when a repository is
        attached -- the in-memory dict is empty in DB mode."""
        if self._repository is None:
            return self.search_messages(
                tenant_id, query, session_id=session_id, limit=limit, scope=scope
            )
        rows = await self._repository.search_messages(
            tenant_id, query, scope=scope, session_id=session_id, limit=limit
        )
        return [self._message_from_row(r) for r in rows]

    # ── Conversation summary ──────────────────────────────────────────────────

    async def asummarize_session(
        self, session_id: str, tenant_id: str, *, scope: ChatScope = SYSTEM_SCOPE
    ) -> str:
        """Durable summary (ORG-32): topics from the persisted, bounded recent
        history; the message count is the whole session's, not the window's."""
        if self._repository is None:
            if self.get_session(session_id, tenant_id, scope=scope) is None:
                return self._summary_of([])
            return self.summarize_session(session_id, tenant_id)
        msgs = await self.alist_messages(session_id, tenant_id, limit=500, scope=scope)
        total = await self._repository.count_messages(session_id, tenant_id, scope=scope)
        return self._summary_of(msgs, total=int(total))

    def summarize_session(self, session_id: str, tenant_id: str) -> str:
        """Return a brief summary of the session conversation."""
        return self._summary_of(self.list_messages(session_id, tenant_id))

    @staticmethod
    def _summary_of(msgs: list[_Message], *, total: int | None = None) -> str:
        if not msgs:
            return "Empty session."
        topics: list[str] = []
        for m in msgs:
            if m.role == "user" and len(m.content) > 10:
                topics.append(m.content[:80])
        if not topics:
            return "No user messages yet."
        count = len(msgs) if total is None else max(total, len(msgs))
        summary = f"This session covered {count} messages. Topics: " + "; ".join(topics[:3])
        if len(topics) > 3:
            summary += f" and {len(topics) - 3} more."
        return summary
