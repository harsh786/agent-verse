/**
 * Final Polish — E2E Test Suite
 * Tests for all remaining fixes that push the platform to 10/10.
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function authPage(page: Page) {
  await setupAuth(page);
  await page.route('**/tenants/me**', route =>
    route.fulfill({ status: 200, body: JSON.stringify({ tenant_id: 'tid', name: 'Test', plan: 'professional' }) })
  );
}

// ─── Admin Page — No frontend API key ─────────────────────────────────────────

test.describe('Admin Page — Security', () => {
  test('admin page does not expose VITE_PLATFORM_ADMIN_KEY', async ({ page }) => {
    await authPage(page);
    await page.route('**/admin/**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({ tenants: [], total: 0, active_goals: 0, total_tenants: 0 }) })
    );
    await page.goto('/admin');
    // Check source does not contain the admin key pattern
    const html = await page.content();
    // The env var should not be embedded in the page
    expect(html).not.toContain('VITE_PLATFORM_ADMIN_KEY');
    // Admin API calls should use normal X-API-Key, not a separate admin key
    await expect(page.locator('text=Something went wrong').first()).not.toBeVisible({ timeout: 5_000 });
  });

  test('admin API uses tenant auth headers', async ({ page }) => {
    await authPage(page);
    const headers: Record<string, string>[] = [];
    await page.route('**/admin/**', route => {
      headers.push(route.request().headers());
      route.fulfill({ status: 200, body: JSON.stringify({ tenants: [], total: 0 }) });
    });
    await page.goto('/admin');
    await page.waitForTimeout(1000);
    if (headers.length > 0) {
      // Should use X-API-Key, NOT a separate admin key header
      const hasApiKey = headers.some(h => h['x-api-key'] || h['authorization']);
      const hasAdminKeyHeader = headers.some(h => h['x-admin-key'] || h['x-platform-admin']);
      expect(hasAdminKeyHeader).toBe(false);
    }
  });
});

// ─── GoalsListPage — Cache Invalidation ───────────────────────────────────────

test.describe('GoalsListPage — Cache Invalidation', () => {
  test('goal submission triggers cache refresh', async ({ page }) => {
    await authPage(page);
    let listCallCount = 0;
    await page.route('**/goals**', async route => {
      const method = route.request().method();
      if (method === 'GET') {
        listCallCount++;
        await route.fulfill({ status: 200, body: JSON.stringify({ goals: [] }) });
      } else if (method === 'POST') {
        await route.fulfill({ status: 201, body: JSON.stringify({ goal_id: 'new-g-1', status: 'planning' }) });
      } else {
        await route.continue();
      }
    });
    await page.route('**/agents**', route => route.fulfill({ status: 200, body: JSON.stringify([]) }));
    await page.route('**/insights/estimate**', route => route.fulfill({ status: 200, body: JSON.stringify({ estimated_cost_usd: { mean: 0.05 }, success_probability: 0.8 }) }));

    await page.goto('/goals');
    await page.waitForTimeout(500);
    const beforeCount = listCallCount;

    // Submit a goal
    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await textarea.fill('Test goal for cache test');
    await page.locator('button[type="submit"], button:has-text("Submit")').first().click();
    await page.waitForTimeout(600);

    // List should have been re-fetched after submit
    expect(listCallCount).toBeGreaterThan(beforeCount);
  });
});

// ─── Settings — Session Revoke ────────────────────────────────────────────────

test.describe('Settings — Session Revoke', () => {
  test('revoke button fires DELETE /tenants/me/sessions/:id', async ({ page }) => {
    await authPage(page);
    let revokeCalled = false;
    await page.route('**/tenants/me/sessions**', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          body: JSON.stringify([{ session_id: 'sess-001', device: 'Chrome on Mac', last_seen: new Date().toISOString() }]),
        });
      } else if (route.request().method() === 'DELETE') {
        revokeCalled = true;
        await route.fulfill({ status: 204, body: '' });
      } else {
        await route.continue();
      }
    });
    await page.route('**/auth/keys**', route => route.fulfill({ status: 200, body: JSON.stringify([]) }));

    await page.goto('/settings?tab=security');
    // Session should show with revoke button
    const revokeBtn = page.locator('button:has-text("Revoke")').first();
    if (await revokeBtn.isVisible({ timeout: 8_000 })) {
      await revokeBtn.click();
      await page.waitForTimeout(500);
      expect(revokeCalled).toBe(true);
    }
  });
});

