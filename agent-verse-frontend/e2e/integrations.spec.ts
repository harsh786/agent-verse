/**
 * Integrations — E2E Tests
 *
 * Covers /integrations → IntegrationsPage.tsx:
 *   1. Page load — provider cards (Slack, Zapier, Alertmanager, Datadog), endpoints
 *   2. Empty state — no Zapier completed goals
 *   3. Populated state — Zapier completed goals list
 *   4. Primary interaction — copy endpoint button
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function mockZapierGoals(page: Page, goals: Record<string, unknown>[] = []): Promise<void> {
  await page.route('**/integrations/zapier/goals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(goals) })
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — Page load
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Integrations — page load', () => {
  test('1. Renders header and all four provider cards with endpoints', async ({ page }) => {
    await setupAuth(page);
    await mockZapierGoals(page);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });

    for (const name of ['Slack', 'Zapier', 'Alertmanager', 'Datadog']) {
      await expect(page.getByRole('heading', { name, level: 2 })).toBeVisible();
    }

    await expect(page.getByText('/integrations/slack/commands')).toBeVisible();
    await expect(page.getByText('/integrations/zapier/trigger')).toBeVisible();
    await expect(page.getByText('/integrations/events/alertmanager')).toBeVisible();
    await expect(page.getByText('/integrations/events/datadog')).toBeVisible();
  });

  test('2. Shows required env vars for each provider', async ({ page }) => {
    await setupAuth(page);
    await mockZapierGoals(page);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('SLACK_SIGNING_SECRET')).toBeVisible();
    await expect(page.getByText('ZAPIER_SECRET')).toBeVisible();
    await expect(page.getByText('DATADOG_WEBHOOK_SECRET')).toBeVisible();
  });

  test('3. Provider cards show a running status badge', async ({ page }) => {
    await setupAuth(page);
    await mockZapierGoals(page);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    // 4 provider cards, each with a "running" StatusBadge.
    await expect(page.getByText(/running/i).first()).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — Zapier completed-goals empty state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Integrations — Zapier goals empty state', () => {
  test('4. Shows empty-state message when there are no completed goals', async ({ page }) => {
    await setupAuth(page);
    await mockZapierGoals(page, []);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText('No completed goals available to the Zapier poll trigger.')
    ).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 3 — Zapier completed-goals populated state
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Integrations — Zapier goals populated state', () => {
  test('5. Lists completed goals with status badges', async ({ page }) => {
    await setupAuth(page);
    await mockZapierGoals(page, [
      { goal_id: 'g-1', goal: 'Summarize weekly sales report', status: 'completed' },
      { goal_id: 'g-2', goal: 'Investigate prod alert', status: 'failed' },
    ]);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Summarize weekly sales report')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Investigate prod alert')).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 4 — Primary interaction: copy endpoint
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Integrations — copy endpoint', () => {
  test('6. Clicking the copy button copies the full endpoint URL and shows a toast', async ({
    page,
    context,
  }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await setupAuth(page);
    await mockZapierGoals(page);
    await page.goto('/integrations');

    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: 'Copy endpoint Slash command' }).click();

    await expect(page.getByRole('status').filter({ hasText: 'Endpoint copied to clipboard.' })).toBeVisible({
      timeout: 5000,
    });

    const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardText).toContain('/integrations/slack/commands');
  });
});
