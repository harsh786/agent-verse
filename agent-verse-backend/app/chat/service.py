"""ChatService — session CRUD, message dispatch, and streaming.

This is the core orchestrator that:
1. Creates / retrieves sessions
2. Saves messages to DB
3. Classifies intent and routes to QA stream or goal execution
4. Tracks per-message token usage
"""

from __future__ import annotations

import contextlib
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
    ) -> None:
        self._sessions: dict[str, _Session] = {}
        self._messages: dict[str, _Message] = {}
        self._folders: dict[str, _Folder] = {}
        self._artifacts: dict[str, _Artifact] = {}
        self._usage: dict[str, _Usage] = {}
        self._router = IntentRouter()
        self._ctx = ConversationContext()
        # clarify round tracking per session
        self._clarify_rounds: dict[str, int] = {}
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

        session = self.get_session(session_id, tenant_id)
        # Fetch a generous window so long sessions trigger summarization (the
        # default list_messages limit is small); ConversationContext then windows
        # and compresses it down to what actually enters the prompt.
        history = [
            {"role": m.role, "content": m.content}
            for m in self.list_messages(session_id, tenant_id, limit=1000)
        ]
        system_prompt = session.system_prompt if session else None
        if len(history) > self._ctx.COMPRESS_THRESHOLD:
            # Long session: summarize the older portion with a real LLM call and
            # keep only the recent window verbatim (replaces the static placeholder).
            old = history[: -self._ctx.MAX_TURNS]
            recent = history[-self._ctx.MAX_TURNS :]
            summary = await self._summarize_history(old)
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
        self.save_message(
            session_id=session_id, tenant_id=tenant_id, role="assistant", content=answer
        )
        yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)

    async def _summarize_history(self, messages: list[dict[str, Any]]) -> str:
        """LLM-summarize an older slice of a long conversation (Phase 1)."""
        if not messages or self._answer_generator is None:
            return ""
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
