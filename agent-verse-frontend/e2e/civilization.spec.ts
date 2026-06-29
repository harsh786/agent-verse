/**
 * Playwright E2E tests for Agent Civilization UI.
 *
 * All backend calls are mocked via page.route() so these tests run
 * against the frontend dev server only (no backend required).
 *
 * Pattern mirrors goals.spec.ts: setupAuth() + per-test route mocks.
 */

import { test, expect, type Page } from '@playwright/test';

// ── Shared auth helper (same pattern as goals.spec.ts) ───────────────────────

async function setupAuth(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'enterprise',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });

  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_id: 'test-tenant',
        name: 'Test Org',
        plan: 'enterprise',
      }),
    })
  );
}

// ── Mock data ─────────────────────────────────────────────────────────────────

const CIV_ID = 'civ-test-1';

const CIVILIZATION = {
  id: CIV_ID,
  name: 'Test Civilization',
  status: 'active',
  constitution: {
    max_depth: 3,
    max_total_agents: 10,
    total_budget_usd: 50,
    per_agent_budget_usd: 10,
    budget_decay: 0.7,
    spawn_rate_limit_per_min: 30,
  },
  created_at: new Date().toISOString(),
};

const CIVILIZATION_DETAIL = {
  ...CIVILIZATION,
  metrics: {
    total_members: 3,
    active_members: 2,
    idle_members: 1,
    retired_members: 0,
    total_budget_spent_usd: 5.2,
    avg_reputation: 0.72,
           max_reputation: 1.0,
           min_reputation: 0.0,
  },
};

const GRAPH = {
  nodes: [
    {
      id: 'agent-root',
      label: 'coordinator',
      status: 'active',
      reputation: 0.8,
      depth: 0,
      budget_spent_usd: 1.2,
    },
    {
      id: 'agent-child-1',
      label: 'jira-worker',
      status: 'active',
      reputation: 0.7,
      depth: 1,
      budget_spent_usd: 0.8,
    },
    {
      id: 'agent-child-2',
      label: 'confluence-writer',
      status: 'idle',
      reputation: 0.6,
      depth: 1,
      budget_spent_usd: 0.5,
    },
  ],
  edges: [
    { source: 'agent-root', target: 'agent-child-1', type: 'spawn_lineage' },
    { source: 'agent-root', target: 'agent-child-2', type: 'spawn_lineage' },
  ],
  member_count: 3,
};

const BLACKBOARD_ENTRIES = [
  {
    id: 'bb-1',
    author_agent_id: 'agent-root',
    topic: 'jira_analysis',
    content: 'Found 15 P1 issues',
    confidence: 0.9,
    version: 1,
    created_at: new Date().toISOString(),
  },
];

const LEARNINGS = [
  {
    id: 'l-1',
    candidate: 'P1 issues resolved in <24h',
    source_agent_id: 'agent-1',
    status: 'promoted',
    eval_score: 0.85,
    promoted_memory_id: 'mem-1',
    created_at: new Date().toISOString(),
    decided_at: new Date().toISOString(),
  },
  {
    id: 'l-2',
    candidate: 'Bad pattern...',
    source_agent_id: 'agent-2',
    status: 'rejected',
    eval_score: 0.2,
    promoted_memory_id: null,
    created_at: new Date().toISOString(),
    decided_at: new Date().toISOString(),
  },
];

const SPAWNS = [
  {
    id: 's-1',
    requester_agent_id: 'agent-root',
    requested_capability: 'jira_search',
    decision: 'approved',
    reason: 'within limits',
    created_at: new Date().toISOString(),
  },
  {
    id: 's-2',
    requester_agent_id: 'agent-root',
    requested_capability: 'deploy_prod',
    decision: 'denied',
    reason: 'depth limit',
    created_at: new Date().toISOString(),
  },
];

// ── Route-setup helper ────────────────────────────────────────────────────────

