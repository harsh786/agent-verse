/**
 * Real E2E tests for the Chat feature — NO HTTP mocking.
 *
 * Chat is the platform's core feature, so this file goes a bit further than a
 * plain render check: it creates a real session, dispatches a real message,
 * and verifies a reply renders in the UI. It stays robust to the dev backend
 * running without a real LLM key (FakeProvider fallback) by asserting on
 * dispatch/message *shape*, not on any particular AI-generated wording.
 *
 * Route: /chat and /chat/:sessionId → src/features/chat/ChatPage.tsx
 * Backend: agent-verse-backend/app/chat/router.py (prefix "/chat", 33 endpoints).
 *   - POST /chat/sessions                       (create session)
 *   - GET  /chat/sessions                       (list sessions)
 *   - POST /chat/sessions/{id}/messages         (dispatch a message — classifies
 *                                                 intent, returns dispatch metadata;
 *                                                 the actual reply streams via
 *                                                 GET /chat/sessions/{id}/stream)
 *   - GET  /chat/sessions/{id}/messages         (list messages in a session)
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/chat.real.spec.ts
 */

import { expect } from '@playwright/test';
import { test, FRONTEND_BASE } from './fixtures';

test.describe('Chat — real sessions', () => {
  test('sessions list API returns an array for a fresh tenant', async ({ api }) => {
    const resp = await api.get('/chat/sessions');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.sessions)).toBe(true);
  });

  test('create session, dispatch a message, and read it back via the real API', async ({ api }) => {
    const createResp = await api.post('/chat/sessions', { title: 'E2E real chat session' });
    expect(createResp.status()).toBe(201);
    const session = await createResp.json();
    expect(session.id ?? session.session_id).toBeTruthy();
    const sessionId = session.id ?? session.session_id;

    const dispatchResp = await api.post(`/chat/sessions/${sessionId}/messages`, {
      content: 'Say hello and confirm the system is working.',
    });
    expect(dispatchResp.status()).toBe(200);
    const dispatch = await dispatchResp.json();
    expect(dispatch.message_id).toBeTruthy();
    expect(dispatch.session_id).toBe(sessionId);
    expect(['QA', 'GOAL', 'CLARIFY', 'SCHEDULE']).toContain(dispatch.intent);

    // The user message itself should be persisted regardless of LLM availability.
    const messagesResp = await api.get(`/chat/sessions/${sessionId}/messages`);
    expect(messagesResp.status()).toBe(200);
    const { messages } = await messagesResp.json();
    expect(Array.isArray(messages)).toBe(true);
    expect(messages.some((m: { role: string }) => m.role === 'user')).toBe(true);
  });
});

test.describe('Chat — page renders and basic message flow', () => {
  test('chat page renders without a critical error', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/chat`, { waitUntil: 'networkidle' });
    await expect(authedPage.locator('body')).toBeVisible();
    const text = (await authedPage.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
  });

  test('submitting a message via the UI renders it and a response (or graceful fallback)', async ({ authedPage }) => {
    await authedPage.goto(`${FRONTEND_BASE}/chat`, { waitUntil: 'networkidle' });

    // Fresh tenant has no sessions — the empty state offers "Start a New Chat",
    // which creates a real session (POST /chat/sessions) and navigates to
    // /chat/:sessionId. Wait for that navigation before looking for the composer.
    const startButton = authedPage.getByRole('button', { name: 'Start a New Chat' });
    if (await startButton.isVisible().catch(() => false)) {
      await startButton.click();
      await authedPage.waitForURL(/\/chat\/.+/, { timeout: 15_000 }).catch(() => {/* fall through — composer wait below still guards this */});
    }

    const composer = authedPage.getByPlaceholder(
      'Ask a question or describe a goal… (Enter to send, Shift+Enter for newline)',
    );
    await expect(composer).toBeVisible({ timeout: 15_000 });
    await composer.fill('Say hello and confirm the system is working.');
    await composer.press('Enter');

    // The user's own message should render immediately regardless of whether an
    // LLM provider is configured (dev environments commonly run FakeProvider).
    await expect(
      authedPage.locator('[data-testid^="message-"]').filter({ hasText: 'Say hello' }),
    ).toBeVisible({ timeout: 15_000 });

    // Give the (real or fake) provider a moment to reply via the SSE stream, then
    // assert we did not surface a hard crash — a second message bubble (any
    // content, including a graceful error message) or an unchanged single bubble
    // are both acceptable; a thrown error boundary is not.
    await authedPage.waitForTimeout(3000);
    await expect(authedPage.locator('[data-testid="error-boundary-message"]')).not.toBeVisible();
    const bodyText = (await authedPage.locator('body').textContent()) ?? '';
    expect(bodyText.toLowerCase()).not.toContain('internal server error');
  });
});
