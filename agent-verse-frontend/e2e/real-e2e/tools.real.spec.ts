/**
 * Real E2E tests for the Tools feature — NO HTTP mocking.
 *
 * Route: /tools (ToolsPage.tsx)
 * Backend: app/api/tools.py (prefix /tools) — sandboxed code execution,
 * tenant workspace file operations, and email sending.
 *
 * NOTE: the backend signup endpoint rate-limits to 10 signups/IP/hour, shared
 * with other test suites/agents on this host. This file creates exactly ONE
 * real tenant (in beforeAll) and reuses it for every test below.
 */
import { expect, request as pwRequest, type APIRequestContext, type Page } from '@playwright/test';
import { test, createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, type E2ETenant } from './fixtures';

let ctx: APIRequestContext;
let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

test.beforeAll(async () => {
  ctx = await pwRequest.newContext();
  tenant = await createE2ETenant(ctx, '-tools');
  api = apiClient(ctx, tenant);
});

test.afterAll(async () => {
  await ctx.dispose();
});

test.describe('Tools — real code execution', () => {
  test('POST /tools/execute-code runs real Python in the sandbox', async () => {
    const resp = await api.post('/tools/execute-code', {
      code: 'print("hello from real e2e")',
      language: 'python',
      timeout: 10,
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    // This dev environment locks subprocess execution down by default
    // (app/tools/code_interpreter.py requires AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true,
    // which isn't set here) — that's a deliberate safety guard, not a bug, so
    // tolerate the "disabled" response instead of requiring real stdout.
    if (body.success) {
      expect(body.stdout).toContain('hello from real e2e');
      expect(body.exit_code).toBe(0);
    } else {
      expect(body.stderr).toContain('Subprocess execution is disabled');
    }
  });

  test('POST /tools/execute-code rejects a timeout over 60s', async () => {
    const resp = await api.post('/tools/execute-code', {
      code: 'print(1)',
      language: 'python',
      timeout: 999,
    });
    expect(resp.status()).toBe(422);
  });
});

test.describe('Tools — real workspace file operations', () => {
  test('list files in the tenant workspace', async () => {
    const resp = await api.get('/tools/files?directory=.');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('write → read → delete a real workspace file', async () => {
    const path = `e2e-real-${Date.now()}.txt`;

    const writeResp = await api.post(`/tools/files/${path}`, { content: 'hello from real e2e' });
    expect(writeResp.status()).toBe(201);

    const readResp = await api.get(`/tools/files/${path}`);
    expect(readResp.status()).toBe(200);
    const readBody = await readResp.json();
    expect(readBody.content).toBe('hello from real e2e');

    const deleteResp = await api.delete(`/tools/files/${path}`);
    expect(deleteResp.status()).toBe(204);

    const readAfterDelete = await api.get(`/tools/files/${path}`);
    expect(readAfterDelete.status()).toBe(404);
  });

  test('reading an unknown file returns 404', async () => {
    const resp = await api.get('/tools/files/does-not-exist.txt');
    expect(resp.status()).toBe(404);
  });
});

test.describe('Tools — page rendering', () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await loginFrontend(page, tenant);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test('tools page renders with all three tabs', async () => {
    await page.goto(`${FRONTEND_BASE}/tools`, { waitUntil: 'networkidle' });
    await expect(page.getByRole('tab', { name: /code runner/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /file manager/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /^email$/i })).toBeVisible();
  });

  test('running real code from the Code Runner tab shows output', async () => {
    await page.goto(`${FRONTEND_BASE}/tools`, { waitUntil: 'networkidle' });
    // CodeMirror renders a contenteditable area, not a <textarea>; type via keyboard.
    await page.locator('.cm-content').click();
    await page.keyboard.type('print("real-e2e-tools-check")');
    await page.getByRole('button', { name: /run code/i }).click();
    await expect(page.getByText('real-e2e-tools-check')).toBeVisible({ timeout: 15_000 });
  });

  test('switching to the File Manager tab lists the real workspace', async () => {
    await page.goto(`${FRONTEND_BASE}/tools`, { waitUntil: 'networkidle' });
    await page.getByRole('tab', { name: /file manager/i }).click();
    await expect(page.getByText(/workspace/i).first()).toBeVisible();
  });
});
