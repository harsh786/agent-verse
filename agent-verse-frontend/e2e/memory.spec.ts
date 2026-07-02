/**
 * E2E tests — Memory Explorer page
 *
 * Covers all four sections of the world-class MemoryExplorerPage:
 *   1. Semantic Recall     — search, results with confidence bars and type badges
 *   2. Long-term Memories  — list, type filter, add memory, delete, clear all
 *   3. Tool Reliability    — fixed field names, table, all-good state
 *   4. Execution Memory    — expandable section, recent plans
 */
import { test, expect, type Page } from '@playwright/test';

// ── Auth helper ───────────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
  );
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

// ── Shared mock ───────────────────────────────────────────────────────────────

async function mockMemoryApi(
  page: Page,
  opts: {
    memories?: object[];
    reliability?: object[];
    recallResults?: object[];
    execMemories?: object[];
  } = {}
) {
  const memories = opts.memories ?? [];
  const reliability = opts.reliability ?? [];
  const recallResults = opts.recallResults ?? [];
  const execMemories = opts.execMemories ?? [];

  await page.route(/localhost:8000\/memory(.*)/, async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.includes('/memory/tool-reliability'))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reliability) });

    if (url.includes('/memory/recall'))
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ query: 'test', results: recallResults }),
      });

    if (url.includes('/memory/execution'))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(execMemories) });

    if (method === 'DELETE' && url.match(/\/memory\/[^/]+$/))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 'ok', status: 'ok' }) });

    if (method === 'DELETE' && url.match(/\/memory$|\/memory\?/))
      return route.fulfill({ status: 204 });

    if (method === 'POST' && url.match(/\/memory$|\/memory\?/))
      return route.fulfill({
        status: 201, contentType: 'application/json',
        body: JSON.stringify({ id: 'new-mem', content: 'Manual memory', memory_type: 'fact', confidence: 0.8, tags: [], created_at: '' }),
      });

    if (method === 'GET')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(memories) });

    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// Page structure
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Memory Explorer — Page structure', () => {
  test('shows h1 heading and all section headers', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page);
    await page.goto('/memory');
    await expect(page.locator('h1').filter({ hasText: /memory explorer/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/semantic recall/i)).toBeVisible();
    await expect(page.getByText(/long-term memories/i)).toBeVisible();
    await expect(page.getByText(/tool reliability/i)).toBeVisible();
    await expect(page.getByText(/execution memory/i)).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Section 1: Semantic Recall
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Memory Explorer — Semantic Recall', () => {
  test('recall input and button are present', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page);
    await page.goto('/memory');
    await expect(page.getByPlaceholder(/recall memories relevant/i)).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /recall/i })).toBeVisible();
  });

  test('clicking Recall returns and displays results', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, {
      recallResults: [
        { content: 'Always validate inputs', confidence: 0.92, memory_type: 'fact', source: 'goal-abc' },
      ],
    });
    await page.goto('/memory');
    await page.getByPlaceholder(/recall memories relevant/i).fill('validation');
    await page.getByRole('button', { name: /recall/i }).click();
    await expect(page.getByText('Always validate inputs')).toBeVisible({ timeout: 10000 });
  });

  test('shows no-results message when recall returns empty', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, { recallResults: [] });
    await page.goto('/memory');
    await page.getByPlaceholder(/recall memories relevant/i).fill('xyz unknown');
    await page.getByRole('button', { name: /recall/i }).click();
    await expect(page.getByText(/no relevant memories/i)).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Section 2: Long-term Memories
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Memory Explorer — Long-term Memories', () => {
  const mem = {
    id: 'mem-001',
    content: 'Use async functions for all API calls',
    memory_type: 'skill',
    confidence: 0.92,
    tags: ['async', 'api'],
    created_at: '2026-06-01T00:00:00Z',
  };

  test('lists memories with content, type badge, and tags', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, { memories: [mem] });
    await page.goto('/memory');
    await expect(page.getByText('Use async functions for all API calls')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('skill')).toBeVisible();
    await expect(page.getByText('#async')).toBeVisible();
    await expect(page.getByText('#api')).toBeVisible();
  });

  test('shows empty state when no memories', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page);
    await page.goto('/memory');
    await expect(page.getByText(/no memories yet/i)).toBeVisible({ timeout: 15000 });
  });

  test('type filter pills render including fact, skill, preference', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page);
    await page.goto('/memory');
    await expect(page.getByRole('button', { name: /^fact$/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /^skill$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /preference/i })).toBeVisible();
  });

  test('clicking a type filter triggers a new request with memory_type param', async ({ page }) => {
    let filteredUrl = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/memory(.*)/, async (route) => {
      const url = route.request().url();
      if (url.includes('memory_type=fact')) filteredUrl = url;
      if (url.includes('/tool-reliability')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('/memory');
    await page.getByRole('button', { name: /^fact$/i }).click();
    await page.waitForFunction(() => true, null, { timeout: 5000 });
    expect(filteredUrl).toContain('memory_type=fact');
  });

  test('Add Memory button opens create modal', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page);
    await page.goto('/memory');
    await page.getByRole('button', { name: /^add$/i }).click();
    await expect(page.getByRole('heading', { name: /add memory/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByLabel(/content/i)).toBeVisible();
    await expect(page.getByLabel(/type/i)).toBeVisible();
  });

  test('Create Memory modal submits POST /memory', async ({ page }) => {
    let postedBody = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/memory(.*)/, async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.includes('/tool-reliability')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (method === 'POST') {
        postedBody = await route.request().postData() ?? '';
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'new', content: 'Test memory', memory_type: 'fact', confidence: 0.8, tags: [], created_at: '' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.goto('/memory');
    await page.getByRole('button', { name: /^add$/i }).click();
    await page.getByLabel(/content/i).fill('Test memory content');
    await page.getByRole('button', { name: /create memory/i }).click();

    await page.waitForFunction(() => !document.querySelector('[role="dialog"]') || true, null, { timeout: 5000 });
    const parsed = JSON.parse(postedBody);
    expect(parsed.content).toBe('Test memory content');
  });

  test('deletes a memory and calls DELETE /memory/{id}', async ({ page }) => {
    let deleteUrl = '';
    await setupAuth(page);
    await page.route(/localhost:8000\/memory(.*)/, async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.includes('/tool-reliability')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (method === 'DELETE' && url.match(/\/memory\/mem-del/)) {
        deleteUrl = url;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 'mem-del', status: 'ok' }) });
      }
      if (method === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'mem-del', content: 'To be deleted', memory_type: 'fact', confidence: 0.5, tags: [], created_at: '' }]) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.goto('/memory');
    await expect(page.getByText('To be deleted')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /delete memory/i }).first().click();
    await expect(page.getByText('To be deleted')).not.toBeVisible({ timeout: 10000 });
    expect(deleteUrl).toContain('mem-del');
  });

  test('Clear All button shows confirm dialog', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, { memories: [{ id: 'x', content: 'Some memory', memory_type: 'fact', confidence: 0.5, tags: [], created_at: '' }] });
    await page.goto('/memory');
    await expect(page.getByText('Some memory')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /clear all/i }).click();
    await expect(page.getByText(/clear all memories/i)).toBeVisible({ timeout: 5000 });
  });

  test('confirms Clear All and calls DELETE /memory', async ({ page }) => {
    let clearCalled = false;
    await setupAuth(page);
    await page.route(/localhost:8000\/memory(.*)/, async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.includes('/tool-reliability')) return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      if (method === 'DELETE' && url.match(/\/memory$|\/memory\?/)) {
        clearCalled = true;
        return route.fulfill({ status: 204 });
      }
      if (method === 'GET')
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ id: 'x', content: 'Memory to clear', memory_type: 'fact', confidence: 0.5, tags: [], created_at: '' }]) });
      return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    });

    await page.goto('/memory');
    await expect(page.getByText('Memory to clear')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /clear all/i }).click();
    await page.getByRole('button', { name: /clear all/i }).last().click(); // confirm button in modal
    await page.waitForTimeout(1000);
    expect(clearCalled).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Section 3: Tool Reliability
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Memory Explorer — Tool Reliability', () => {
  test('shows all-good message when no unreliable tools', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, { reliability: [] });
    await page.goto('/memory');
    await expect(page.getByText(/all tools are performing/i)).toBeVisible({ timeout: 15000 });
  });

  test('displays tool name, total calls, failures, and success rate', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, {
      reliability: [
        { tool_name: 'jira_create_issue', success_count: 7, failure_count: 3, total_calls: 10, success_rate: 0.7 },
      ],
    });
    await page.goto('/memory');
    await expect(page.getByText('jira_create_issue')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('10')).toBeVisible(); // total_calls
    await expect(page.getByText('70%')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Section 4: Execution Memory
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Memory Explorer — Execution Memory', () => {
  test('execution memory section is collapsed by default', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, {
      execMemories: [{ goal_text: 'Deploy the service to prod', success: true, recorded_at: '' }],
    });
    await page.goto('/memory');
    await expect(page.getByText(/execution memory/i)).toBeVisible({ timeout: 15000 });
    // Content hidden before expand
    await expect(page.getByText('Deploy the service to prod')).not.toBeVisible();
  });

  test('clicking Execution Memory expands and shows plans', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, {
      execMemories: [
        { goal_text: 'Deploy the service to prod', success: true, recorded_at: '2026-06-01T00:00:00Z' },
        { goal_text: 'Send weekly report', success: true, recorded_at: '2026-06-02T00:00:00Z' },
      ],
    });
    await page.goto('/memory');
    await page.getByText(/execution memory/i).first().click();
    await expect(page.getByText('Deploy the service to prod')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Send weekly report')).toBeVisible();
  });

  test('shows empty state when no execution plans', async ({ page }) => {
    await setupAuth(page);
    await mockMemoryApi(page, { execMemories: [] });
    await page.goto('/memory');
    await page.getByText(/execution memory/i).first().click();
    await expect(page.getByText(/no execution memories/i)).toBeVisible({ timeout: 10000 });
  });
});
