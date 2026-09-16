/**
 * Channel Mappings E2E Tests
 *
 * Covers /channel-mappings (src/features/channels/ChannelMappingsPage.tsx):
 *   1. Loads and renders header/content
 *   2. Empty state when no mappings
 *   3. Populated state renders mapping list
 *   4. Primary interaction: add a channel via the modal
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MockMapping {
  id: string;
  channel_type: string;
  channel_id: string;
  created_at?: string;
}

async function mockChannelsApi(
  page: Page,
  { mappings = [] as MockMapping[], created }: { mappings?: MockMapping[]; created?: MockMapping } = {}
): Promise<void> {
  await page.route(/localhost:8000\/channels\/mappings/, async (route) => {
    const method = route.request().method();

    if (method === 'POST') {
      const body = route.request().postDataJSON() as { channel_type: string; channel_id: string };
      const stub: MockMapping = created ?? {
        id: 'ch-new',
        channel_type: body.channel_type,
        channel_id: body.channel_id,
        created_at: new Date().toISOString(),
      };
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(stub) });
    }

    // GET (list)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mappings) });
  });
}

const SAMPLE_MAPPINGS: MockMapping[] = [
  { id: 'ch-1', channel_type: 'slack', channel_id: 'T12345ABCD', created_at: '2026-01-01T00:00:00Z' },
  { id: 'ch-2', channel_type: 'email', channel_id: 'support@company.com', created_at: '2026-01-02T00:00:00Z' },
];

// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Channel Mappings — Page load', () => {
  test('1. Renders header and add-channel button', async ({ page }) => {
    await setupAuth(page);
    await mockChannelsApi(page, { mappings: SAMPLE_MAPPINGS });
    await page.goto('/channel-mappings');

    await expect(page.getByRole('heading', { name: /channel connections/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /add channel/i })).toBeVisible();
  });

  test('2. Shows error state with retry when the API call fails', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/channels\/mappings/, (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await page.goto('/channel-mappings');

    await expect(page.getByRole('alert')).toContainText(/failed to load channel mappings/i, { timeout: 10000 });
    await expect(page.getByRole('button', { name: /retry/i })).toBeVisible();
  });
});

test.describe('Channel Mappings — Empty state', () => {
  test('3. Shows empty state when no channels are connected', async ({ page }) => {
    await setupAuth(page);
    await mockChannelsApi(page, { mappings: [] });
    await page.goto('/channel-mappings');

    await expect(page.getByRole('heading', { name: /channel connections/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no channels connected/i)).toBeVisible();
  });
});

test.describe('Channel Mappings — Populated state', () => {
  test('4. Renders the list of connected channels', async ({ page }) => {
    await setupAuth(page);
    await mockChannelsApi(page, { mappings: SAMPLE_MAPPINGS });
    await page.goto('/channel-mappings');

    await expect(page.getByText('T12345ABCD')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('support@company.com')).toBeVisible();
    await expect(page.getByText(/connected/i).first()).toBeVisible();
  });
});

test.describe('Channel Mappings — Add channel interaction', () => {
  test('5. Opens the add-channel modal, fills the form, and connects', async ({ page }) => {
    await setupAuth(page);
    await mockChannelsApi(page, { mappings: [] });
    await page.goto('/channel-mappings');

    await expect(page.getByText(/no channels connected/i)).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /add channel/i }).click();
    const dialog = page.getByRole('dialog', { name: /add channel connection/i });
    await expect(dialog).toBeVisible();

    // Default channel type is slack → label is "Workspace ID"
    await expect(dialog.getByText(/workspace id/i)).toBeVisible();
    await dialog.locator('input[type="text"]').fill('T99999XYZ');

    await dialog.getByRole('button', { name: /connect channel/i }).click();
    await expect(dialog).toBeHidden({ timeout: 5000 });
  });

  test('6. Connect button is disabled until a channel id is entered', async ({ page }) => {
    await setupAuth(page);
    await mockChannelsApi(page, { mappings: [] });
    await page.goto('/channel-mappings');

    await expect(page.getByText(/no channels connected/i)).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /add channel/i }).click();

    const dialog = page.getByRole('dialog', { name: /add channel connection/i });
    const connectBtn = dialog.getByRole('button', { name: /connect channel/i });
    await expect(connectBtn).toBeDisabled();

    await dialog.locator('input[type="text"]').fill('T99999XYZ');
    await expect(connectBtn).toBeEnabled();
  });
});
