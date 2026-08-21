/**
 * Live E2E: Team Formation detail page against a real backend.
 * No route mocks. Requires live env + valid credentials.
 */
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5173';
const ORG_ID = process.env.TEST_ORG_ID;
const TEAM_ID = process.env.TEST_TEAM_ID;
const API_KEY = process.env.TEST_API_KEY;
const TENANT_ID = process.env.TEST_TENANT_ID ?? 'my-org';

const liveReady = !!ORG_ID && !!TEAM_ID && !!API_KEY;

test.skip(!liveReady, 'Set TEST_ORG_ID, TEST_TEAM_ID and TEST_API_KEY for live team E2E');

test.describe('Team Formation (live)', () => {
  test('team detail page loads and renders member section', async ({ page }) => {
    await page.goto(`${BASE_URL}/auth`);
    await page.getByLabel('Tenant ID').fill(TENANT_ID);
    await page.locator('#apiKey').fill(API_KEY!);
    await page.getByRole('button', { name: 'Sign in' }).click();

    await expect(page).not.toHaveURL(/\/auth$/);

    await page.goto(`${BASE_URL}/org/${ORG_ID!}/team/${TEAM_ID!}`);
    await page.waitForLoadState('networkidle');

    await expect(page).not.toHaveURL(/\/auth$/);
    await expect(page.getByRole('heading', { name: 'Mission Team' })).toBeVisible();

    // Accept either real members or explicit empty-state when no members exist yet.
    const memberOne = page.getByText('Agent 1');
    const emptyState = page.getByText('No team members available yet.');
    await expect(memberOne.or(emptyState)).toBeVisible({ timeout: 10000 });
  });
});
