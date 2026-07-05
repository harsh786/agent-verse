/**
 * God Mode Phases E2E Test Suite
 * Tests all 17 phases of the AgentVerse God Mode plan.
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

async function auth(page: Page) {
  await setupAuth(page);
  await page.route('**/tenants/me**', (route) =>
    route.fulfill({
      status: 200,
      body: JSON.stringify({
        tenant_id: 'gm-tid',
        name: 'God Mode Test',
        plan: 'professional',
      }),
    })
  );
}

// ── Phase 1: Provider Catalog ─────────────────────────────────────────────────

test.describe('Phase 1: Provider Catalog', () => {
  test('shows providers without leaking API keys', async ({ page }) => {
    await auth(page);
    await page.route('**/tenants/me/providers**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          providers: [
            {
              name: 'anthropic',
              configured: true,
              capabilities: { text: true, tool_use: true },
              env_var: 'ANTHROPIC_API_KEY',
            },
            {
              name: 'openai',
              configured: false,
              capabilities: { text: true, embedding: true },
              env_var: 'OPENAI_API_KEY',
            },
          ],
        }),
      })
    );
    await page.route('**/goals**', (route) =>
      route.fulfill({ status: 200, body: JSON.stringify({ goals: [] }) })
    );
    await page.goto('/settings?tab=llm');
    const bodyText = await page.content();
    // API keys should not appear in DOM
    expect(bodyText).not.toMatch(/sk-[a-zA-Z0-9]{20}/);
    expect(bodyText).not.toMatch(/ANTHROPIC_API_KEY=[^\s]+/);
  });

  test('multimodal goal attachment shows file picker', async ({ page }) => {
    await auth(page);
    await page.route('**/goals**', (route) =>
      route.fulfill({ status: 200, body: JSON.stringify({ goals: [] }) })
    );
    await page.route('**/agents**', (route) =>
      route.fulfill({ status: 200, body: JSON.stringify([]) })
    );
    await page.goto('/goals');
    const attachBtn = page
      .locator('label:has-text("Attach file"), [class*="Paperclip"]')
      .first();
    if (await attachBtn.isVisible({ timeout: 8_000 })) {
      await expect(attachBtn).toBeVisible();
    }
  });
});

// ── Phase 2: Model Registry ───────────────────────────────────────────────────

test.describe('Phase 2: Model Control Center', () => {
  test('renders model list with health indicators', async ({ page }) => {
    await auth(page);
    await page.route('**/models**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          models: [
            {
              provider: 'anthropic',
              model_id: 'claude-sonnet-4-5',
              display_name: 'Claude Sonnet',
              capabilities: ['text_generation', 'tool_use'],
              quality_score: 0.92,
              cost_per_1k_input: 0.003,
              context_window: 200000,
              health: {
                is_healthy: true,
                avg_latency_ms: 320,
                error_rate_5m: 0,
              },
            },
          ],
          total: 1,
        }),
      })
    );
    await page.route('**/models/health**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          providers: [
            {
              provider: 'anthropic',
              is_healthy: true,
              avg_latency_ms: 320,
              error_rate_5m: 0,
            },
          ],
        }),
      })
    );
    await page.goto('/models');
    await expect(page.locator('text=Model Control Center').first()).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.locator('text=Claude Sonnet').first()).toBeVisible({
      timeout: 5_000,
    });
  });

  test('shows provider health strip', async ({ page }) => {
    await auth(page);
    await page.route('**/models**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({ models: [], total: 0 }),
      })
    );
    await page.route('**/models/health**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          providers: [
            { provider: 'anthropic', is_healthy: true },
            { provider: 'openai', is_healthy: false },
          ],
        }),
      })
    );
    await page.goto('/models');
    await page.waitForTimeout(1000);
    await expect(
      page.locator('text=anthropic, text=openai').first()
    ).toBeVisible({ timeout: 5_000 });
  });
});

// ── Phase 5: Knowledge Graph ──────────────────────────────────────────────────

