import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: { apiKey: 'test-api-key', tenantId: 'test-tenant', plan: '', isAuthenticated: true },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-api-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

test.describe('Collaboration', () => {
  test('creates and opens a collaboration session', async ({ page }) => {
    await setupAuth(page);

    await page.route('**/collab/sessions', async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: [] });
        return;
      }
      await route.fulfill({
        status: 201,
        json: {
          session_id: 'session-e2e',
          name: 'E2E review',
          mode: 'review',
          participants: ['human:lead', 'agent:jira'],
          participant_count: 2,
          status: 'active',
          content: 'Initial collaboration draft',
          goal_id: 'goal-e2e',
          agent_id: 'agent-e2e',
          created_at: '2026-06-25T00:00:00+00:00',
        },
      });
    });
    await page.route('**/collab/sessions/session-e2e/operations', async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.route('**/collab/sessions/session-e2e/consensus', async (route) => {
      await route.fulfill({ json: { agreed: false, summary: 'No agreement round yet' } });
    });

    await page.goto('/collaboration');
    await expect(page.getByRole('heading', { name: 'Collaboration' })).toBeVisible();
    await page.getByRole('button', { name: /new session/i }).click();
    await expect(page.getByText('New Collaboration Session')).toBeVisible();
    await page.getByPlaceholder(/session name|sprint planning/i).fill('E2E review');
    await page.getByPlaceholder('Goal ID').fill('goal-e2e');
    const agentInput = page.getByPlaceholder(/agent/i);
    if (await agentInput.count()) {
      await agentInput.fill('agent-e2e');
    }
    const participantsInput = page.getByPlaceholder(/participants/i);
    if (await participantsInput.count()) {
      await participantsInput.fill('human:lead,agent:jira');
    }
    await page.getByText('Create Session').click();

    await expect(page.getByText('Shared draft')).toBeVisible();
    await expect(page.locator('textarea').filter({ hasText: '' }).first()).toHaveValue(
      'Initial collaboration draft'
    );
    await expect(page.getByText('human:lead')).toBeVisible();
    await expect(page.getByText('agent:jira')).toBeVisible();
  });
});