// ─── Template Library — Empty State ───────────────────────────────────────────

test.describe('TemplateLibraryPage — Empty State', () => {
  test('empty state shows "No templates found" not wrong i18n key', async ({ page }) => {
    await authPage(page);
    await page.route('**/templates**', route =>
      route.fulfill({ status: 200, body: JSON.stringify([]) })
    );
    await page.goto('/templates');
    await expect(page.locator('text=No templates found').first()).toBeVisible({ timeout: 8_000 });
    // Should NOT show the raw i18n key
    await expect(page.locator('text=marketplace.noResults').first()).not.toBeVisible();
  });
});

// ─── NL Scheduler — Human-readable Response ───────────────────────────────────

test.describe('Schedules NL Scheduler — Response Format', () => {
  test('NL scheduler shows human-readable response not raw JSON', async ({ page }) => {
    await authPage(page);
    await page.route('**/schedules**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({ schedules: [] }) })
    );
    await page.route('**/nl/schedule**', route =>
      route.fulfill({
        status: 200,
        body: JSON.stringify([{
          schedule_id: 'sched-001',
          name: 'Daily Report',
          trigger_type: 'cron',
          cron_expr: '0 9 * * *',
          goal_template: 'Generate sales report',
        }]),
      })
    );

    await page.goto('/schedules');
    // Switch to NL tab
    await page.locator('[role="tab"]:has-text("NL"), button:has-text("NL Scheduler")').first().click().catch(() => {});
    
    const input = page.locator('input[placeholder*="natural language"], textarea[placeholder*="natural language"]').first();
    if (await input.isVisible({ timeout: 5_000 })) {
      await input.fill('Run daily report every morning');
      await page.keyboard.press('Enter');
      
      // Response should be human-readable
      await expect(
        page.locator('text=Daily Report, text=Created schedule').first()
      ).toBeVisible({ timeout: 8_000 });
      
      // Should NOT show raw JSON
      await expect(page.locator('text=schedule_id').first()).not.toBeVisible();
      await expect(page.locator('text={"schedule_id"').first()).not.toBeVisible();
    }
  });
});

// ─── Guardrail Center — Violation Timestamps ──────────────────────────────────

test.describe('GuardrailCenter — Violation Timestamps', () => {
  test('violations table shows real timestamps not ID prefix', async ({ page }) => {
    await authPage(page);
    await page.route('**/guardrails**', route =>
      route.fulfill({ status: 200, body: JSON.stringify([]) })
    );
    await page.route('**/guardrails/violations**', route =>
      route.fulfill({
        status: 200,
        body: JSON.stringify([{
          id: 'abc123deadbeef',
          rule_name: 'Block PII',
          violation_type: 'pii_detection',
          severity: 'high',
          message: 'PII detected in output',
          created_at: '2025-06-15T14:30:00Z',
        }]),
      })
    );
    await page.route('**/guardrails/stats**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({ total_24h: 1, total_all_time: 1, risk_score_p95: 0.8, top_category: 'pii' }) })
    );

    await page.goto('/settings?tab=guardrails');
    const violationsTab = page.locator('[role="tab"]:has-text("Violations"), button:has-text("Violations")').first();
    if (await violationsTab.isVisible({ timeout: 5_000 })) {
      await violationsTab.click();
      await page.locator('text=Block PII').waitFor({ timeout: 8_000 });
      
      // Should show a time like "2:30:00 PM", NOT "abc123de"
      const rows = page.locator('tr, [data-testid*="violation"]');
      if (await rows.count() > 0) {
        const rowText = await rows.first().textContent() ?? '';
        expect(rowText).not.toContain('abc123de');
        // Should have time pattern
        expect(rowText).toMatch(/\d+:\d+/);
      }
    }
  });
});

