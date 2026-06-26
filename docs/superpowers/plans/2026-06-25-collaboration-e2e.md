# Collaboration E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement DB-backed, goal-linked, human-agent and agent-agent collaboration sessions with persisted operations, authenticated WebSockets, consensus rounds, frontend session UX, and E2E tests.

**Architecture:** Replace module-level collaboration dictionaries with a `CollaborationStore` backed by PostgreSQL tables that already exist (`collab_sessions`, `collab_operations`) and keep WebSocket connections as ephemeral fanout only. REST APIs create/list/get/history/close sessions, WebSockets persist operations and broadcast sanitized events, and consensus endpoints use the existing `AgentCollabSession` round model.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL RLS, WebSocket, React/Vite, TanStack Query, Zustand, Vitest, Playwright.

---

## Task 1: Backend Collaboration Store

**Files:**
- Create: `agent-verse-backend/app/collab/store.py`
- Modify: `agent-verse-backend/app/db/models/intelligence.py`
- Test: `agent-verse-backend/tests/collab/test_collab_store.py`

## Task 2: REST API Persistence And Tenant Safety

**Files:**
- Modify: `agent-verse-backend/app/api/collab.py`
- Modify: `agent-verse-backend/app/main.py`
- Test: `agent-verse-backend/tests/api/test_collab.py`

## Task 3: Authenticated WebSocket Operation Persistence

**Files:**
- Modify: `agent-verse-backend/app/api/collab.py`
- Test: `agent-verse-backend/tests/api/test_collab.py`

## Task 4: Consensus Rounds And Goal Linkage

**Files:**
- Modify: `agent-verse-backend/app/api/collab.py`
- Test: `agent-verse-backend/tests/api/test_collab.py`

## Task 5: Frontend Collaboration UX

**Files:**
- Modify: `agent-verse-frontend/src/features/collaboration/CollaborationPage.tsx`
- Modify: `agent-verse-frontend/src/lib/ws/useCollabSocket.ts`
- Test: `agent-verse-frontend/src/features/collaboration/CollaborationPage.test.tsx`

## Task 6: E2E Tests

**Files:**
- Create: `agent-verse-frontend/e2e/collaboration.spec.ts`
- Test backend and frontend suites.

## Acceptance Criteria

- Sessions are tenant-scoped and persisted.
- Operations are persisted with version order.
- WebSocket messages are authenticated and broadcast.
- Session history survives API process memory loss.
- Consensus rounds produce agreed/disagreed result.
- Frontend can create, join, chat, edit draft content, and view history.
- Tests pass: backend collab tests, frontend unit/typecheck, Playwright E2E.