async function setupCivilizationRoutes(page: Page): Promise<void> {
  // GET /civilizations → list
  // POST /civilizations → create
  await page.route(/\/civilizations$/, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([CIVILIZATION]),
      });
    } else {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'civ-new-1',
          name: 'New Civ',
          status: 'active',
          constitution: {},
        }),
      });
    }
  });

  // GET /civilizations/{id}
  await page.route(new RegExp(`/civilizations/${CIV_ID}$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CIVILIZATION_DETAIL),
    });
  });

  // GET /civilizations/{id}/graph
  await page.route(new RegExp(`/civilizations/${CIV_ID}/graph`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(GRAPH),
    });
  });

  // GET /civilizations/{id}/blackboard
  await page.route(new RegExp(`/civilizations/${CIV_ID}/blackboard`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(BLACKBOARD_ENTRIES),
    });
  });

  // GET /civilizations/{id}/learnings
  await page.route(new RegExp(`/civilizations/${CIV_ID}/learnings`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(LEARNINGS),
    });
  });

  // GET /civilizations/{id}/spawns
  await page.route(new RegExp(`/civilizations/${CIV_ID}/spawns`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(SPAWNS),
    });
  });

  // POST /civilizations/{id}/goals
  await page.route(new RegExp(`/civilizations/${CIV_ID}/goals`), async (route) => {
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'accepted',
        goal_id: 'goal-new-1',
        agent_id: 'agent-root',
      }),
    });
  });

  // POST /civilizations/{id}/controls/pause|resume
  await page.route(
    new RegExp(`/civilizations/${CIV_ID}/controls/(pause|resume)`),
    async (route) => {
      const action = route.request().url().includes('pause') ? 'paused' : 'active';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: action, civilization_id: CIV_ID }),
      });
    }
  );

  // SSE stream
  await page.route(new RegExp(`/civilizations/${CIV_ID}/stream`), async (route) => {
    const ts = new Date().toISOString();
    const stream =
      `data: {"type":"agent_spawned","payload":{"agent_id":"agent-new","depth":1},"ts":"${ts}"}\n\n` +
      `data: {"type":"blackboard_posted","payload":{"topic":"jira","content":"Found 15 issues"},"ts":"${ts}"}\n\n`;
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: stream,
    });
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Agent Civilization', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await setupCivilizationRoutes(page);
    await page.goto(`/civilization/${CIV_ID}`);
  });

  test('civilization page loads with name visible', async ({ page }) => {
    await expect(page.getByText('Test Civilization')).toBeVisible({ timeout: 5000 });
  });

  test('metrics panel shows active-agent count', async ({ page }) => {
    // The detail endpoint returns active_members: 2
    await expect(
      page.getByText(/active.*agent/i).first()
    ).toBeVisible({ timeout: 5000 });
  });

  test('society map renders the React Flow canvas', async ({ page }) => {
    // React Flow wraps its viewport in a div with the rf__ prefix
    await page.waitForTimeout(800);
    const canvas = page
      .locator('.react-flow, [data-testid="rf__wrapper"]')
      .first();
    await expect(canvas).toBeVisible({ timeout: 5000 });
  });

  test('submitting a goal sends POST to /goals endpoint', async ({ page }) => {
    let goalSubmitted = false;

    // Override with a capturing handler
    await page.route(new RegExp(`/civilizations/${CIV_ID}/goals`), async (route) => {
      goalSubmitted = true;
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', goal_id: 'g-new' }),
      });
    });

    const goalInput = page.getByPlaceholder(/submit a goal/i);
    await goalInput.fill('Analyze all Jira P1 issues');
    await page.keyboard.press('Enter');

    await page.waitForTimeout(600);
    expect(goalSubmitted).toBe(true);
  });

  test('blackboard tab shows topic and content', async ({ page }) => {
    await page.getByText('📋 Blackboard').click();
    await expect(page.getByText('jira_analysis')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('Found 15 P1 issues')).toBeVisible({ timeout: 3000 });
  });

  test('learning ledger tab shows promoted and rejected entries', async ({ page }) => {
    await expect(page.getByText('🧠 Learning Ledger')).toBeVisible({ timeout: 10000 });
    await page.getByText('🧠 Learning Ledger').click();
    await expect(page.getByText('promoted')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('rejected')).toBeVisible({ timeout: 10000 });
  });

  test('spawn audit tab shows approved and denied entries', async ({ page }) => {
    await page.getByText('🌱 Spawn Audit').click();
    await expect(page.getByText('approved')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('denied')).toBeVisible({ timeout: 3000 });
  });

  test('pause button calls controls/pause endpoint', async ({ page }) => {
    let pauseCalled = false;

    await page.route(
      new RegExp(`/civilizations/${CIV_ID}/controls/pause`),
      async (route) => {
        pauseCalled = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'paused' }),
        });
      }
    );

    await page.locator('button').filter({ hasText: 'Pause Civilization' }).click();
    await page.waitForTimeout(500);
    expect(pauseCalled).toBe(true);
  });
});

// ── Civilization list page ────────────────────────────────────────────────────

test.describe('Civilization List', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await setupCivilizationRoutes(page);
    await page.goto('/civilization');
  });

  test('civilization list page shows civilization name', async ({ page }) => {
    await expect(
      page.getByText('Test Civilization')
    ).toBeVisible({ timeout: 5000 });
  });

  test('civilization status badge shows active', async ({ page }) => {
    await expect(
      page.getByText('active').first()
    ).toBeVisible({ timeout: 5000 });
  });
});
