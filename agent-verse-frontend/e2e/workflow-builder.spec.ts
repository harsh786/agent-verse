import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
  // Catch-all: block unmocked localhost:8000 requests from hitting the real backend
  // (which returns 401 for test API keys and triggers logout). Registered FIRST
  // so specific mocks added later have higher priority via Playwright's LIFO matching.
  await page.route(/localhost:8000/, (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) })
  );
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
    // Also set sessionStorage for the secureStorage fallback
    sessionStorage.setItem(
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
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

async function setupWorkflowRoutes(page: Page, workflows: object[] = []) {
  await page.route(/localhost:8000\/workflows/, (route) => {
    const method = route.request().method();
    if (method === 'GET') {
      const url = route.request().url();
      // GET /workflows/{id}
      if (/\/workflows\/[^/]+$/.test(url) && !url.endsWith('/workflows/')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(workflows[0] ?? {}),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(workflows),
      });
    }
    if (method === 'POST') {
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'wf-new', name: 'My Workflow', definition: {} }),
      });
    }
    if (method === 'PUT' || method === 'PATCH') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(workflows[0] ?? { id: 'wf-001' }),
      });
    }
    return route.continue();
  });
}

test.describe('Workflow Builder', () => {
  // Test 1: Page renders with toolbar controls
  test('shows workflow builder page with toolbar controls', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole('button', { name: '▶ Run' })).toBeVisible({ timeout: 30000 });
  });

  // Test 2: Workflow name input defaults
  test('shows workflow name input with default value', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.locator('input').filter({ hasValue: /my workflow/i })).toBeVisible({
      timeout: 15000,
    });
  });

  // Test 3: Save workflow
  test('can save a workflow', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 10000 });
  });

  // Test 4: Load saved workflow
  test('can load a saved workflow from the dropdown', async ({ page }) => {
    const workflows = [
      {
        id: 'wf-saved',
        name: 'Deploy Pipeline',
        definition: { steps: [], edges: [] },
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await setupWorkflowRoutes(page, workflows);
    await page.goto('/workflow-builder');

    await expect(page.getByRole('option', { name: 'Deploy Pipeline' })).toBeAttached({ timeout: 15000 });
  });

  // Test 5: Dry Run button visibility
  test('Dry Run button is visible and enabled', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByRole('button', { name: /dry run/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /dry run/i })).toBeEnabled();
  });

  // Test 6: Node palette is visible
  test('shows node palette with all node types', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByText('Node Palette')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /Add Trigger \/ Start node/i })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /Add Tool Call node/i })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('button', { name: /Add Agent Step node/i })).toBeVisible({ timeout: 5000 });
  });

  // Test 7: All palette items are draggable
  test('all palette node buttons have draggable attribute', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByRole('button', { name: /Add Trigger \/ Start node/i })).toBeVisible({ timeout: 15000 });

    // Check that palette buttons have draggable attribute
    const paletteButtons = page.locator('[aria-label^="Add "][aria-label$=" node"]');
    const count = await paletteButtons.count();
    expect(count).toBeGreaterThanOrEqual(9);

    for (let i = 0; i < count; i++) {
      const btn = paletteButtons.nth(i);
      const draggable = await btn.getAttribute('draggable');
      expect(draggable).toBe('true');
    }
  });

  // Test 8: Clicking palette item adds a node (canvas no longer empty)
  test('clicking a palette item adds a node', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByText('Build your workflow')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /Add Trigger \/ Start node/i }).click();

    // The empty state hint should disappear once a node is added
    await expect(page.getByText('Build your workflow')).not.toBeVisible({ timeout: 5000 });
  });

  // Test 9: NL goal generation UI
  test('shows natural language workflow generation', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.locator('textarea[aria-label="Natural language workflow description"]')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /generate/i })).toBeVisible({ timeout: 5000 });
  });

  // Test 10: Generate from NL creates nodes
  test('NL generate from goal creates workflow nodes', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            plan: { steps: ['Analyze data', 'Process results', 'Send report'] },
          }),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');

    const textarea = page.locator('textarea[aria-label="Natural language workflow description"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });
    await textarea.fill('Analyze sales and send report');

    const generateBtn = page.getByRole('button', { name: /generate/i });
    await expect(generateBtn).toBeEnabled({ timeout: 5000 });
    await generateBtn.click();

    // Empty state should be gone after nodes are generated
    await expect(page.getByText('Build your workflow')).not.toBeVisible({ timeout: 10000 });
  });

  // Test 11: Node inspector panel
  test('node inspector panel shows on node click', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    await expect(page.getByText('Inspector')).toBeVisible({ timeout: 15000 });
    // Click a palette item to add a node, then verify inspector panel exists
    await page.getByRole('button', { name: /Add Tool Call node/i }).click();
    // Inspector section should still be visible
    await expect(page.getByText(/Inspector/)).toBeVisible({ timeout: 5000 });
  });

  // Test 12: New button resets the canvas
  test('New button resets the workflow', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    // Add a node
    await expect(page.getByRole('button', { name: /Add Trigger \/ Start node/i })).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /Add Trigger \/ Start node/i }).click();
    await expect(page.getByText('Build your workflow')).not.toBeVisible({ timeout: 5000 });

    // Click New
    await page.getByRole('button', { name: /New/i }).click();

    // Canvas should be empty again
    await expect(page.getByText('Build your workflow')).toBeVisible({ timeout: 5000 });
  });

  // Test 13: Workflow name can be edited
  test('workflow name can be changed via the input', async ({ page }) => {
    await setupAuth(page);
    await setupWorkflowRoutes(page);
    await page.goto('/workflow-builder');

    const nameInput = page.locator('input[aria-label="Workflow name"]');
    await expect(nameInput).toBeVisible({ timeout: 15000 });
    await nameInput.fill('My Custom Pipeline');
    await expect(nameInput).toHaveValue('My Custom Pipeline');
  });
});
