/**
 * Comprehensive tests for the AgentsListPage (/agents).
 *
 * Coverage:
 * - Page structure: heading, subtitle, search input, filter pills, table headers
 * - Table content: name, status badge (Active/Inactive), autonomy mode label, goal template
 * - Client-side search: filtering by name, updates ?q= URL param
 * - Client-side autonomy filter: pills filter rows, update ?mode= URL param
 * - Sort: clicking Name / Created column headers updates ?sort= URL param
 * - Empty states: global "No agents yet" and filter "No matching agents"
 * - Create modal: open, textarea state, disabled/enabled button, cancel
 * - Delete confirmation: clicking Delete row action opens confirm dialog
 * - Row navigation: clicking a row navigates to /agents/{id}
 *
 * All tests use the shared setupAuth helper and a local mockAgents() helper
 * so no real backend is needed.
 */

import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

const MOCK_AGENTS = [
  {
    agent_id: 'agent-001',
    name: 'DevOps Bot',
    autonomy_mode: 'bounded-autonomous',
    goal_template: 'Deploy {{service}} to {{env}}',
    is_active: true,
    created_at: new Date().toISOString(),
  },
  {
    agent_id: 'agent-002',
    name: 'Code Reviewer',
    autonomy_mode: 'supervised',
    goal_template: 'Review PR {{pr_number}} in {{repo}}',
    is_active: false,
    created_at: new Date(Date.now() - 86_400_000).toISOString(),
  },
  {
    agent_id: 'agent-003',
    name: 'Infra Guardian',
    autonomy_mode: 'fully-autonomous',
    goal_template: 'Monitor all prod services and alert on anomalies',
    is_active: true,
    created_at: new Date(Date.now() - 172_800_000).toISOString(),
  },
];

// ── Local mock helper ──────────────────────────────────────────────────────────

