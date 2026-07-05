/**
 * Shared E2E helpers for authentication and common API mocking.
 *
 * Pattern rationale:
 * - page.route(/localhost:8000/) catch-all is registered FIRST so it is last in
 *   Playwright's LIFO queue.  Specific mocks registered afterwards win.
 * - addInitScript writes to localStorage (Zustand's secureStorage reads
 *   sessionStorage ?? localStorage).  Including sessionValidated:true skips
 *   RequireAuth's GET /tenants/me call; we also mock that endpoint as a
 *   belt-and-suspenders safety net.
 */

import { type Page } from '@playwright/test';

// ── Constants ────────────────────────────────────────────────────────────────

export const TEST_API_KEY = process.env.TEST_API_KEY ?? 'test-api-key';
export const TEST_TENANT_ID = process.env.TEST_TENANT_ID ?? 'test-tenant';
export const TEST_PLAN = process.env.TEST_PLAN ?? 'professional';

// ── Primary helper ───────────────────────────────────────────────────────────

/**
 * Inject auth state into storage and set up the session-validation mock.
 *
 * Call this at the TOP of beforeEach (before page.goto) so addInitScript
 * executes when the page first loads.
 *
 * Example:
 * ```ts
 * test.beforeEach(async ({ page }) => {
 *   await setupAuth(page);
 *   await mockAgentsApi(page);   // add any page-specific mocks here
 *   await page.goto('/agents');
 * });
 * ```
 */
export async function setupAuth(page: Page): Promise<void> {
  // Catch-all: block unmocked localhost:8000 requests so they never reach the
  // real backend (which would 401 and trigger logout).  Must be registered
  // BEFORE specific mocks so LIFO ordering lets the specific mocks win.
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'unmocked — add a specific route for this test' }),
    })
  );

  // Hydrate Zustand auth store before the page scripts execute.
  // sessionValidated: true makes RequireAuth skip GET /tenants/me.
  await page.addInitScript(
    ({
      apiKey,
      tenantId,
      plan,
    }: {
      apiKey: string;
      tenantId: string;
      plan: string;
    }) => {
      const authState = JSON.stringify({
        state: {
          apiKey,
          tenantId,
          plan,
          isAuthenticated: true,
          ssoMode: false,
          accessToken: '',
          refreshToken: '',
          tokenExpiresAt: 0,
          sessionValidated: true,
        },
        version: 0,
      });
      // secureStorage reads sessionStorage ?? localStorage — set both so
      // it works regardless of which storage the store queries first.
      localStorage.setItem('av-auth', authState);
      sessionStorage.setItem('av-auth', authState);
      localStorage.setItem('av_api_key', apiKey);
    },
    { apiKey: TEST_API_KEY, tenantId: TEST_TENANT_ID, plan: TEST_PLAN }
  );

  // Belt-and-suspenders: mock the session-validation endpoint in case
  // sessionValidated resets during the test (e.g. after a full page reload).
  await page.route('**/tenants/me', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_id: TEST_TENANT_ID,
          name: 'E2E Test Tenant',
          plan: TEST_PLAN,
        }),
      });
    }
    return route.continue();
  });
}

// ── Common API mock helpers ──────────────────────────────────────────────────

export interface MockAgent {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template: string;
  is_active?: boolean;
  created_at?: string;
}

export interface MockGoal {
  id: string;
  goal_id?: string;
  goal: string;
  status: string;
  agent_id?: string;
  created_at?: string;
  result_artifact?: unknown;
}

/**
 * Mock GET/POST/DELETE /agents and GET /agents/{id}.
 *
 * @param agents - list returned by GET /agents
 * @param created - body returned by POST /agents (defaults to first agent or a stub)
 */
export async function mockAgentsApi(
  page: Page,
  agents: MockAgent[] = [],
  created?: MockAgent
): Promise<void> {
  await page.route(/localhost:8000\/agents/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // GET /agents/{id}
    if (method === 'GET' && url.match(/\/agents\/[^/?]+$/)) {
      const id = url.split('/agents/')[1].split('?')[0];
      const found = agents.find((a) => a.agent_id === id);
      return route.fulfill({
        status: found ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(found ?? { detail: 'not found' }),
      });
    }

    // GET /agents (list)
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(agents),
      });
    }

    // POST /agents (NL create)
    if (method === 'POST') {
      const stub: MockAgent = {
        agent_id: 'agent-new',
        name: 'New Agent',
        autonomy_mode: 'supervised',
        goal_template: '',
        is_active: true,
        created_at: new Date().toISOString(),
      };
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(created ?? stub),
      });
    }

    // DELETE /agents/{id}
    if (method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }

    return route.continue();
  });
}

/**
 * Mock GET /goals (list), POST /goals (create), GET /goals/{id}, and
 * GET /goals/metrics so pages render cleanly.
 */
export async function mockGoalsApi(
  page: Page,
  {
    goals = [] as MockGoal[],
    newGoal = null as MockGoal | null,
  } = {}
): Promise<void> {
  await page.route(/localhost:8000\/goals/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    if (method === 'POST' && url.includes('/cancel')) {
      const cancelled =
        goals.find((g) => url.includes(g.id)) ?? goals[0];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...(cancelled ?? {}), status: 'cancelled' }),
      });
    }

    if (method === 'POST') {
      const stub: MockGoal = {
        id: 'g-new',
        goal_id: 'g-new',
        status: 'planning',
        goal: 'New goal',
      };
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify(newGoal ?? stub),
      });
    }

    if (url.includes('/metrics')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_goals: 0,
          total_goals: goals.length,
          success_rate: 0,
          avg_latency_ms: 0,
          cost_today_usd: 0,
          goals_today: 0,
        }),
      });
    }

    // GET /goals/{id}
    if (method === 'GET' && url.match(/\/goals\/[^/?]+$/)) {
      const id = url.split('/goals/')[1].split('?')[0];
      const found = goals.find((g) => g.id === id || g.goal_id === id);
      return route.fulfill({
        status: found ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(found ?? { detail: 'not found' }),
      });
    }

    // GET /goals (list)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ goals }),
    });
  });
}

/**
 * Minimal mock for the /templates endpoint.
 */
export async function mockTemplatesApi(
  page: Page,
  templates: unknown[] = []
): Promise<void> {
  await page.route(/localhost:8000\/templates/, async (route) => {
    const method = route.request().method();
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(templates),
      });
    }
    return route.continue();
  });
}