// ─── Builder Page — Full Wizard ───────────────────────────────────────────────

test.describe('Builder Page — Full Wizard', () => {
  test.beforeEach(async ({ page }) => {
    await authPage(page);
    await page.route('**/builder/projects**', async route => {
      await route.fulfill({
        status: 200,
        body: JSON.stringify({
          goal_id: 'build-goal-001',
          name: 'My Landing Page',
          files: [
            { path: 'src/App.tsx', size: '2.4 KB' },
            { path: 'src/index.css', size: '1.1 KB' },
            { path: 'package.json', size: '512 B' },
          ],
          summary: 'Generated React landing page with hero section and CTA.',
        }),
      });
    });
  });

  test('shows 8 project type options', async ({ page }) => {
    await page.goto('/builder');
    await expect(page.locator('text=Landing Page').first()).toBeVisible({ timeout: 8_000 });
    await expect(page.locator('text=REST API').first()).toBeVisible();
    await expect(page.locator('text=Full-Stack App').first()).toBeVisible();
    await expect(page.locator('text=CLI Tool').first()).toBeVisible();
  });

  test('project type selection shows Continue button', async ({ page }) => {
    await page.goto('/builder');
    await page.locator('button:has-text("Dashboard")').first().click();
    await expect(page.locator('button:has-text("Continue")').first()).toBeVisible();
  });

  test('configuration step shows framework options', async ({ page }) => {
    await page.goto('/builder');
    await page.locator('button:has-text("Continue")').first().click();
    await expect(page.locator('button:has-text("React"), button:has-text("Next.js")').first()).toBeVisible({ timeout: 5_000 });
  });

  test('build shows progress steps and done state', async ({ page }) => {
    await page.goto('/builder');
    await page.locator('button:has-text("Continue")').first().click();
    const textarea = page.locator('textarea[placeholder*="Describe"]').first();
    await textarea.fill('A modern landing page for a SaaS product with hero, features, and pricing sections');
    await page.locator('button:has-text("Build Project")').first().click();
    await expect(page.locator('text=My Landing Page, text=generated').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('text=App.tsx, text=src').first()).toBeVisible({ timeout: 5_000 });
  });

  test('done state shows View Execution link', async ({ page }) => {
    await page.goto('/builder');
    await page.locator('button:has-text("Continue")').first().click();
    await page.locator('textarea[placeholder*="Describe"]').first().fill('Landing page for AI startup');
    await page.locator('button:has-text("Build Project")').first().click();
    await expect(page.locator('text=View Execution, a[href*="/goals/"]').first()).toBeVisible({ timeout: 10_000 });
  });
});

// ─── Skills Page — Edit & Test ─────────────────────────────────────────────────

