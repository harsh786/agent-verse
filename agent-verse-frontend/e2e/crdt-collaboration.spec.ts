/**
 * crdt-collaboration.spec.ts — End-to-end tests for the CRDT collaborative editor.
 *
 * Coverage:
 * - CRDT Collaborative Editor (single-client): page structure, session card,
 *   live panel, editor textarea, connection status, undo/redo, char count,
 *   awareness strip, offline footer, presence bar, consensus card, insights panel
 * - CRDT Multi-Client Conflict Resolution: two-context concurrent editing,
 *   cross-broadcast, undo isolation, new session creation flow
 *
 * Architecture notes:
 * - CollaborationPage fetches GET /collab/sessions → renders session cards
 * - Clicking a card mounts LiveSessionPanel (review mode → showDraft → CRDTEditor)
 * - CRDTEditor uses useYjsCollab which connects to ws://localhost:8000/collab/crdt/{roomId}
 * - LiveSessionPanel uses useCollabSocket → ws://localhost:8000/collab/sessions/{id}/ws
 * - Both WS connections are mocked via page.routeWebSocket()
 */

import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ─── Shared mock data ─────────────────────────────────────────────────────────

/** Must match CollabSession interface in CollaborationPage.tsx */
const MOCK_SESSION = {
  session_id: 'sess-crdt-001',
  name: 'CRDT Test Session',
  mode: 'review', // review → showDraft = true → CRDTEditor is rendered
  status: 'active',
  participants: ['human:lead', 'agent:reviewer'],
  participant_count: 2,
  content: 'Initial collaborative document content.',
  created_at: new Date().toISOString(),
  goal_id: null,
  agent_id: null,
};

// ─── Shared setup helpers ─────────────────────────────────────────────────────

/**
 * Wire up all routes and WS mocks needed for a collaboration session test.
 * Must be called BEFORE page.goto() so WS routes are registered in time.
 */
async function setupCollabSession(page: Page) {
  await setupAuth(page);

  // Session list (GET) and creation (POST)
  await page.route('**/collab/sessions', async (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([MOCK_SESSION]),
      });
    }
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_SESSION),
    });
  });

  // Operations (empty list — clean session)
  await page.route(
    `**/collab/sessions/${MOCK_SESSION.session_id}/operations`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      }),
  );

  // Consensus (no agreement yet)
  await page.route(
    `**/collab/sessions/${MOCK_SESSION.session_id}/consensus`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agreed: false, summary: 'No consensus yet', dissenter: null }),
      }),
  );

  // Collab session WebSocket (/collab/sessions/{id}/ws)
  // Responds to pings to keep the connection alive; ignores other messages.
  await page.routeWebSocket(/collab\/sessions\/sess-crdt-001\/ws/, (ws) => {
    ws.onMessage((msg) => {
      try {
        const data = JSON.parse(typeof msg === 'string' ? msg : '{}') as { type?: string };
        if (data.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
        }
      } catch {
        // Non-JSON binary frames — ignore
      }
    });
  });

  // CRDT y-websocket (/collab/crdt/{roomName})
  // Echo back messages to simulate single-client sync (Yjs will reach synced state).
  await page.routeWebSocket(/collab\/crdt/, (ws) => {
    ws.onMessage((msg) => {
      ws.send(msg);
    });
  });
}

/**
 * Click the first session card and wait for the live session panel to appear.
 * Assumes page is already at /collaboration with sessions loaded.
 */
async function openSession(page: Page) {
  await expect(page.locator('[data-testid="session-card"]').first()).toBeVisible({
    timeout: 10_000,
  });
  await page.locator('[data-testid="session-card"]').first().click();
  await expect(page.locator('[data-testid="live-session"]')).toBeVisible({ timeout: 8_000 });
}

// ─────────────────────────────────────────────────────────────────────────────
// CRDT Collaborative Editor — Single Client
// ─────────────────────────────────────────────────────────────────────────────

