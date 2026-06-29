import { test, expect, type Page } from '@playwright/test';

// ── Shared helpers ─────────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      'av-auth',
      JSON.stringify({
        state: {
          apiKey: 'test-key',
          tenantId: 'test-tenant',
          plan: 'free',
          isAuthenticated: true,
        },
        version: 0,
      })
    );
    localStorage.setItem('av_api_key', 'test-key');
  });

  // RequireAuth session validation
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

// ── Governance — Page structure ───────────────────────────────────────────────

test.describe('Governance — page structure', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);

    // Default mocks for all 4 tab endpoints
    await page.route('**/governance/policies', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });

    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });

    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
        });
      }
      return route.continue();
    });

    await page.route(/localhost:8000\/governance\/audit/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );

    await page.goto('/governance');
  });

  test('renders Governance h1 heading and subtitle', async ({ page }) => {
    await expect(page.locator('h1').filter({ hasText: /governance/i })).toBeVisible({
      timeout: 15000,
    });
    await expect(
      page.getByText('Policies, approvals, audit log, and budget controls')
    ).toBeVisible();
  });

  test('shows exactly 4 tabs: policies, approvals, audit, budget', async ({ page }) => {
    for (const tab of ['policies', 'approvals', 'audit', 'budget']) {
      await expect(page.getByRole('button', { name: tab, exact: true })).toBeVisible({
        timeout: 15000,
      });
    }
  });

  test('policies tab is shown by default (empty state message)', async ({ page }) => {
    await expect(page.getByText('No policies defined.')).toBeVisible({ timeout: 15000 });
  });
});

// ── Governance — Policies tab ─────────────────────────────────────────────────

test.describe('Governance — Policies tab', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);

    // Approvals + budget needed for other tabs; policies controlled per-test
    await page.route('**/governance/approvals', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
        });
      }
      return route.continue();
    });
  });

  test('"+ New Policy" button toggles the policy creation form', async ({ page }) => {
    await page.route('**/governance/policies', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/governance');

    // Form is hidden initially
    await expect(page.getByText('New Policy')).not.toBeVisible({ timeout: 10000 });

    await page.getByRole('button', { name: '+ New Policy' }).click();

    // Form fields appear
    await expect(page.getByText('New Policy')).toBeVisible();
    await expect(page.locator('input[placeholder="block-shell-commands"]')).toBeVisible();
    await expect(page.locator('input[placeholder="shell:*"]')).toBeVisible();
    await expect(page.getByRole('combobox').filter({ hasText: /deny/i })).toBeVisible();
  });

  test('"Cancel" on the form hides it again', async ({ page }) => {
    await page.route('**/governance/policies', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/governance');

    await page.getByRole('button', { name: '+ New Policy' }).click();
    await expect(page.getByText('New Policy')).toBeVisible({ timeout: 10000 });

    // The toggle button now reads "Cancel"
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('button', { name: '+ New Policy' })).toBeVisible();
    await expect(page.locator('input[placeholder="block-shell-commands"]')).not.toBeVisible();
  });

  test('can submit a new policy; policy appears in the list', async ({ page }) => {
    const createdPolicy = {
      policy_id: 'pol-001',
      name: 'block-shell',
      tools_pattern: 'shell:*',
      action: 'deny',
    };

    // LIFO: this handler registered later will take precedence over any default
    let policyList: typeof createdPolicy[] = [];
    await page.route('**/governance/policies', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(policyList),
        });
      }
      if (method === 'POST') {
        policyList = [createdPolicy];
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(createdPolicy),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');

    // Open form, fill, save
    await page.getByRole('button', { name: '+ New Policy' }).click();
    await page.locator('input[placeholder="block-shell-commands"]').fill('block-shell');
    await page.locator('input[placeholder="shell:*"]').fill('shell:*');
    await page.getByRole('button', { name: 'Save Policy' }).click();

    // Policy name and pattern appear in the table
    await expect(page.getByText('block-shell')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('shell:*')).toBeVisible();
  });

  test('"Save Policy" button is disabled while form fields are empty', async ({ page }) => {
    await page.route('**/governance/policies', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/governance');

    await page.getByRole('button', { name: '+ New Policy' }).click();
    await expect(page.getByRole('button', { name: 'Save Policy' })).toBeDisabled({
      timeout: 10000,
    });
  });

  test('existing policies render with Name, Tools Pattern, Action, and Delete button', async ({
    page,
  }) => {
    const policies = [
      {
        policy_id: 'pol-x',
        name: 'no-exec',
        tools_pattern: 'exec:*',
        action: 'require_approval',
      },
    ];

    await page.route('**/governance/policies', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(policies),
      })
    );
    await page.goto('/governance');

    await expect(page.getByText('no-exec')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('exec:*')).toBeVisible();
    // "require_approval" → "require approval" (underscore replaced in badge)
    await expect(page.getByText('require approval')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete' })).toBeVisible();
  });
});