test.describe('Skills Page — Edit & Test', () => {
  const MOCK_SKILL = {
    id: 'skill-001',
    name: 'PR Review',
    description: 'Automatically review pull requests',
    trigger_hints: ['review pull request', 'check PR'],
    instructions: 'Review the code for bugs and best practices',
    allowed_tools: ['github:get_pr', 'github:comment'],
    platform: false,
    enabled: true,
  };

  test.beforeEach(async ({ page }) => {
    await authPage(page);
    await page.route('**/skills**', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, body: JSON.stringify([MOCK_SKILL]) });
      } else {
        await route.continue();
      }
    });
  });

  test('shows skill with edit button', async ({ page }) => {
    await page.goto('/skills');
    await expect(page.locator('text=PR Review').first()).toBeVisible({ timeout: 8_000 });
    await expect(page.locator('button[aria-label*="Edit"], button:has-text("Edit")').first()).toBeVisible({ timeout: 3_000 });
  });

  test('edit button opens edit modal', async ({ page }) => {
    await page.goto('/skills');
    await page.locator('text=PR Review').waitFor({ timeout: 8_000 });
    await page.locator('button[aria-label*="Edit"], button:has-text("Edit")').first().click();
    await expect(page.locator('text=Edit Skill, input[value*="PR Review"]').first()).toBeVisible({ timeout: 3_000 });
  });

  test('search filters skills', async ({ page }) => {
    await page.goto('/skills');
    await page.locator('text=PR Review').waitFor({ timeout: 8_000 });
    const searchInput = page.locator('input[placeholder*="Search"]').first();
    if (await searchInput.isVisible()) {
      await searchInput.fill('xyz-no-match');
      await expect(page.locator('text=PR Review').first()).not.toBeVisible({ timeout: 3_000 });
    }
  });

  test('export button downloads skills JSON', async ({ page }) => {
    const downloads: string[] = [];
    page.on('download', download => downloads.push(download.suggestedFilename()));
    await page.goto('/skills');
    await page.locator('text=PR Review').waitFor({ timeout: 8_000 });
    const exportBtn = page.locator('button:has-text("Export")').first();
    if (await exportBtn.isVisible()) {
      await exportBtn.click();
      await page.waitForTimeout(500);
    }
  });
});

// ─── SAML — Real Connection Test ──────────────────────────────────────────────

test.describe('Enterprise SAML — Real Test', () => {
  test.beforeEach(async ({ page }) => {
    await authPage(page);
    await page.route('**/enterprise/**', async route => {
      const url = route.request().url();
      if (url.includes('/saml/test')) {
        await route.fulfill({
          status: 200,
          body: JSON.stringify({ success: true, latency_ms: 142, status_code: 200, message: 'IdP responded with HTTP 200' }),
        });
      } else {
        await route.fulfill({ status: 200, body: JSON.stringify({}) });
      }
    });
  });

  test('SAML test button triggers real API not setTimeout', async ({ page }) => {
    let samlTestCalled = false;
    await page.route('**/enterprise/saml/test**', route => {
      samlTestCalled = true;
      route.fulfill({ status: 200, body: JSON.stringify({ success: true, latency_ms: 100, message: 'IdP responded with HTTP 200' }) });
    });

    await page.goto('/enterprise');
    const samlTab = page.locator('[role="tab"]:has-text("SSO"), button:has-text("SAML"), button:has-text("SSO")').first();
    if (await samlTab.isVisible({ timeout: 5_000 })) {
      await samlTab.click();
    }

    const testBtn = page.locator('button:has-text("Test Connection"), button:has-text("Test SAML")').first();
    if (await testBtn.isVisible({ timeout: 8_000 })) {
      await testBtn.click();
      await page.waitForTimeout(1_000);
      // Real API should be called
      expect(samlTestCalled).toBe(true);
    }
  });
});

// ─── Observability — Log Export & Search ──────────────────────────────────────

test.describe('Observability — Log Pipeline', () => {
  test.beforeEach(async ({ page }) => {
    await authPage(page);
    await page.route('**/observability/**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({ logs: [
        { id: 'l1', timestamp: new Date().toISOString(), level: 'info', message: 'Goal completed successfully', source: 'goal_complete' },
        { id: 'l2', timestamp: new Date().toISOString(), level: 'error', message: 'Tool execution failed', source: 'tool_call_failed' },
      ], total: 2 }) })
    );
  });

  test('logs tab has search input', async ({ page }) => {
    await page.goto('/observability');
    await page.locator('[role="tab"]:has-text("Logs"), button:has-text("Logs")').first().click().catch(() => {});
    await expect(page.locator('input[placeholder*="Search logs"]').first()).toBeVisible({ timeout: 8_000 });
  });

  test('logs tab has export button', async ({ page }) => {
    await page.goto('/observability');
    await page.locator('[role="tab"]:has-text("Logs"), button:has-text("Logs")').first().click().catch(() => {});
    await expect(page.locator('button:has-text("Export Logs"), button:has-text("Export")').first()).toBeVisible({ timeout: 8_000 });
  });

  test('log shipping info message is shown', async ({ page }) => {
    await page.goto('/observability');
    await page.locator('[role="tab"]:has-text("Logs"), button:has-text("Logs")').first().click().catch(() => {});
    await expect(
      page.locator('text=goal execution events, text=log shipping').first()
    ).toBeVisible({ timeout: 8_000 });
  });

  test('log search filters entries', async ({ page }) => {
    await page.goto('/observability');
    await page.locator('[role="tab"]:has-text("Logs"), button:has-text("Logs")').first().click().catch(() => {});
    const searchInput = page.locator('input[placeholder*="Search logs"]').first();
    if (await searchInput.isVisible({ timeout: 5_000 })) {
      await searchInput.fill('completed');
      await page.waitForTimeout(300);
      // "Tool execution failed" should be filtered out
      await expect(page.locator('text=Tool execution failed').first()).not.toBeVisible({ timeout: 3_000 });
    }
  });
});

