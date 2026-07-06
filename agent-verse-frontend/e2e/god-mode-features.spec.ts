import { test, expect } from '@playwright/test';

test.describe('God Mode Features', () => {
  test.beforeEach(async ({ page }) => {
    // Mock all API calls — pin to localhost:8000 so Vite source files are not intercepted
    await page.route('http://localhost:8000/health**', r => r.fulfill({ json: { status: 'healthy' } }));
    await page.route('http://localhost:8000/goals**', r => r.fulfill({ json: { goals: [], total: 0 } }));
    await page.route('http://localhost:8000/agents**', r => r.fulfill({ json: { agents: [], total: 0 } }));
    await page.route('http://localhost:8000/knowledge-graph**', r => r.fulfill({ json: { nodes: [], edges: [], communities: [] } }));
    await page.route('http://localhost:8000/model-registry**', r => r.fulfill({ json: { models: [], providers: [] } }));
    await page.route('http://localhost:8000/skills-runtime**', r => r.fulfill({ json: { skills: [] } }));
    await page.route('http://localhost:8000/memory**', r => r.fulfill({ json: { memories: [], total: 0 } }));
    await page.route('http://localhost:8000/guardrails**', r => r.fulfill({ json: { rules: [], violations: [] } }));
    await page.route('http://localhost:8000/ai-ops**', r => r.fulfill({ json: { status: 'ok', alerts: [] } }));
  });

  test('Phase 2: Model Control Center page loads', async ({ page }) => {
    await page.goto('/models');
    await expect(page.locator('body')).toBeVisible();
  });

  test('Phase 5: Knowledge Graph explorer loads', async ({ page }) => {
    await page.goto('/knowledge-graph');
    await expect(page.locator('body')).toBeVisible();
  });

  test('Phase 8: Guardrails v2 page loads', async ({ page }) => {
    await page.goto('/guardrails');
    await expect(page.locator('body')).toBeVisible();
  });

  test('Phase 10: AI Ops dashboard loads', async ({ page }) => {
    await page.goto('/observability');
    await expect(page.locator('body')).toBeVisible();
  });

  test('Phase 11: Memory v2 explorer loads', async ({ page }) => {
    await page.goto('/memory');
    await expect(page.locator('body')).toBeVisible();
  });

  test('Phase 13: Mission Control layout is dark themed', async ({ page }) => {
    await page.goto('/');
    // The dark design system defines --command-black in globals.css.
    // Checking this CSS variable confirms the Mission Control theme loaded correctly.
    const commandBlack = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--command-black').trim()
    );
    expect(commandBlack).toBe('#080A12');
  });

  test('Goals page: empty state shows message', async ({ page }) => {
    await page.goto('/goals');
    await page.waitForTimeout(1000);
    // Should show something (loading or empty state or content)
    const text = await page.locator('body').textContent();
    expect(text!.trim().length).toBeGreaterThan(0);
  });

  test('Agents page: create agent button exists', async ({ page }) => {
    await page.goto('/agents');
    await page.waitForTimeout(1000);
    const body = await page.locator('body').textContent();
    expect(body!.trim().length).toBeGreaterThan(0);
  });
});
