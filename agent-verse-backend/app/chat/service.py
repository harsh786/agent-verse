"""ChatService — session CRUD, message dispatch, and streaming.

This is the core orchestrator that:
1. Creates / retrieves sessions
2. Saves messages to DB
3. Classifies intent and routes to QA stream or goal execution
4. Tracks per-message token usage
"""

from __future__ import annotations

import contextlib
import hashlib as _hashlib
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.chat.context import ConversationContext
from app.chat.intent import Intent, IntentRouter

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
    return {
        "session_id": str(session_id),
        "message_id": str(execution_context.get("message_id", "")),
    }


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
        # clarify round tracking per session
        self._clarify_rounds: dict[str, int] = {}
        # Phase 3: (tenant, channel, channel_user_id) -> session_id, so a channel
        # user's messages continue one conversation across turns/channels.
        self._channel_sessions: dict[tuple[str, str, str], str] = {}
        # Real-engine dependencies (injected in the lifespan; None on the pure
        # in-memory/unit-test path). ``goal_service`` drives GOAL turns through the
        # real AgentGraph; ``answer_generator`` produces real QA answers.
        self._goal_service = goal_service
        self._answer_generator = answer_generator
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
    ) -> _Session:
        sid = _hex()
        session = _Session(
            id=sid,
            tenant_id=tenant_id,
            title=title,
            system_prompt=system_prompt,
            agent_id=agent_id,
            folder_id=folder_id,
        )
        self._sessions[sid] = session
        return session

    def get_or_create_channel_session(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        title: str | None = None,
    ) -> _Session:
        """Resolve the durable session for a channel user (Phase 3).

        Returns the existing conversation for (channel, channel_user_id) so an
        inbound WhatsApp/Telegram message continues the same thread, creating one
        on first contact. This is what lets a conversation span channels/time.
        """
        key = (tenant_id, channel, channel_user_id)
        existing_id = self._channel_sessions.get(key)
        if existing_id is not None:
            existing = self.get_session(existing_id, tenant_id)
            if existing is not None:
                return existing
        session = self.create_session(tenant_id, title=title or f"{channel}:{channel_user_id}")
        self._channel_sessions[key] = session.id
        return session

    def get_session(self, session_id: str, tenant_id: str) -> _Session | None:
        s = self._sessions.get(session_id)
        if s and s.tenant_id == tenant_id:
            return s
        return None

    def list_sessions(self, tenant_id: str) -> list[_Session]:
        sessions = [s for s in self._sessions.values() if s.tenant_id == tenant_id]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def update_session(self, session_id: str, tenant_id: str, **kwargs: Any) -> _Session | None:
        s = self.get_session(session_id, tenant_id)
        if not s:
            return None
        for k, v in kwargs.items():
            if hasattr(s, k):
                setattr(s, k, v)
        s.updated_at = _now()
        return s

    def delete_session(self, session_id: str, tenant_id: str) -> bool:
        s = self.get_session(session_id, tenant_id)
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

    def list_messages(self, session_id: str, tenant_id: str, limit: int = 100) -> list[_Message]:
        msgs = [
            m
            for m in self._messages.values()
            if m.session_id == session_id and m.tenant_id == tenant_id
        ]
        return sorted(msgs, key=lambda m: m.created_at)[-limit:]

    def edit_message(
        self, message_id: str, tenant_id: str, new_content: str
    ) -> tuple[_Message | None, list[str]]:
        """Edit a user message and return (updated_message, pruned_message_ids).

        All messages after this one in the same branch are pruned.
        """
        msg = self._messages.get(message_id)
        if not msg or msg.tenant_id != tenant_id or msg.role != "user":
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

    def delete_message(self, message_id: str, tenant_id: str) -> bool:
        msg = self._messages.get(message_id)
        if not msg or msg.tenant_id != tenant_id:
            return False
        del self._messages[message_id]
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
    ) -> dict[str, Any]:
        """Async, repository-backed dispatch (persistence swap stage 3c).

        Mirrors ``dispatch`` but reads history and persists the user message via
        the async a* methods, so a chat turn is durable. Intent is classified
        before the save, so the stored message carries it.
        """
        if await self.aget_session(session_id, tenant_id) is None:
            raise ValueError(f"Session {session_id} not found")

        history = [
            {"role": m.role, "content": m.content}
            for m in await self.alist_messages(session_id, tenant_id, limit=1000)
        ]
        clarify_round = self._clarify_rounds.get(session_id, 0)
        intent = self._router.classify(
            user_message, history=history, clarify_round=clarify_round
        )
        user_msg = await self.asave_message(
            session_id=session_id, tenant_id=tenant_id, role="user",
            content=user_message, intent=intent.value,
        )
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
            self._clarify_rounds.pop(session_id, None)
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
        specs = await self._nl_scheduler.parse(message)
        ids: list[str] = []
        for spec in specs:
            schedule_id = await self._schedule_store.create_async(
                goal_id=message,
                spec=spec,
                tenant_ctx=tenant_ctx,
                agent_id=agent_id,
                goal_template=message,
            )
            ids.append(str(schedule_id))
        return ids

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
        return await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="assistant",
            content=content,
            goal_id=goal_id,
            metadata={"delivery": "async"},
        )

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
        )

    async def acreate_session(
        self,
        tenant_id: str,
        title: str = "New Chat",
        system_prompt: str | None = None,
        agent_id: str | None = None,
        folder_id: str | None = None,
    ) -> _Session:
        if self._repository is None:
            return self.create_session(
                tenant_id, title=title, system_prompt=system_prompt,
                agent_id=agent_id, folder_id=folder_id,
            )
        sid = _hex()
        await self._repository.create_session(
            session_id=sid, tenant_id=tenant_id, title=title,
            system_prompt=system_prompt, agent_id=agent_id, folder_id=folder_id,
        )
        row = await self._repository.get_session(sid, tenant_id)
        return self._session_from_row(row) if row else _Session(
            id=sid, tenant_id=tenant_id, title=title, system_prompt=system_prompt,
            agent_id=agent_id, folder_id=folder_id,
        )

    async def aget_session(self, session_id: str, tenant_id: str) -> _Session | None:
        if self._repository is None:
            return self.get_session(session_id, tenant_id)
        row = await self._repository.get_session(session_id, tenant_id)
        return self._session_from_row(row) if row else None

    async def alist_sessions(self, tenant_id: str) -> list[_Session]:
        if self._repository is None:
            return self.list_sessions(tenant_id)
        rows = await self._repository.list_sessions(tenant_id)
        return [self._session_from_row(r) for r in rows]

    async def aupdate_session(
        self, session_id: str, tenant_id: str, **kwargs: Any
    ) -> _Session | None:
        if self._repository is None:
            return self.update_session(session_id, tenant_id, **kwargs)
        await self._repository.update_session(session_id, tenant_id, **kwargs)
        return await self.aget_session(session_id, tenant_id)

    async def adelete_session(self, session_id: str, tenant_id: str) -> bool:
        if self._repository is None:
            return self.delete_session(session_id, tenant_id)
        return bool(await self._repository.delete_session(session_id, tenant_id))

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
    ) -> _Message:
        if self._repository is None:
            return self.save_message(
                session_id=session_id, tenant_id=tenant_id, role=role,
                content=content, goal_id=goal_id, intent=intent, metadata=metadata,
            )
        message_id = _hex()
        await self._repository.save_message(
            message_id=message_id, session_id=session_id, tenant_id=tenant_id,
            role=role, content=content, intent=intent, goal_id=goal_id, metadata=metadata,
        )
        return _Message(
            id=message_id, session_id=session_id, tenant_id=tenant_id, role=role,
            content=content, goal_id=goal_id, intent=intent, metadata=metadata or {},
        )

    async def alist_messages(
        self, session_id: str, tenant_id: str, limit: int = 100
    ) -> list[_Message]:
        if self._repository is None:
            return self.list_messages(session_id, tenant_id, limit=limit)
        rows = await self._repository.list_messages(session_id, tenant_id)
        msgs = [self._message_from_row(r) for r in rows]
        return msgs[-limit:] if limit else msgs

    async def aedit_message(
        self, message_id: str, tenant_id: str, new_content: str
    ) -> tuple[_Message | None, list[str]]:
        """Durable edit of a user message returning (updated_message, pruned_ids).

        All messages created after the edited one in the same session are pruned
        (branch pruning). Falls back to the in-memory path when no repository is
        wired.
        """
        if self._repository is None:
            return self.edit_message(message_id, tenant_id, new_content)
        row = await self._repository.get_message(message_id, tenant_id)
        if row is None or str(row.get("role")) != "user":
            return None, []
        await self._repository.update_message_content(message_id, tenant_id, new_content)
        pruned = await self._repository.delete_messages_after(
            str(row["session_id"]), tenant_id, row["created_at"]
        )
        row["content"] = new_content
        return self._message_from_row(row), pruned

    async def attach_file(
        self,
        *,
        session_id: str,
        tenant_id: str,
        content_bytes: bytes,
        filename: str = "document",
    ) -> _Message | None:
        """Parse an uploaded file and store it as conversation context (Phase 4).

        The extracted text is saved as a user message so it's naturally included
        in the next turn's history (no run_qa/run_goal change needed). Returns None
        if the session doesn't exist.
        """
        if await self.aget_session(session_id, tenant_id) is None:
            return None
        from app.chat.attachments import format_attachment_context, parse_attachment

        parsed = await parse_attachment(content_bytes, filename)
        context = format_attachment_context(parsed)
        return await self.asave_message(
            session_id=session_id,
            tenant_id=tenant_id,
            role="user",
            content=context,
            metadata={"attachment": filename, "kind": "attachment"},
        )

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
        completion can be delivered back into this thread (Phase 2)."""
        if self._goal_service is None:
            raise RuntimeError("chat GOAL execution requires a GoalService to be wired")
        session = self.get_session(session_id, tenant_id)
        result = await self._goal_service.submit_goal(
            goal=user_message,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id or (session.agent_id if session else None),
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
            summary = await self._summarize_history(old, session_id=session_id)
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
        request = CompletionRequest(
            messages=[Message(role=t["role"], content=t["content"]) for t in turns],
            model="",
        )

        yield sse_event(ChatEventType.MESSAGE_STARTED, session_id=session_id, message_id=message_id)
        parts: list[str] = []
        streamer = getattr(self._answer_generator, "stream_complete", None)
        if callable(streamer):
            async for chunk in streamer(request):
                parts.append(chunk)
                yield sse_event(ChatEventType.TOKEN, token=chunk, message_id=message_id)
        else:
            # Provider without a streaming API — one-shot complete().
            resp = await self._answer_generator.complete(request)
            text = getattr(resp, "content", "") or ""
            parts.append(text)
            yield sse_event(ChatEventType.TOKEN, token=text, message_id=message_id)
        answer = "".join(parts).strip()
        await self.asave_message(
            session_id=session_id, tenant_id=tenant_id, role="assistant", content=answer
        )
        yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)

    async def _summarize_history(
        self, messages: list[dict[str, Any]], *, session_id: str | None = None
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
                    merged = await self._merge_summary(prev_summary, messages[prev_count:])
                    if merged:
                        self._rolling_summary[session_id] = (len(messages), merged)
                    return merged or prev_summary
            summary = await self._llm_summarize(messages)
            if summary:
                self._rolling_summary[session_id] = (len(messages), summary)
            return summary

        # One-shot (whole-session distillation): content-hash cache.
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        cache_key = _hashlib.sha256(convo.encode("utf-8")).hexdigest()
        cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached
        summary = await self._llm_summarize(messages)
        if summary:
            if len(self._summary_cache) >= 512:
                self._summary_cache.pop(next(iter(self._summary_cache)), None)
            self._summary_cache[cache_key] = summary
        return summary

    async def _llm_summarize(self, messages: list[dict[str, Any]]) -> str:
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
            resp = await self._answer_generator.complete(request)
            return (getattr(resp, "content", "") or "").strip()
        except Exception:
            return ""

    async def _merge_summary(self, prior: str, new_messages: list[dict[str, Any]]) -> str:
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
            resp = await self._answer_generator.complete(request)
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
        summary = await self._summarize_history(history)
        if not summary:
            return 0
        with contextlib.suppress(Exception):
            await self._memory_writer(summary, tenant_id)
            return 1
        return 0

    def handle_channel_message(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Unified entry point for an inbound channel message (Phase 3).

        Resolves the channel user's durable session and runs the SAME dispatch
        (save + intent classification + history) as web chat, so WhatsApp/Telegram/
        API and the web UI share one pipeline. Returns the dispatch metadata plus
        the resolved ``session_id`` and ``channel``.

        NOTE (persistence): this path is still the sync/in-memory pipeline and is
        currently dormant (no inbound webhook wires it). Before exposing it on a
        live endpoint it MUST move to the async ``adispatch`` path AND gain a
        durable channel->session mapping (the in-memory ``_channel_sessions`` map
        does not survive restarts), otherwise channel writes would diverge from
        the Postgres-backed web/API reads.
        """
        session = self.get_or_create_channel_session(
            tenant_id=tenant_id, channel=channel, channel_user_id=channel_user_id
        )
        result = self.dispatch(session.id, tenant_id, text)
        result["channel"] = channel
        return result

    # ── Folder CRUD ───────────────────────────────────────────────────────────

    def create_folder(self, tenant_id: str, name: str, color: str = "#6366f1") -> _Folder:
        f = _Folder(id=_hex(), tenant_id=tenant_id, name=name, color=color)
        self._folders[f.id] = f
        return f

    def list_folders(self, tenant_id: str) -> list[_Folder]:
        return [f for f in self._folders.values() if f.tenant_id == tenant_id]

    def delete_folder(self, folder_id: str, tenant_id: str) -> bool:
        f = self._folders.get(folder_id)
        if not f or f.tenant_id != tenant_id:
            return False
        del self._folders[folder_id]
        # Unassign sessions in this folder
        for s in self._sessions.values():
            if s.folder_id == folder_id:
                s.folder_id = None
        return True

    def move_session_to_folder(
        self, session_id: str, tenant_id: str, folder_id: str | None
    ) -> _Session | None:
        return self.update_session(session_id, tenant_id, folder_id=folder_id)

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

    # ── Search ────────────────────────────────────────────────────────────────

    def search_messages(
        self,
        tenant_id: str,
        query: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[_Message]:
        """Simple substring search — production uses Postgres FTS index."""
        q = query.lower()
        results = [
            m
            for m in self._messages.values()
            if m.tenant_id == tenant_id
            and (session_id is None or m.session_id == session_id)
            and q in m.content.lower()
        ]
        return results[:limit]

    # ── Conversation summary ──────────────────────────────────────────────────

    def summarize_session(self, session_id: str, tenant_id: str) -> str:
        """Return a brief summary of the session conversation."""
        msgs = self.list_messages(session_id, tenant_id)
        if not msgs:
            return "Empty session."
        topics: list[str] = []
        for m in msgs:
            if m.role == "user" and len(m.content) > 10:
                topics.append(m.content[:80])
        if not topics:
            return "No user messages yet."
        summary = f"This session covered {len(msgs)} messages. Topics: " + "; ".join(topics[:3])
        if len(topics) > 3:
            summary += f" and {len(topics) - 3} more."
        return summary