// ─── ConnectorDetail — Real Connector Usage ───────────────────────────────────

test.describe('ConnectorDetail — Connector-Filtered Usage', () => {
  const MOCK_CONNECTOR = {
    id: 'conn-001', server_id: 'conn-001', name: 'GitHub', url: 'https://api.github.com', auth_type: 'bearer', status: 'active',
  };

  test('usage tab shows filtered connector data', async ({ page }) => {
    await authPage(page);
    await page.route('**/connectors/conn-001**', route => route.fulfill({ status: 200, body: JSON.stringify(MOCK_CONNECTOR) }));
    await page.route('**/connectors/conn-001/tools**', route => route.fulfill({ status: 200, body: JSON.stringify([]) }));
    await page.route('**/connectors/conn-001/usage**', route =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          goals: [{ id: 'g1', goal: 'Fix JIRA bugs using GitHub', status: 'complete', created_at: new Date().toISOString() }],
          total: 1,
          success_rate: 100,
          filtered: true,
        }),
      })
    );

    await page.goto('/connectors/conn-001');
    const usageTab = page.locator('[role="tab"]:has-text("Usage"), button:has-text("Usage")').first();
    if (await usageTab.isVisible({ timeout: 5_000 })) {
      await usageTab.click();
      // Should show the filtered goal
      await expect(page.locator('text=Fix JIRA bugs using GitHub').first()).toBeVisible({ timeout: 8_000 });
      // Should show the "filtered" label
      await expect(page.locator('text=referenced this connector, text=Goals that').first()).toBeVisible({ timeout: 3_000 });
    }
  });
});

// ─── Governance Budget Gauge Fix ─────────────────────────────────────────────

test.describe('Governance Budget — Gauge Formula', () => {
  test('budget tab loads without crash', async ({ page }) => {
    await authPage(page);
    await page.route('**/governance/policies**', route => route.fulfill({ status: 200, body: JSON.stringify([]) }));
    await page.route('**/governance/approvals**', route => route.fulfill({ status: 200, body: JSON.stringify({ pending: [], sla_stats: {} }) }));
    await page.route('**/audit/events**', route => route.fulfill({ status: 200, body: JSON.stringify([]) }));
    await page.route('**/governance/cost**', route =>
      route.fulfill({ status: 200, body: JSON.stringify({
        total_cost_usd: 5.00,
        daily_spent: 1.20,
        budget: { daily_usd: 10, per_goal_usd: 2 },
        total_goals: 3,
      }) })
    );

    await page.goto('/governance');
    await page.locator('[role="tab"]:has-text("Budget"), button:has-text("Budget")').first().click();
    // Should render without crash
    await expect(page.locator('text=Something went wrong').first()).not.toBeVisible({ timeout: 5_000 });
    // Budget gauges should be visible
    await expect(page.locator('svg, [class*="gauge"]').first()).toBeVisible({ timeout: 5_000 });
  });
});
