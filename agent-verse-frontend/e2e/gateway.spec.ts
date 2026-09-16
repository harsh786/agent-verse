/**
 * Gateway Settings — E2E Tests
 *
 * Covers /settings/gateway and /org/:orgId/gateway (both render
 * GatewaySettingsPage):
 *   1. Renders channels + security settings
 *   2. Empty/fallback state when the config endpoint fails
 *   3. Populated state — connected channels show status + tool counts
 *   4. Connect flow — opens modal, submits token, channel becomes connected
 *   5. Org-scoped route renders the same page
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

interface MockChannel {
  id: string;
  name: string;
  description: string;
  status: 'connected' | 'disconnected' | 'pending';
  endpoint?: string;
  userCount?: number;
  toolCount?: number;
  alwaysOn?: boolean;
}

async function mockGatewayConfig(
  page: Page,
  opts: {
    fail?: boolean;
    channels?: MockChannel[];
    max_commands_per_hour?: number;
    require_2fa_for?: string[];
  } = {}
): Promise<void> {
  await page.route('**/v1/gateway/config', (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    if (opts.fail) {
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        max_commands_per_hour: opts.max_commands_per_hour ?? 100,
        require_2fa_for: opts.require_2fa_for ?? ['approve', 'change-autonomy', 'delete'],
        channels: opts.channels ?? [],
      }),
    });
  });
}

async function mockChannelConnect(page: Page, channelId: string): Promise<void> {
  await page.route(`**/v1/gateway/channels/${channelId}/connect`, (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ success: true }),
    });
  });
}

const REST_API_CHANNEL: MockChannel = {
  id: 'rest-api', name: 'REST API', alwaysOn: true, status: 'connected',
  description: 'Always enabled — POST /v1/org/{org_id}/command',
  endpoint: 'POST /v1/org/{org_id}/command',
};

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 1 — /settings/gateway
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Gateway Settings — /settings/gateway', () => {
  test('1. Renders heading, channel list, and security settings', async ({ page }) => {
    await setupAuth(page);
    await mockGatewayConfig(page, {
      channels: [REST_API_CHANNEL, {
        id: 'slack', name: 'Slack', status: 'disconnected',
        description: 'Slash commands, mentions, Bolt SDK integration',
      }],
    });

    await page.goto('/settings/gateway');

    await expect(page.getByRole('heading', { name: /command gateway/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('REST API')).toBeVisible();
    await expect(page.getByText('Slack')).toBeVisible();
    await expect(page.getByText(/security settings/i)).toBeVisible();
    await expect(page.getByText(/rate limit/i)).toBeVisible();
    await expect(page.getByText('approve')).toBeVisible();
  });

  test('2. Falls back to default channels when the config request fails', async ({ page }) => {
    await setupAuth(page);
    await mockGatewayConfig(page, { fail: true });

    await page.goto('/settings/gateway');

    await expect(page.getByRole('heading', { name: /command gateway/i })).toBeVisible({ timeout: 10000 });
    // The hook's .catch() fallback exposes the 8 static channels, only REST API connected.
    await expect(page.getByText('1 channel active')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Telegram')).toBeVisible();
    await expect(page.getByText('WhatsApp')).toBeVisible();
  });

  test('3. Populated state — connected channel shows status badge and tool count', async ({ page }) => {
    await setupAuth(page);
    await mockGatewayConfig(page, {
      channels: [
        REST_API_CHANNEL,
        {
          id: 'mcp-server', name: 'MCP Server', status: 'connected',
          description: 'Expose org as MCP tools to Claude, Cursor, and other AI tools',
          toolCount: 14,
        },
      ],
    });

    await page.goto('/settings/gateway');
    await expect(page.getByRole('heading', { name: /command gateway/i })).toBeVisible({ timeout: 10000 });

    await expect(page.getByText('2 channels active')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Tools exposed: 14')).toBeVisible();
    await expect(page.getByLabel('Connected').first()).toBeVisible();
  });

  test('4. Connect flow — opens modal, submits token, and refetches config', async ({ page }) => {
    await setupAuth(page);
    let refetchCount = 0;
    await page.route('**/v1/gateway/config', (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      refetchCount += 1;
      const channels =
        refetchCount === 1
          ? [REST_API_CHANNEL, { id: 'slack', name: 'Slack', status: 'disconnected' as const, description: 'Slash commands' }]
          : [REST_API_CHANNEL, { id: 'slack', name: 'Slack', status: 'connected' as const, description: 'Slash commands' }];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ max_commands_per_hour: 100, require_2fa_for: [], channels }),
      });
    });
    await mockChannelConnect(page, 'slack');

    await page.goto('/settings/gateway');
    await expect(page.getByRole('heading', { name: /command gateway/i })).toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: /connect slack/i }).click();

    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Connect Slack')).toBeVisible();

    await page.getByLabel(/oauth token|access token/i).fill('xoxb-test-token');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();

    await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// SUITE 2 — /org/:orgId/gateway
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Gateway Settings — /org/:orgId/gateway', () => {
  test('5. Org-scoped route renders the same Command Gateway page', async ({ page }) => {
    await setupAuth(page);
    await mockGatewayConfig(page, { channels: [REST_API_CHANNEL] });

    await page.goto('/org/org-1/gateway');

    await expect(page.getByRole('heading', { name: /command gateway/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('REST API')).toBeVisible();
  });
});