async function mockAgents(page: Page, agents = MOCK_AGENTS) {
  await page.route(/localhost:8000\/agents/, async (route) => {
    const method = route.request().method();
    const url = route.request().url();

    // GET /agents/{id} — individual agent detail
    if (method === 'GET' && url.match(/\/agents\/[^/?]+$/)) {
      const id = url.split('/agents/')[1].split('?')[0];
      const found = agents.find((a) => a.agent_id === id);
      return route.fulfill({
        status: found ? 200 : 404,
        contentType: 'application/json',
        body: JSON.stringify(found ?? { detail: 'not found' }),
      });
    }

    // GET /agents — list
    if (method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(agents),
      });
    }

    // POST /agents — NL create
    if (method === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_id: 'agent-new',
          name: 'New Agent',
          autonomy_mode: 'supervised',
          goal_template: '',
          is_active: true,
          created_at: new Date().toISOString(),
        }),
      });
    }

    // DELETE /agents/{id}
    if (method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }

    return route.continue();
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Agents list page', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await mockAgents(page);
    await page.goto('/agents');
    // Wait for page to fully render before each test
    await expect(page.locator('h1').filter({ hasText: /agent/i })).toBeVisible({
      timeout: 15_000,
    });
  });

  // ── Page structure ──────────────────────────────────────────────────────────

  test('shows the agent registry h1 heading', async ({ page }) => {
    // The page heading is "Agent Registry"; the old /^agents$/i could never match.
    await expect(page.locator('h1').filter({ hasText: /agent registry/i })).toBeVisible();
  });

  test('shows the autonomous-agents subtitle', async ({ page }) => {
    // Actual copy: "{n} autonomous agents under mission control".
    await expect(
      page.getByText(/autonomous agents under mission control/i)
    ).toBeVisible();
  });

  test('shows search input with correct placeholder', async ({ page }) => {
    await expect(page.locator('input[placeholder*="Search agents"]')).toBeVisible();
  });

  test('shows all four autonomy filter pills', async ({ page }) => {
    for (const label of ['All', 'Supervised', 'Bounded Autonomous', 'Fully Autonomous']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
  });

  test('shows the "+ New Agent" button', async ({ page }) => {
    // i18n key agents.new → "New Agent"
    await expect(page.locator('button').filter({ hasText: /new agent/i })).toBeVisible();
  });

  test('shows table column headers', async ({ page }) => {
    for (const header of ['Name', 'Status', 'Autonomy Mode', 'Goal Template', 'Created', 'Actions']) {
      await expect(page.locator('th').filter({ hasText: header })).toBeVisible({ timeout: 10_000 });
    }
  });

  // ── Table content ───────────────────────────────────────────────────────────

  test('renders all agent names from the API', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Code Reviewer')).toBeVisible();
    await expect(page.getByText('Infra Guardian')).toBeVisible();
  });

  test('shows "Active" badge for is_active:true agents', async ({ page }) => {
    // DevOps Bot and Infra Guardian are active
    const activeBadges = page.getByText('Active');
    await expect(activeBadges.first()).toBeVisible({ timeout: 10_000 });
  });

  test('shows "Inactive" badge for is_active:false agents', async ({ page }) => {
    // Code Reviewer is inactive
    await expect(page.getByText('Inactive')).toBeVisible({ timeout: 10_000 });
  });

  test('shows human-readable autonomy mode labels in table rows', async ({ page }) => {
    // Each label also names a filter pill above the table, so scope to rows —
    // an unscoped getByText is a strict-mode violation (2 matches).
    const rows = page.locator('tbody');
    await expect(rows.getByText('Bounded Autonomous')).toBeVisible({ timeout: 10_000 });
    await expect(rows.getByText('Supervised')).toBeVisible();
    await expect(rows.getByText('Fully Autonomous')).toBeVisible();
  });

  test('shows goal template text for each agent', async ({ page }) => {
    await expect(page.getByText('Deploy {{service}} to {{env}}')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Review PR {{pr_number}} in {{repo}}')).toBeVisible();
  });

  test('shows View and Delete action buttons in each row', async ({ page }) => {
    const firstRow = page.locator('tbody tr').first();
    await expect(firstRow.getByRole('button', { name: 'View' })).toBeVisible({ timeout: 10_000 });
    await expect(firstRow.getByRole('button', { name: 'Delete' })).toBeVisible();
  });

  // ── Search ──────────────────────────────────────────────────────────────────

  test('typing in search input shows only matching agents', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    await page.locator('input[placeholder*="Search agents"]').fill('Code');
    await expect(page.getByText('Code Reviewer')).toBeVisible();
    await expect(page.getByText('DevOps Bot')).not.toBeVisible();
    await expect(page.getByText('Infra Guardian')).not.toBeVisible();
  });

  test('search updates the ?q= URL param', async ({ page }) => {
    await page.locator('input[placeholder*="Search agents"]').fill('DevOps');
    await expect(page).toHaveURL(/q=DevOps/, { timeout: 5_000 });
  });

  test('clearing search shows all agents again', async ({ page }) => {
    const search = page.locator('input[placeholder*="Search agents"]');
    await search.fill('Infra');
    await expect(page.getByText('DevOps Bot')).not.toBeVisible();
    await search.clear();
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 5_000 });
  });

  test('shows "No matching agents" when search yields zero results', async ({ page }) => {
    await page.locator('input[placeholder*="Search agents"]').fill('xyzzy-no-match-999');
    await expect(page.getByText('No matching agents')).toBeVisible({ timeout: 10_000 });
  });

  // ── Autonomy filter ─────────────────────────────────────────────────────────

  test('Supervised filter shows only supervised agents', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Supervised', exact: true }).click();
    await expect(page.getByText('Code Reviewer')).toBeVisible();
    await expect(page.getByText('DevOps Bot')).not.toBeVisible();
    await expect(page.getByText('Infra Guardian')).not.toBeVisible();
  });

  test('Bounded Autonomous filter shows only bounded-autonomous agents', async ({ page }) => {
    await page.getByRole('button', { name: 'Bounded Autonomous', exact: true }).click();
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Code Reviewer')).not.toBeVisible();
    await expect(page.getByText('Infra Guardian')).not.toBeVisible();
  });

  test('Fully Autonomous filter shows only fully-autonomous agents', async ({ page }) => {
    await page.getByRole('button', { name: 'Fully Autonomous', exact: true }).click();
    await expect(page.getByText('Infra Guardian')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('DevOps Bot')).not.toBeVisible();
    await expect(page.getByText('Code Reviewer')).not.toBeVisible();
  });

  test('All filter pill restores all agents after a filter was applied', async ({ page }) => {
    await page.getByRole('button', { name: 'Supervised', exact: true }).click();
    await expect(page.getByText('Code Reviewer')).toBeVisible({ timeout: 5_000 });
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Infra Guardian')).toBeVisible();
  });

  // ── Sorting ─────────────────────────────────────────────────────────────────

  test('clicking Name column header sets ?sort=name in the URL', async ({ page }) => {
    await page.locator('th').filter({ hasText: /^Name/ }).click();
    await expect(page).toHaveURL(/sort=name/, { timeout: 5_000 });
  });

  test('clicking Name column header a second time toggles sort direction', async ({ page }) => {
    const nameTh = page.locator('th').filter({ hasText: /^Name/ });
    await nameTh.click();
    await expect(page).toHaveURL(/dir=asc/, { timeout: 5_000 });
    await nameTh.click();
    await expect(page).toHaveURL(/dir=desc/, { timeout: 5_000 });
  });

  test('clicking Created column header sets ?sort=created_at in the URL', async ({ page }) => {
    await page.locator('th').filter({ hasText: /^Created/ }).click();
    await expect(page).toHaveURL(/sort=created_at/, { timeout: 5_000 });
  });

  // ── Row navigation ──────────────────────────────────────────────────────────

  test('clicking an agent row navigates to its detail page', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    await page.locator('tr[role="button"]').filter({ hasText: 'DevOps Bot' }).click();
    await expect(page).toHaveURL(/\/agents\/agent-001/, { timeout: 10_000 });
  });

  test('clicking the View action button navigates to the detail page', async ({ page }) => {
    const row = page.locator('tbody tr').filter({ hasText: 'Code Reviewer' });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole('button', { name: 'View' }).click();
    await expect(page).toHaveURL(/\/agents\/agent-002/, { timeout: 10_000 });
  });

  // ── Create modal ─────────────────────────────────────────────────────────────

  test('clicking New Agent opens the create modal', async ({ page }) => {
    await page.locator('button').filter({ hasText: /new agent/i }).click();
    await expect(page.getByText('Deploy New Agent')).toBeVisible({
      timeout: 5_000,
    });
    await expect(page.locator('textarea[placeholder*="Create an agent that"]')).toBeVisible();
  });

  test('Create button is disabled when the textarea is empty', async ({ page }) => {
    await page.locator('button').filter({ hasText: /new agent/i }).click();
    await expect(page.getByRole('button', { name: /^Deploy Agent$/ })).toBeDisabled({ timeout: 5_000 });
  });

  test('Create button becomes enabled once textarea has text', async ({ page }) => {
    await page.locator('button').filter({ hasText: /new agent/i }).click();
    await page
      .locator('textarea[placeholder*="Create an agent that"]')
      .fill('Monitor all GitHub repos for critical security alerts');
    await expect(page.getByRole('button', { name: /^Deploy Agent$/ })).toBeEnabled({ timeout: 5_000 });
  });

  test('Cancel button in create modal closes it without navigating', async ({ page }) => {
    await page.locator('button').filter({ hasText: /new agent/i }).click();
    await expect(page.getByText('Deploy New Agent')).toBeVisible({
      timeout: 5_000,
    });
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByText('Deploy New Agent')).not.toBeVisible();
    // Still on /agents
    await expect(page).toHaveURL(/\/agents$/);
  });

  test('Escape key closes the create modal', async ({ page }) => {
    await page.locator('button').filter({ hasText: /new agent/i }).click();
    await expect(page.getByText('Deploy New Agent')).toBeVisible({
      timeout: 5_000,
    });
    await page.keyboard.press('Escape');
    await expect(page.getByText('Deploy New Agent')).not.toBeVisible({
      timeout: 3_000,
    });
  });

  // ── Delete confirmation ──────────────────────────────────────────────────────

  test('clicking Delete shows a confirmation modal with destructive copy', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    const row = page.locator('tbody tr').filter({ hasText: 'DevOps Bot' });
    // Stop propagation so the row click (navigate) doesn't fire
    await row.getByRole('button', { name: 'Delete' }).click();
    // ConfirmModal should appear
    await expect(page.getByText(/delete agent/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('This action cannot be undone')).toBeVisible();
  });

  test('cancelling delete confirmation keeps the agent in the list', async ({ page }) => {
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 10_000 });
    const row = page.locator('tbody tr').filter({ hasText: 'DevOps Bot' });
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByText(/delete agent/i)).toBeVisible({ timeout: 5_000 });
    // Click the Cancel button in the confirm modal
    await page.getByRole('button', { name: /cancel/i }).last().click();
    await expect(page.getByText('DevOps Bot')).toBeVisible({ timeout: 5_000 });
  });
});

// ── Empty state tests (separate describe to avoid beforeEach's mockAgents) ────

test.describe('Agents list page — empty state', () => {
  test('shows "No agents yet" when the API returns an empty list', async ({ page }) => {
    await setupAuth(page);
    // Override with empty list — must be registered AFTER the catch-all from setupAuth
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
    await page.goto('/agents');
    await expect(page.locator('h1').filter({ hasText: /agent/i })).toBeVisible({
      timeout: 15_000,
    });
    // i18n: agents.noAgents → "No agents yet"
    await expect(page.getByText('No agents yet')).toBeVisible({ timeout: 10_000 });
  });

  test('shows secondary empty-state description text', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
    await page.goto('/agents');
    await expect(page.locator('h1').filter({ hasText: /agent/i })).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByText('Deploy your first agent using the button above.')
    ).toBeVisible({ timeout: 10_000 });
  });

  test('shows error message when agent API returns 500', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/agents/, (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' }),
      })
    );
    await page.goto('/agents');
    await expect(page.locator('h1').filter({ hasText: /agent/i })).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      page.getByText(/failed to load agents/i)
    ).toBeVisible({ timeout: 10_000 });
  });
});
