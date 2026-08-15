---
title: "Chat Interface — Deep Dives"
description: "Complete technical reference for AgentVerse's conversational interface: intent routing, session management, goal execution streaming, clarification flows, frontend UX, and API."
outline: deep
---

# Chat Interface — Deep Dives

This folder documents the `app/chat/` package — a GitHub Copilot / Claude-style conversational interface that routes messages to either direct LLM Q&A or the full agent goal execution engine.

| File | Topic |
|---|---|
| [01-architecture-and-intent-routing.md](01-architecture-and-intent-routing.md) | System architecture, intent classification (QA vs GOAL vs CLARIFY), request lifecycle |
| [02-session-management-and-persistence.md](02-session-management-and-persistence.md) | ChatSession/ChatMessage models, TTL, pinning, Postgres RLS, Redis pub/sub |
| [03-goal-execution-and-sse-streaming.md](03-goal-execution-and-sse-streaming.md) | SSE event schema (9 types), goal lifecycle in chat, stream multiplexing |
| [04-clarification-flows-and-hitl.md](04-clarification-flows-and-hitl.md) | Pre-execution clarification, mid-execution blocking questions, HITL approval gate |
| [05-frontend-components-and-ux.md](05-frontend-components-and-ux.md) | All React components, UX patterns, accessibility, responsive layout, keyboard shortcuts |
| [06-api-reference.md](06-api-reference.md) | All 7 REST endpoints — request/response schemas, auth, pagination, error codes |

## Summary

The chat interface is a **thin orchestration layer** on top of AgentVerse's existing engine:

1. Every message hits `POST /v1/chat/sessions/{id}/messages`
2. `IntentRouter` classifies it as `QA`, `GOAL`, or `CLARIFY` (~50-100ms LLM call)
3. **QA path**: last 20 turns + optional RAG context + LLM → token-by-token SSE stream
4. **GOAL path**: `ChatService` calls existing `GoalService.submit_goal()` unchanged, then bridges Redis pub/sub step events into the chat SSE stream
5. **CLARIFY path**: agent generates one focused question, waits for user answer, then resubmits as fully-specified goal
6. All agent patterns (LangGraph, MCP tools, RAG, Memory, HITL, cost control) work automatically — zero changes to the agent engine
