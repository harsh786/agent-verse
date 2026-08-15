"""ChatService — session CRUD, message dispatch, and streaming.

This is the core orchestrator that:
1. Creates / retrieves sessions
2. Saves messages to DB
3. Classifies intent and routes to QA stream or goal execution
4. Tracks per-message token usage
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.chat.context import ConversationContext
from app.chat.intent import Intent, IntentRouter


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

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._messages: dict[str, _Message] = {}
        self._folders: dict[str, _Folder] = {}
        self._artifacts: dict[str, _Artifact] = {}
        self._usage: dict[str, _Usage] = {}
        self._router = IntentRouter()
        self._ctx = ConversationContext()
        # clarify round tracking per session
        self._clarify_rounds: dict[str, int] = {}

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
            m for m in self._messages.values()
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
            u for u in self._usage.values()
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
            a for a in self._artifacts.values()
            if a.session_id == session_id and a.tenant_id == tenant_id
        ]

    def update_artifact(
        self, artifact_id: str, tenant_id: str, content: str
    ) -> _Artifact | None:
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
            m for m in self._messages.values()
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