// ── Governance — Approvals tab ────────────────────────────────────────────────

test.describe('Governance — Approvals tab', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/policies', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
        });
      }
      return route.continue();
    });
  });

  test('shows empty state when there are no pending approvals', async ({ page }) => {
    await page.route('**/governance/approvals', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/governance');

    await page.getByRole('button', { name: 'approvals', exact: true }).click();
    await expect(page.getByText(/no pending approvals/i)).toBeVisible({ timeout: 15000 });
  });

  test('shows Approve and Reject buttons for a pending approval', async ({ page }) => {
    const approvals = [
      {
        request_id: 'req-001',
        goal_id: 'goal-001',
        tool_name: 'github:push_to_main',
        reason: 'Pushing directly to main branch',
        created_at: new Date().toISOString(),
      },
    ];

    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(approvals),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'approvals', exact: true }).click();

    await expect(page.getByText('github:push_to_main')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Pushing directly to main branch')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Approve' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Reject' })).toBeVisible();
  });

  test('approval card shows goal ID and tool name', async ({ page }) => {
    const approvals = [
      {
        request_id: 'req-002',
        goal_id: 'goal-xyz-456',
        tool_name: 'k8s:scale_deployment',
        created_at: new Date().toISOString(),
      },
    ];

    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(approvals),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'approvals', exact: true }).click();

    await expect(page.getByText('k8s:scale_deployment')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/goal-xyz-456/)).toBeVisible();
  });
});

// ── Governance — Budget tab ───────────────────────────────────────────────────

test.describe('Governance — Budget tab', () => {
  test.beforeEach(async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/policies', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/governance/approvals', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
  });

  test('Budget tab shows "Budget Limits" with current values from API', async ({ page }) => {
    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 2.5, per_tenant_daily_usd: 75.0 }),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'budget', exact: true }).click();

    await expect(page.getByText('Budget Limits')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Per Goal Limit')).toBeVisible();
    await expect(page.getByText('Daily Tenant Limit')).toBeVisible();
    await expect(page.getByText('$2.50')).toBeVisible();
    await expect(page.getByText('$75.00')).toBeVisible();
  });

  test('"Edit" button reveals Per Goal and Per Tenant Daily input fields', async ({ page }) => {
    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'budget', exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();

    await expect(page.getByText('Per Goal (USD)')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Per Tenant Daily (USD)')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save Budget' })).toBeVisible();
  });

  test('"Cancel" in edit mode hides the form and shows the read-only view again', async ({
    page,
  }) => {
    await page.route('**/governance/budget', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 }),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'budget', exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByRole('button', { name: 'Save Budget' })).toBeVisible({ timeout: 10000 });

    // The toggle button now reads "Cancel" in edit mode
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('button', { name: 'Save Budget' })).not.toBeVisible();
    await expect(page.getByText('$1.00')).toBeVisible();
  });

  test('saving updated budget values displays the new figures', async ({ page }) => {
    let storedBudget = { per_goal_usd: 1.0, per_tenant_daily_usd: 50.0 };

    await page.route('**/governance/budget', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(storedBudget),
        });
      }
      if (method === 'PUT') {
        const body = JSON.parse(route.request().postData() ?? '{}');
        storedBudget = body;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(storedBudget),
        });
      }
      return route.continue();
    });

    await page.goto('/governance');
    await page.getByRole('button', { name: 'budget', exact: true }).click();
    await page.getByRole('button', { name: 'Edit' }).click();

    // Find the two number inputs in order (Per Goal first, Per Tenant Daily second)
    const inputs = page.locator('input[type="number"]');
    await inputs.first().fill('5');
    await inputs.nth(1).fill('200');

    await page.getByRole('button', { name: 'Save Budget' }).click();

    // After successful save, form closes and updated values show in read-only view
    await expect(page.getByText('$5.00')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('$200.00')).toBeVisible();
  });
});