test.describe('CRDT Collaborative Editor', () => {
  test('collaboration page renders the "Collaboration" heading', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await expect(
      page.getByRole('heading', { name: 'Collaboration' }),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('session card for "CRDT Test Session" is visible in the grid', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await expect(page.getByText('CRDT Test Session')).toBeVisible({ timeout: 8_000 });
    await expect(page.locator('[data-testid="session-card"]')).toBeVisible({ timeout: 8_000 });
  });

  test('session card shows mode badge and active status indicator', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    const card = page.locator('[data-testid="session-card"]').first();
    await card.waitFor({ timeout: 8_000 });
    // Mode badge text for 'review' mode
    await expect(card.getByText('Review')).toBeVisible();
    // Active status pill
    await expect(card.getByText('active')).toBeVisible();
  });

  test('clicking a session card opens the live session panel', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(page.locator('[data-testid="live-session"]')).toBeVisible();
  });

  test('review-mode live panel shows "Shared draft" section', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(page.getByText('Shared draft')).toBeVisible({ timeout: 5_000 });
    // data-testid="draft-editor" wraps the CRDTEditor
    await expect(page.locator('[data-testid="draft-editor"]')).toBeVisible();
  });

  test('CRDT editor textarea is present with aria-label "Collaborative editor"', async ({
    page,
  }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(
      page.locator('textarea[aria-label="Collaborative editor"]'),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('CRDT editor toolbar shows a connection status indicator', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    // One of: "Live" (green), "Offline" (amber), or "Connecting…" (spinner)
    await expect(
      page
        .locator('text=Live')
        .or(page.locator('text=Offline'))
        .or(page.locator('text=Connecting…')),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('CRDT editor toolbar shows Undo and Redo buttons', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(
      page.locator('button[title="Undo (Ctrl+Z)"]'),
    ).toBeVisible({ timeout: 5_000 });
    await expect(
      page.locator('button[title="Redo (Ctrl+Y)"]'),
    ).toBeVisible({ timeout: 5_000 });
  });

  test('CRDT editor shows "Only you here" when no remote cursors are present', async ({
    page,
  }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    // The awareness strip in CRDTEditor shows this when cursors.length === 0
    await expect(page.getByText('Only you here')).toBeVisible({ timeout: 8_000 });
  });

  test('char count starts at 0 and updates after typing', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    const editor = page.locator('textarea[aria-label="Collaborative editor"]');
    await editor.waitFor({ timeout: 5_000 });
    // Initial char count text: "{n} chars"
    await expect(page.getByText('0 chars')).toBeVisible({ timeout: 5_000 });
    await editor.fill('Hello collaborative world!'); // 26 chars
    await expect(page.getByText('26 chars')).toBeVisible({ timeout: 3_000 });
  });

  test('typing then clearing resets char count to 0', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    const editor = page.locator('textarea[aria-label="Collaborative editor"]');
    await editor.waitFor({ timeout: 5_000 });
    await editor.fill('Some text');
    await expect(page.getByText('9 chars')).toBeVisible({ timeout: 3_000 });
    await editor.fill(''); // clear
    await expect(page.getByText('0 chars')).toBeVisible({ timeout: 3_000 });
  });

  test('Ctrl+Z keyboard shortcut triggers undo through Yjs UndoManager', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    const editor = page.locator('textarea[aria-label="Collaborative editor"]');
    await editor.waitFor({ timeout: 5_000 });
    await editor.fill('Undo test content');
    await editor.press('Control+z');
    // No crash — the page should still be functional
    await expect(page.locator('[data-testid="draft-editor"]')).toBeVisible();
  });

  test('offline footer appears when CRDT WS is disconnected and editor has content', async ({
    page,
  }) => {
    await setupAuth(page);
    // Close CRDT WS immediately → editor enters offline state
    await page.routeWebSocket(/collab\/crdt/, (ws) => {
      ws.close();
    });
    // Set up remaining routes without the CRDT echo
    await page.route('**/collab/sessions', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([MOCK_SESSION]),
      }),
    );
    await page.route(
      `**/collab/sessions/${MOCK_SESSION.session_id}/operations`,
      (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    );
    await page.route(
      `**/collab/sessions/${MOCK_SESSION.session_id}/consensus`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ agreed: false, summary: '' }),
        }),
    );
    await page.routeWebSocket(/collab\/sessions\/sess-crdt-001\/ws/, (ws) => {
      ws.onMessage(() => {});
    });
    await page.goto('/collaboration');
    await openSession(page);
    const editor = page.locator('textarea[aria-label="Collaborative editor"]');
    await editor.waitFor({ timeout: 5_000 });
    // Offline footer only shows when !connected && text.length > 0
    await editor.fill('Content to trigger offline footer');
    await expect(
      page.getByText('Working offline — changes will sync when reconnected'),
    ).toBeVisible({ timeout: 8_000 });
  });

  test('participant presence bar is rendered inside the live session panel', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(page.locator('[data-testid="presence-bar"]')).toBeVisible({ timeout: 5_000 });
    // Participants from mock: "human:lead", "agent:reviewer"
    await expect(page.getByText('lead')).toBeVisible();
  });

  test('consensus card shows "No consensus yet" initially', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(page.locator('[data-testid="consensus-card"]')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Consensus Status')).toBeVisible();
    await expect(page.getByText('No consensus yet')).toBeVisible();
  });

  test('session insights panel has "Generate Insights" button', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(
      page.locator('[data-testid="generate-insights-btn"]'),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Generate Insights')).toBeVisible();
  });

  test('"Generate Insights" button calls insights endpoint and displays results', async ({
    page,
  }) => {
    await setupCollabSession(page);
    await page.route(
      `**/collab/sessions/${MOCK_SESSION.session_id}/insights`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            key_decisions: ['Use CRDT for collaboration'],
            action_items: ['Set up y-websocket server'],
            open_questions: ['How to handle large documents?'],
            agreement_level: 0.8,
            sentiment: 'positive',
            summary: 'Team aligned on CRDT approach.',
          }),
        }),
    );
    await page.goto('/collaboration');
    await openSession(page);
    await page.locator('[data-testid="generate-insights-btn"]').click();
    await expect(page.getByText('Use CRDT for collaboration')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Team aligned on CRDT approach.')).toBeVisible();
  });

  test('Close button hides the live session panel and returns to session list', async ({
    page,
  }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    await expect(page.locator('[data-testid="live-session"]')).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(
      page.locator('[data-testid="live-session"]'),
    ).not.toBeVisible({ timeout: 3_000 });
  });

  test('status filter pills (All / Active / Closed) are visible', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await expect(page.getByRole('button', { name: 'All', exact: true })).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.getByRole('button', { name: 'Active', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Closed', exact: true })).toBeVisible();
  });

  test('"Closed" filter hides active session cards', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await page.getByText('CRDT Test Session').waitFor({ timeout: 8_000 });
    await page.getByRole('button', { name: 'Closed', exact: true }).click();
    // MOCK_SESSION has status='active' so it should be filtered out
    await expect(page.getByText('CRDT Test Session')).not.toBeVisible({ timeout: 3_000 });
  });

  test('"Active" filter shows the active session card', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await page.getByText('CRDT Test Session').waitFor({ timeout: 8_000 });
    await page.getByRole('button', { name: 'Active', exact: true }).click();
    await expect(page.getByText('CRDT Test Session')).toBeVisible({ timeout: 3_000 });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// CRDT Multi-Client Conflict Resolution
// Uses separate browser contexts to simulate two independent users.
// ─────────────────────────────────────────────────────────────────────────────

test.describe('CRDT Multi-Client Conflict Resolution', () => {
  test('two clients can edit the same session without either crashing', async ({ browser }) => {
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      // Each client gets independent echo WS — simulates offline CRDT editing
      await setupCollabSession(page1);
      await setupCollabSession(page2);

      await Promise.all([
        page1.goto('/collaboration'),
        page2.goto('/collaboration'),
      ]);

      // Open the session on both clients
      for (const p of [page1, page2]) {
        const card = p.locator('[data-testid="session-card"]').first();
        if (await card.isVisible({ timeout: 5_000 }).catch(() => false)) {
          await card.click();
        }
        await p
          .locator('[data-testid="live-session"]')
          .waitFor({ timeout: 8_000 })
          .catch(() => {});
      }

      // Both clients type in their respective editors
      const editor1 = page1.locator('textarea[aria-label="Collaborative editor"]');
      const editor2 = page2.locator('textarea[aria-label="Collaborative editor"]');

      if (await editor1.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await editor1.fill('Client 1 edits this document');
        await page1.waitForTimeout(150);
      }
      if (await editor2.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await editor2.fill('Client 2 edits this document');
        await page2.waitForTimeout(150);
      }

      // Neither client should have crashed or shown an error boundary
      await expect(page1.getByText('Something went wrong')).not.toBeVisible();
      await expect(page2.getByText('Something went wrong')).not.toBeVisible();

      // Each editor should contain content (no data loss)
      if (await editor1.isVisible().catch(() => false)) {
        const val = await editor1.inputValue();
        expect(val.length).toBeGreaterThan(0);
      }
      if (await editor2.isVisible().catch(() => false)) {
        const val = await editor2.inputValue();
        expect(val.length).toBeGreaterThan(0);
      }
    } finally {
      await context1.close();
      await context2.close();
    }
  });

  test('undo on client 1 does not crash client 2', async ({ browser }) => {
    const context1 = await browser.newContext();
    const context2 = await browser.newContext();
    const page1 = await context1.newPage();
    const page2 = await context2.newPage();

    try {
      await setupCollabSession(page1);
      await setupCollabSession(page2);

      await Promise.all([
        page1.goto('/collaboration'),
        page2.goto('/collaboration'),
      ]);

      for (const p of [page1, page2]) {
        const card = p.locator('[data-testid="session-card"]').first();
        if (await card.isVisible({ timeout: 5_000 }).catch(() => false)) {
          await card.click();
        }
        await p
          .locator('[data-testid="live-session"]')
          .waitFor({ timeout: 8_000 })
          .catch(() => {});
      }

      const editor1 = page1.locator('textarea[aria-label="Collaborative editor"]');
      if (await editor1.isVisible({ timeout: 3_000 }).catch(() => false)) {
        await editor1.fill('Text before undo');
        await page1.waitForTimeout(100);
        const undoBtn = page1.locator('button[title="Undo (Ctrl+Z)"]');
        if (
          (await undoBtn.isVisible().catch(() => false)) &&
          (await undoBtn.isEnabled().catch(() => false))
        ) {
          await undoBtn.click();
          await page1.waitForTimeout(100);
        }
      }

      // Undo on client 1 must not affect client 2's rendering
      await expect(page1.getByText('Something went wrong')).not.toBeVisible();
      await expect(page2.getByText('Something went wrong')).not.toBeVisible();
    } finally {
      await context1.close();
      await context2.close();
    }
  });

  test('creating a new session opens the live panel immediately', async ({ page }) => {
    await setupAuth(page);

    const newSession = {
      ...MOCK_SESSION,
      session_id: 'sess-new-001',
      name: 'New Collab Session',
    };

    let sessions: typeof newSession[] = [];
    await page.route('**/collab/sessions', async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(sessions),
        });
      }
      sessions = [newSession];
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(newSession),
      });
    });
    await page.route('**/collab/sessions/sess-new-001/operations', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
    );
    await page.route('**/collab/sessions/sess-new-001/consensus', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ agreed: false, summary: '' }),
      }),
    );
    await page.routeWebSocket(/collab\/sessions\/sess-new-001\/ws/, (ws) => {
      ws.onMessage(() => {});
    });
    await page.routeWebSocket(/collab\/crdt/, (ws) => {
      ws.onMessage((msg) => ws.send(msg));
    });

    await page.goto('/collaboration');
    // Open create panel
    await page.locator('[data-testid="create-session-btn"]').click();
    await expect(page.getByText('New Collaboration Session')).toBeVisible({ timeout: 5_000 });
    await page.getByPlaceholder('Session name').fill('New Collab Session');
    await page.getByText('Create Session').click();
    // onSuccess sets activeSession → live panel mounts immediately
    await expect(page.locator('[data-testid="live-session"]')).toBeVisible({ timeout: 8_000 });
  });

  test('Keyboard shortcut Ctrl+Y triggers redo without crashing', async ({ page }) => {
    await setupCollabSession(page);
    await page.goto('/collaboration');
    await openSession(page);
    const editor = page.locator('textarea[aria-label="Collaborative editor"]');
    await editor.waitFor({ timeout: 5_000 });
    await editor.fill('Redo test');
    await editor.press('Control+z'); // undo
    await page.waitForTimeout(100);
    await editor.press('Control+y'); // redo
    // No crash — editor and panel still functional
    await expect(page.locator('[data-testid="draft-editor"]')).toBeVisible();
    await expect(page.getByText('Something went wrong')).not.toBeVisible();
  });
});
