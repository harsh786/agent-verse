"""ConversationContext — builds LLM context from chat history.

Handles:
- Last 20 turns for Q&A
- Compressed summary for goal dispatch (max 500 tokens)
- Long-session summarization when > 100 messages
- Long-term memory injection (top-3)
- System prompt injection
- File context injection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    role: str  # "user" | "assistant" | "system"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationContext:
    """Build LLM message lists from stored chat history."""

    MAX_TURNS = 20
    COMPRESS_THRESHOLD = 100  # compress when message count exceeds this

    def build_for_qa(
        self,
        messages: list[dict[str, Any]],
        session_system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """Return last MAX_TURNS messages as OpenAI-style message list."""
        turns: list[dict[str, str]] = []

        if session_system_prompt:
            turns.append({"role": "system", "content": session_system_prompt})

        recent = messages[-self.MAX_TURNS :]
        for m in recent:
            turns.append({"role": m["role"], "content": m["content"]})

        return turns

    def build_for_goal(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 500,
    ) -> str:
        """Return compact context string for goal dispatch (max_tokens chars ~= tokens)."""
        recent = messages[-self.MAX_TURNS :]
        parts: list[str] = []
        budget = max_tokens * 4  # rough char estimate

        for m in reversed(recent):
            chunk = f"[{m['role'].upper()}] {m['content'][:400]}"
            if len(chunk) > budget:
                break
            parts.append(chunk)
            budget -= len(chunk)

        parts.reverse()
        return "\n".join(parts)

    def compress_long_session(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Summarize oldest messages when session exceeds COMPRESS_THRESHOLD."""
        if len(messages) <= self.COMPRESS_THRESHOLD:
            return messages

        old = messages[: -self.MAX_TURNS]
        recent = messages[-self.MAX_TURNS :]

        summary_text = (
            f"[Earlier conversation summary: {len(old)} messages exchanged. "
            "Topics covered include prior Q&A, agent goals, and clarifications.]"
        )
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": summary_text,
            "metadata": {"compressed": True, "original_count": len(old)},
        }
        return [summary_msg, *recent]

    def inject_long_term_memory(
        self,
        memories: list[str],
        turns: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Prepend top memories as a system turn."""
        if not memories:
            return turns
        memory_text = "Relevant long-term memories:\n" + "\n".join(f"- {m}" for m in memories[:3])
        return [{"role": "system", "content": memory_text}, *turns]

    def inject_system_prompt(
        self,
        system_prompt: str,
        turns: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Prepend session system_prompt before all other turns."""
        return [{"role": "system", "content": system_prompt}, *turns]

    def inject_personalization(
        self,
        personalization_block: str,
        turns: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Prepend the principal's personalization (tone + standing instructions +
        preferences) as a high-priority system turn (Phase 11).

        Placed first so the model treats standing instructions as governing the
        whole reply, ahead of memories/history.
        """
        if not personalization_block.strip():
            return turns
        return [{"role": "system", "content": personalization_block}, *turns]

    def inject_file_context(
        self,
        file_contents: list[str],
        turns: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Prepend uploaded file content as a system turn."""
        if not file_contents:
            return turns
        files_text = "Uploaded file context:\n" + "\n---\n".join(file_contents)
        return [{"role": "system", "content": files_text}, *turns]

    def inject_workspace_rag(
        self,
        snippets: list[str],
        turns: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Inject top-5 codebase snippets as a system turn."""
        if not snippets:
            return turns
        rag_text = "Relevant codebase context:\n" + "\n---\n".join(snippets[:5])
        return [{"role": "system", "content": rag_text}, *turns]