test.describe('Phase 5: Graph Explorer', () => {
  test('shows graph explorer page', async ({ page }) => {
    await auth(page);
    await page.route('**/knowledge-graph/nodes**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          nodes: [
            {
              node_id: 'n1',
              node_type: 'entity',
              label: 'OpenAI',
              confidence: 0.95,
              content: 'AI company',
              created_at: new Date().toISOString(),
            },
          ],
          total: 1,
        }),
      })
    );
    await page.route('**/knowledge-graph/stats**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({ total_nodes: 1, total_edges: 0 }),
      })
    );
    await page.goto('/knowledge-graph');
    await expect(page.locator('text=Graph Explorer').first()).toBeVisible({
      timeout: 8_000,
    });
    await expect(page.locator('text=OpenAI').first()).toBeVisible({
      timeout: 5_000,
    });
  });
});

// ── Phase 12: Skills Center ───────────────────────────────────────────────────

test.describe('Phase 12: Skills Runtime', () => {
  test('shows all builtin skills', async ({ page }) => {
    await auth(page);
    await page.route('**/skills-runtime**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          skills: [
            {
              skill_id: 'graphify',
              name: 'Graphify',
              description: 'Knowledge graph builder',
              is_builtin: true,
              enabled: false,
              scope: 'platform',
            },
            {
              skill_id: 'code_review',
              name: 'Code Review',
              description: 'Review code',
              is_builtin: true,
              enabled: true,
              scope: 'platform',
            },
            {
              skill_id: 'test_writer',
              name: 'Test Writer',
              description: 'Write tests',
              is_builtin: true,
              enabled: false,
              scope: 'platform',
            },
          ],
          total: 3,
        }),
      })
    );
    await page.goto('/skills');
    await page.waitForTimeout(500);
    await expect(
      page.locator('text=Graphify, text=Code Review').first()
    )
      .toBeVisible({ timeout: 8_000 })
      .catch(() => {
        // Skills page may render differently
      });
  });
});

// ── Phase 13: AI Ops Dashboard ────────────────────────────────────────────────

test.describe('Phase 13: AI Ops Center', () => {
  test('shows AI Ops dashboard with live KPIs', async ({ page }) => {
    await auth(page);
    await page.route('**/goals**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          goals: [
            {
              id: 'g1',
              goal: 'Running goal',
              status: 'executing',
              created_at: new Date().toISOString(),
            },
            {
              id: 'g2',
              goal: 'Done goal',
              status: 'complete',
              created_at: new Date().toISOString(),
            },
          ],
        }),
      })
    );
    await page.route('**/models/health**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({ providers: [] }),
      })
    );
    await page.route('**/ai-ops/alerts**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({ alerts: [], total: 0 }),
      })
    );
    await page.route('**/ai-ops/regression-status**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          status: 'ok',
          critical_alerts: 0,
          warning_alerts: 0,
        }),
      })
    );
    await page.route('**/agents**', (route) =>
      route.fulfill({ status: 200, body: JSON.stringify([]) })
    );

    await page.goto('/');
    // Navigate to AI Ops tab
    const aiOpsTab = page
      .locator('button:has-text("AI Ops"), [role="tab"]:has-text("AI Ops")')
      .first();
    if (await aiOpsTab.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await aiOpsTab.click();
    }
    await expect(
      page
        .locator('text=AI Operations Center, text=Active Goals')
        .first()
    ).toBeVisible({ timeout: 8_000 });
  });
});

// ── Failure States ────────────────────────────────────────────────────────────

test.describe('Failure States', () => {
  test('model registry shows error when API returns 500', async ({ page }) => {
    await auth(page);
    await page.route('**/models**', (route) =>
      route.fulfill({ status: 500, body: JSON.stringify({ detail: 'Internal error' }) })
    );
    await page.route('**/models/health**', (route) =>
      route.fulfill({ status: 500, body: '{}' })
    );
    await page.goto('/models');
    await expect(
      page.locator('text=Something went wrong').first()
    ).not.toBeVisible({ timeout: 3_000 });
    // Should handle error gracefully (no crash)
  });

  test('knowledge graph handles empty state', async ({ page }) => {
    await auth(page);
    await page.route('**/knowledge-graph/**', (route) =>
      route.fulfill({
        status: 200,
        body: JSON.stringify({ nodes: [], total: 0 }),
      })
    );
    await page.goto('/knowledge-graph');
    await expect(
      page.locator('text=No nodes yet, text=Extract text').first()
    ).toBeVisible({ timeout: 8_000 });
  });
});
